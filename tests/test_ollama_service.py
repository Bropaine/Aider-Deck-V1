import io
import json
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event
from unittest.mock import Mock

import pytest

from deck import ollama_service as api


def reply(data):
    return io.BytesIO(json.dumps(data).encode())


def test_context_window_uses_only_the_matching_loaded_runner(monkeypatch):
    service = api.OllamaService()
    get = Mock(return_value={"models": [
        {"name": "demo:latest", "context_length": 4096},
        {"name": "other:latest", "context_length": 8192},
        {"name": "unknown:latest"},
        {"name": "remote:cloud", "context_length": 32000},
        {"name": "duplicate:latest", "context_length": 4096},
        {"name": "duplicate:latest", "context_length": 2048},
        {"name": "bad:latest", "context_length": True},
    ]})
    monkeypatch.setattr(service, "_get", get)
    assert service.context_window("registry.ollama.ai/library/demo") == 4096
    assert service.context_window("other") == 8192
    for name in ("unknown", "missing", "remote:cloud", "duplicate", "bad"):
        assert service.context_window(name) is None
    get.assert_called_once_with("ps", timeout=0.5)
    service._window_expiry = 0
    get.side_effect = api.Offline("offline")
    assert service.context_window("demo") is None


def test_cached_model_context_metadata_reads_only_the_selected_tag_without_warming_it(server):
    service, wire = server
    wire.info = {"model_info": {"general.architecture": "llama", "llama.context_length": 32768,
                                "vision.context_length": 512}}
    assert service.model_context_limit("demo") == {"model_capacity": 32768}
    assert service.model_context_limit("demo:latest") == {"model_capacity": 32768}
    wire.info["parameters"] = 'stop "num_ctx 9999"\n num_ctx    8192\r\n'
    assert service.model_context_limit("another") == {"model_default": 8192}
    assert wire.calls == [("show", {"model": "demo:latest"}), ("show", {"model": "another:latest"})]
    wire.info = {"model_info": {"vision.context_length": 256, "general.architecture": "text"},
                 "parameters": "num_ctx 0\nnum_ctx -1"}
    assert service.model_context_limit("missing") == {}
    wire.info = {"remote_model": "cloud", "parameters": "num_ctx 2048"}
    assert service.model_context_limit("proxy") == {}
    assert service.model_context_limit("demo:cloud") == {}
    wire.running = False
    assert service.model_context_limit("offline") == {}
    assert service.model_context_limit("offline") == {}
    assert wire.calls.count(("show", {"model": "offline:latest"})) == 1


class Loopback:
    def __init__(self):
        self.running = True
        self.installed, self.loaded = [], []
        self.calls = []
        self.stream = None
        self.info = {"capabilities": ["completion"]}
        self.keep_loaded = True

    @contextmanager
    def request(self, path, body=None, timeout=3):
        self.calls.append((path, body))
        if not self.running:
            raise api.Offline("Stopped")
        if path == "tags":
            yield reply({"models": self.installed})
        elif path == "ps":
            yield reply({"models": self.loaded})
        elif path == "pull":
            yield self.stream()
        elif path == "show":
            yield reply(self.info)
        elif path == "generate":
            if self.keep_loaded:
                self.loaded = [{"name": body["model"], "size": 42, "size_vram": 21}]
            yield reply({"model": body["model"], "done": True})
        else:
            raise AssertionError(path)


@pytest.fixture
def server(monkeypatch, tmp_path):
    service, wire = api.OllamaService("http://127.0.0.1:39876"), Loopback()
    monkeypatch.setattr(service, "_request", wire.request)
    monkeypatch.setattr(api, "_cli", lambda: "ollama.exe")
    monkeypatch.setattr(api.tempfile, "gettempdir", lambda: str(tmp_path))
    return service, wire


@pytest.mark.parametrize(
    "bad",
    [
        "http://203.0.113.1:11434",
        "https://localhost:11434",
        "http://untrusted.invalid:8080",
        "http://user:pw@localhost:11434",
        "localhost:11434/r",
        "http://localhost?to=remote",
        "http://localhost:65536",
        "http://localhost:0",
    ],
)
def test_only_local_api_origins(bad):
    with pytest.raises(ValueError):
        api.local_endpoint(bad)


def test_endpoint_and_tags():
    assert api.local_endpoint("localhost:11333") == "http://127.0.0.1:11333"
    assert api.local_endpoint("http://[::1]:11333/") == "http://[::1]:11333"
    assert api.local_endpoint("0.0.0.0:12345") == "http://127.0.0.1:12345"
    assert (
        api.validate_tag(" ollama_chat/org/my-model:3b-q8_0 ") == "org/my-model:3b-q8_0"
    )
    assert api.validate_tag("ollama/smollm2") == "smollm2:latest"
    for value in (
        None,
        "",
        "a b",
        "x../..",
        "a\n/model b",
        "https://bad/x",
        "/bad",
        "--help",
    ):
        with pytest.raises(ValueError):
            api.validate_tag(value)


def test_start_reuses_owner_or_one_new_hidden_process(server, monkeypatch):
    service, wire = server
    wire.installed = [{"name": "test:1b", "size": 200}]
    process = Mock()
    process.poll.return_value = None

    def launch(*args, **kwargs):
        wire.running = True
        return process

    spawn = Mock(side_effect=launch)
    monkeypatch.setattr(api.subprocess, "Popen", spawn)
    assert service.start()["installed"][0]["name"] == "test:1b"
    spawn.assert_not_called()
    wire.running = False
    offline = service.status()
    assert not offline["running"] and offline["cli_available"] and offline["error"]
    spawn.assert_not_called()
    with ThreadPoolExecutor(4) as pool:
        assert all(
            result["running"]
            for result in pool.map(lambda _: service.start(), range(4))
        )
    assert spawn.call_count == 1
    args, kwargs = spawn.call_args
    assert (
        args[0] == ["ollama.exe", "serve"]
        and kwargs["env"]["OLLAMA_HOST"] == service.endpoint
    )
    assert (
        kwargs["stdin"]
        == kwargs["stdout"]
        == kwargs["stderr"]
        == api.subprocess.DEVNULL
    )
    if os.name == "nt":
        assert kwargs["creationflags"] & api.subprocess.CREATE_NO_WINDOW
    process.terminate.assert_not_called()


def test_start_failure_explained_and_status_is_read_only(server, monkeypatch):
    service, wire = server
    wire.running = False
    monkeypatch.setattr(api, "_cli", lambda: None)
    assert not service.status()["cli_available"]
    with pytest.raises(api.OllamaError, match="Install Ollama"):
        service.start()
    failed = service.pull("test:1b")
    assert "Install Ollama" in service.wait(failed["id"], 2)["error"]


def test_pull_progress_idempotency_failure_retry_and_independent_wait(server):
    service, wire = server
    tag = "org/qwen:4b-q8_0"
    waiting, release = Event(), Event()

    def interrupted():
        yield b'{"status":"pulling a","digest":"a","completed":100,"total":100}\n'
        yield b'{"status":"pulling b","digest":"b","completed":40,"total":80}\n'
        waiting.set()
        assert release.wait(3)
        yield b'{"error":"digest mismatch; retry download"}\n'

    wire.stream = interrupted
    first = service.pull(tag)
    try:
        assert waiting.wait(3)
        assert service.pull(tag)["id"] == first["id"]
        with pytest.raises(ValueError, match="already working"):
            service.load(tag)
        with pytest.raises(TimeoutError):
            service.wait(first["id"], 0)
        seen = service.status()["job"]
        assert (seen["completed"], seen["total"], seen["state"]) == (
            140,
            180,
            "running",
        )
    finally:
        release.set()
    assert "digest mismatch" in service.wait(first["id"], 3)["error"]

    def successful():
        wire.installed.append({"name": tag})
        yield b'{"status":"success"}\n'

    wire.stream = successful
    second = service.pull(tag)
    assert second["id"] != first["id"]
    assert service.wait(second["id"], 3)["state"] == "succeeded"
    assert service.wait(first["id"], 0)["state"] == "failed"
    assert [body for path, body in wire.calls if path == "pull"] == [
        {"model": tag, "stream": True}
    ] * 2


def test_completion_requires_success_and_exact_manifest(server):
    service, wire = server
    wire.stream = lambda: iter([b'{"status":"verifying sha256 digest"}\n'])
    assert (
        "before success" in service.wait(service.pull("fixture:2b")["id"], 3)["error"]
    )
    wire.installed = [{"name": "fixture:1b"}]
    wire.stream = lambda: iter([b'{"status":"success"}\n'])
    assert "requested tag" in service.wait(service.pull("fixture:2b")["id"], 3)["error"]
    assert service.wait(service.pull("registry.ollama.ai/library/fixture:1b")["id"], 3)["state"] == "succeeded"
    with pytest.raises(ValueError, match="Unknown"):
        service.wait("missing", 0)


def test_loading_uses_empty_generate_and_verifies_residency(server):
    service, wire = server
    wire.installed = [{"name": "local:1b", "size": 42}]
    result = service.wait(service.load("local:1b")["id"], 3)
    assert result["state"] == "succeeded" and result["error"] is None
    assert ("show", {"model": "local:1b"}) in wire.calls
    assert (
        "generate",
        {"model": "local:1b", "stream": False, "keep_alive": "5m"},
    ) in wire.calls
    assert service.status()["loaded"][0]["name"] == "local:1b"
    wire.keep_loaded, wire.loaded = False, []
    assert "in memory" in service.wait(service.load("local:1b")["id"], 3)["error"]


def test_load_rejects_uninstalled_cloud_and_non_generative_models(server):
    service, wire = server
    wire.installed = [
        {"name": "cloud-model:latest", "remote_host": "https://ollama.com"},
        {"name": "legacy:cloud"},
        {"name": "embeddings:1b", "capabilities": ["embedding"]},
    ]
    for name in ("missing:1b", "cloud-model", "legacy:cloud", "embeddings:1b"):
        assert service.wait(service.load(name)["id"], 3)["state"] == "failed"
    assert not any(path in ("generate", "pull") for path, _ in wire.calls)


def test_http_errors_are_safe_and_redirects_are_not_followed(monkeypatch):
    service = api.OllamaService()
    conn = Mock()
    response = conn.getresponse.return_value
    response.status = 307
    response.read.return_value = (
        b'{"error":"see https://blob.test/key?signature=secret"}'
    )
    dial = Mock(return_value=conn)
    monkeypatch.setattr(api.http.client, "HTTPConnection", dial)
    with pytest.raises(api.OllamaError, match="registry URL") as raised:
        service._get("tags")
    assert "secret" not in str(raised.value)
    dial.assert_called_once()
    conn.close.assert_called_once()
