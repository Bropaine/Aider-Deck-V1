"""Manage the local Ollama daemon and its model library, independently of Aider."""

from collections import deque
from contextlib import contextmanager
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlsplit
import uuid


class OllamaError(ValueError):
    pass


class Offline(OllamaError):
    pass


def local_endpoint(value):
    value = value or "http://127.0.0.1:11434"
    if not isinstance(value, str):
        raise ValueError("Choose a loopback Ollama HTTP endpoint.")
    if "://" not in value:
        value = "http://" + value
    try:
        url = urlsplit(value)
        host = url.hostname
        port = url.port if url.port is not None else 11434
        # OLLAMA_HOST can specify a bind address. Connect to loopback only.
        host = {"localhost": "127.0.0.1", "0.0.0.0": "127.0.0.1", "::": "::1"}.get(
            host, host
        )
        address = ipaddress.ip_address(host)
    except ValueError:
        raise ValueError("Choose a loopback Ollama HTTP endpoint.") from None
    if (
        url.scheme != "http"
        or not address.is_loopback
        or not port
        or url.username is not None
        or url.password is not None
        or url.path not in ("", "/")
        or url.query
        or url.fragment
    ):
        raise ValueError(
            "Choose a loopback Ollama HTTP endpoint without credentials, paths or queries."
        )
    server = f"[{address}]" if address.version == 6 else str(address)
    return f"http://{server}:{port}"


def validate_tag(model, *, strip_provider=True):
    part = r"[a-zA-Z0-9_][a-zA-Z0-9_.-]*"
    if isinstance(model, str):
        model = model.strip()
        if strip_provider:
            for prefix in ("ollama_chat/", "ollama/"):
                if model.startswith(prefix):
                    model = model[len(prefix) :]
                    break
    if not isinstance(model, str) or not re.fullmatch(
        rf"(?:{part}/){{0,2}}{part}(?::{part})?", model
    ):
        raise ValueError("Enter an Ollama model name and tag, such as qwen3:8b.")
    return model if ":" in model else model + ":latest"


def _safe_message(message):
    # Registry failures can include signed URLs or authentication query strings.
    text = re.sub(r"https?://[^\s<>\"']+", "[registry URL]", str(message))
    return " ".join(text.split())[:600]


def _cli():
    name = "ollama.exe" if os.name == "nt" else "ollama"
    found = shutil.which(name)
    if found:
        return found
    if os.name == "nt" and os.getenv("LOCALAPPDATA"):
        path = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Ollama" / name
        if path.is_file():
            return str(path)
    return None


@contextmanager
def _start_guard(endpoint):
    # Separate Deck processes can manage the same endpoint. The OS releases
    # the lock if a process crashes, so a later click can retry immediately.
    name = hashlib.sha256(endpoint.encode()).hexdigest()[:16]
    with (Path(tempfile.gettempdir()) / f"aider-deck-ollama-{name}.lock").open(
        "a+b"
    ) as lock:
        lock.seek(0)
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
            except OSError:
                raise OllamaError(
                    "Another Deck process is starting Ollama. Refresh its status."
                ) from None
        else:
            import fcntl

            fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _json(data):
    try:
        obj = json.loads(data)
    except (UnicodeError, ValueError):
        raise OllamaError("Ollama returned an unreadable JSON response.") from None
    if not isinstance(obj, dict):
        raise OllamaError("Ollama returned an unexpected response.")
    if obj.get("error"):
        raise OllamaError(_safe_message(obj["error"]))
    return obj


class OllamaService:
    def __init__(self, endpoint=None, *, keep_alive="5m"):
        self.endpoint = local_endpoint(endpoint or os.getenv("OLLAMA_HOST"))
        self.keep_alive = keep_alive
        self._address = urlsplit(self.endpoint)
        self._starts = threading.Lock()
        self._context_guard = threading.Lock()
        self._window_expiry = 0
        self._running_rows = []
        self._model_limits = {}
        self._process = None
        self._changed = threading.Condition()
        self._jobs = deque(maxlen=64)

    @contextmanager
    def _request(self, path, body=None, timeout=3):
        # HTTPConnection connects directly, bypassing HTTP proxy settings and
        # refusing redirects that could leave the selected loopback endpoint.
        conn = http.client.HTTPConnection(
            self._address.hostname, self._address.port, timeout=timeout
        )
        try:
            conn.request(
                "GET" if body is None else "POST",
                "/api/" + path,
                body=json.dumps(body).encode() if body is not None else None,
                headers={"Content-Type": "application/json"}
                if body is not None
                else {},
            )
            response = conn.getresponse()
            if not 200 <= response.status < 300:
                raw = response.read(4096)
                try:
                    error = json.loads(raw).get("error")
                except (ValueError, AttributeError, UnicodeError):
                    error = None
                raise OllamaError(
                    _safe_message(
                        error
                        or f"Ollama returned HTTP {response.status} for /api/{path}."
                    )
                )
            yield response
        except ConnectionRefusedError:
            raise Offline(f"Ollama is not running at {self.endpoint}.") from None
        except (OSError, http.client.HTTPException) as exc:
            raise OllamaError(
                f"Could not complete /api/{path} at {self.endpoint}: {_safe_message(exc)}"
            ) from None
        finally:
            conn.close()

    def _get(self, path, body=None, timeout=3):
        with self._request(path, body, timeout) as response:
            return _json(response.read())

    def model_details(self, tag, *, timeout=3):
        """Read installed model metadata without loading the runner."""
        return self._get("show", {"model": validate_tag(tag, strip_provider=False)}, timeout=timeout)

    def _models(self, path, *, timeout=3):
        rows = self._get(path, timeout=timeout).get("models")
        if not isinstance(rows, list):
            raise OllamaError(f"Ollama did not return a model list from /api/{path}.")
        models = []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("name"), str):
                raise OllamaError(
                    f"Ollama returned a malformed model entry from /api/{path}."
                )
            name = row["name"]
            remote = bool(
                row.get("remote_model")
                or row.get("remote_host")
                or name.endswith((":cloud", "-cloud"))
                or row.get("runner") == "cloud"
            )
            models.append(
                {
                    "name": name,
                    "size": row.get("size"),
                    "remote": remote,
                    "capabilities": row.get("capabilities") or [],
                    **{k: row[k] for k in ("size_vram", "expires_at", "context_length") if k in row},
                }
            )
        return models

    def context_window(self, tag):
        """Read the window allocated to this runner without starting or warming it."""
        with self._context_guard:
            if time.monotonic() >= self._window_expiry:
                try:
                    self._running_rows = self._models("ps", timeout=0.5)
                except OllamaError:
                    self._running_rows = []
                self._window_expiry = time.monotonic() + 3
            windows = [
                row.get("context_length") for row in self._running_rows
                if not row["remote"] and _same_tag(row["name"], tag)
            ]
        if windows and all(type(n) is int and n > 0 and n == windows[0] for n in windows):
            return windows[0]
        return None

    def model_context_limit(self, tag):
        """Read an installed tag's default or supported capacity without loading it."""
        tag = validate_tag(tag, strip_provider=False)
        if tag.lower().endswith((":cloud", "-cloud")):
            return {}
        with self._context_guard:
            cached = self._model_limits.get(tag)
            if cached and time.monotonic() < cached[0]:
                return cached[1]
            try:
                details = self.model_details(tag, timeout=0.75)
            except OllamaError:
                details = {}
            limit = _model_context_limit(details)
            if len(self._model_limits) >= 64 and tag not in self._model_limits:
                oldest = min(self._model_limits, key=lambda name: self._model_limits[name][0])
                del self._model_limits[oldest]
            self._model_limits[tag] = (time.monotonic() + (60 if limit else 10), limit)
            return limit

    def installed_model(self, tag):
        """Return the installed tag as reported by this daemon without loading it."""
        return next((row for row in self._models("tags", timeout=2) if _same_tag(row["name"], tag)), None)

    def status(self):
        result = dict(
            endpoint=self.endpoint,
            running=False,
            cli_available=bool(_cli()),
            installed=[],
            loaded=[],
            error=None,
        )
        try:
            result["installed"] = self._models("tags")
            result["running"] = True
            result["loaded"] = self._models("ps")
        except OllamaError as exc:
            result["error"] = str(exc)
        return {**result, "job": self.job()}

    def _ensure_running(self):
        try:
            self._models("tags")
            return
        except Offline:
            pass
        with self._starts, _start_guard(self.endpoint):
            try:
                self._models("tags")
                return
            except Offline:
                pass
            if self._process is None or self._process.poll() is not None:
                binary = _cli()
                if not binary:
                    raise OllamaError(
                        "Install Ollama for Windows, then start the service."
                    )
                self._process = subprocess.Popen(
                    [binary, "serve"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    cwd=Path.home(),
                    env={**os.environ, "OLLAMA_HOST": self.endpoint},
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                try:
                    self._models("tags")
                    return
                except Offline:
                    if self._process.poll() is not None:
                        raise OllamaError(
                            f"Ollama exited with code {self._process.returncode} during startup. Check its server log."
                        ) from None
                    time.sleep(0.15)
            raise OllamaError(
                "Ollama has not finished starting. Refresh its status and retry."
            )

    def start(self):
        self._ensure_running()
        return self.status()

    def job(self):
        with self._changed:
            return dict(self._jobs[-1]) if self._jobs else None

    def wait(self, job_id, timeout):
        with self._changed:
            job = next((j for j in self._jobs if j["id"] == job_id), None)
            if job is None:
                raise ValueError("Unknown Ollama operation.")
            if not self._changed.wait_for(lambda: job["state"] != "running", timeout):
                raise TimeoutError("Ollama is still working.")
            return dict(job)

    def pull(self, model, *, strip_provider=True):
        return self._submit("pull", model, strip_provider=strip_provider)

    def load(self, model):
        return self._submit("load", model)

    def _submit(self, action, model, *, strip_provider=True):
        model = validate_tag(model, strip_provider=strip_provider)
        with self._changed:
            latest = self._jobs[-1] if self._jobs else None
            if latest and latest["state"] == "running":
                if (latest["action"], latest["model"]) == (action, model):
                    return dict(latest)
                raise ValueError(f"Ollama is already working on {latest['model']}.")
            job = dict(
                id=uuid.uuid4().hex,
                action=action,
                model=model,
                state="running",
                message="Contacting Ollama",
                completed=0,
                total=0,
                error=None,
            )
            self._jobs.append(job)
            try:
                threading.Thread(
                    target=self._work, args=(job,), daemon=True, name=f"ollama-{action}"
                ).start()
            except RuntimeError:
                self._jobs.pop()
                raise
            return dict(job)

    def _update(self, job, **values):
        with self._changed:
            job.update(values)
            self._changed.notify_all()

    def _work(self, job):
        try:
            self._ensure_running()
            if job["action"] == "pull":
                self._pull(job)
            else:
                self._load(job)
        except Exception as exc:
            error = _safe_message(exc) or "Ollama did not complete the operation."
            self._update(job, state="failed", message=error, error=error)
        else:
            self._update(
                job,
                state="succeeded",
                message="Installed" if job["action"] == "pull" else "Loaded",
            )

    def _pull(self, job):
        model = job["model"]
        layers = {}
        success = False
        with self._request(
            "pull", {"model": model, "stream": True}, timeout=300
        ) as response:
            for line in response:
                if not line.strip():
                    continue
                event = _json(line)
                message = event.get("status")
                if not isinstance(message, str):
                    raise OllamaError("Ollama returned a pull event without a status.")
                if event.get("digest") and "total" in event:
                    done, size = event.get("completed", 0), event["total"]
                    if (
                        not isinstance(done, int)
                        or not isinstance(size, int)
                        or not 0 <= done <= size
                    ):
                        raise OllamaError("Ollama returned invalid layer progress.")
                    layers[event["digest"]] = (done, size)
                self._update(
                    job,
                    message=_safe_message(message),
                    completed=sum(x[0] for x in layers.values()),
                    total=sum(x[1] for x in layers.values()),
                )
                if message == "success":
                    success = True
                    break
        if not success:
            raise OllamaError(
                "Ollama's pull stream ended before success. Retry to resume the download."
            )
        if not any(_same_tag(row["name"], model) for row in self._models("tags")):
            raise OllamaError(
                "The pull finished but Ollama did not list the requested tag. Refresh and retry."
            )

    def _load(self, job):
        model = job["model"]
        row = next(
            (r for r in self._models("tags") if _same_tag(r["name"], model)), None
        )
        if row is None:
            raise OllamaError(f"Install {model} in Ollama before loading it.")
        if row["remote"]:
            raise OllamaError(
                "Choose a local model. This tag uses an Ollama cloud runner."
            )
        caps = row["capabilities"]
        if not caps:
            info = self.model_details(model, timeout=15)
            if info.get("remote_model") or info.get("remote_host"):
                raise OllamaError(
                    "Choose a local model. This tag uses an Ollama cloud runner."
                )
            caps = info.get("capabilities") or []
        if "completion" not in caps:
            raise OllamaError(
                f"Ollama does not list text generation as a capability of {model}."
            )
        self._update(job, message="Loading model into memory")
        reply = self._get(
            "generate",
            {"model": model, "stream": False, "keep_alive": self.keep_alive},
            timeout=600,
        )
        if reply.get("done") is not True:
            raise OllamaError("Ollama did not confirm the model finished loading.")
        if not any(
            _same_tag(r["name"], model) and not r["remote"] for r in self._models("ps")
        ):
            raise OllamaError(
                "Ollama returned from loading but did not list the model in memory."
            )


def _model_context_limit(details):
    if details.get("remote_model") or details.get("remote_host"):
        return {}
    parameters = details.get("parameters")
    if isinstance(parameters, str):
        match = re.search(r"(?m)^[ \t]*num_ctx[ \t]+([0-9]{1,10})[ \t]*\r?$", parameters)
        if match and int(match[1]) > 0:
            return {"model_default": int(match[1])}
    info = details.get("model_info")
    if isinstance(info, dict) and isinstance(info.get("general.architecture"), str):
        limit = info.get(info["general.architecture"] + ".context_length")
        if type(limit) is int and limit > 0:
            return {"model_capacity": limit}
    return {}


def _same_tag(reported, requested):
    # Ollama shortens names from its default registry and library namespace
    # when it lists an installed tag, including explicitly qualified pulls.
    def canonical(name):
        name = name.lower().removeprefix("registry.ollama.ai/").removeprefix("library/")
        return name if ":" in name.rsplit("/", 1)[-1] else name + ":latest"

    return canonical(reported) == canonical(requested)
