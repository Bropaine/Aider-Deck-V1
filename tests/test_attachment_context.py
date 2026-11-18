import base64
import subprocess
from unittest.mock import Mock

import pytest

from app import create_app
from deck.attachment_context import attachment_warning, context_budget, text_batch_estimate


MODEL = "ollama_chat/example"


def observation(model=MODEL):
    return dict(status="live", model=model, tokens=dict(stale=False, data=dict(model=model, used=100, max=65536)))


def test_local_budget_uses_the_running_tag_and_prefers_a_current_report():
    state = observation()
    state["reply_tokens"] = dict(model=MODEL, used=50)
    runner = dict(model=MODEL, window=512, model_capacity=131072)
    budget = context_budget(state, 2048, runner)
    assert budget == dict(model=MODEL, window=512, source="Ollama loaded window", used=100,
                          remaining=412, usage_source="report")
    state["tokens"]["stale"] = True
    budget = context_budget(state, None, None)
    assert (budget["window"], budget["remaining"], budget["usage_source"]) == (65536, 65486, "reply")
    state["model"] = "ollama_chat/other"
    budget = context_budget(state, None, runner)
    assert budget["window"] is budget["used"] is budget["remaining"] is None


def test_model_default_configured_capacity_and_anonymous_hosted_report():
    state = observation()
    runner = dict(model=MODEL, model_default=4096, model_capacity=131072)
    assert context_budget(state, 2048, runner)["window"] == 4096
    del runner["model_default"]
    assert context_budget(state, 2048, runner)["window"] == 2048
    assert context_budget(state, None, runner)["window"] == 131072
    del state["tokens"]["data"]["model"]
    assert context_budget(state, None, runner)["used"] is None
    state["model"] = "provider/main"
    state["tokens"]["data"]["used"] = 0
    assert context_budget(state, None, None)["remaining"] == 65536
    state["status"] = "exited"
    assert context_budget(state, 1234, runner)["model"] is None
    assert context_budget(state, 1234, runner)["remaining"] is None


def test_utf8_names_dense_text_and_entire_batch_are_counted():
    name = "例.txt"
    content = "abc中!".encode()
    estimate, mixed = text_batch_estimate([(name, content), ("pad.txt", b"abc"), ("pic.PNG", b"x")])
    overhead = len(name.encode()) + len("pad.txt") + 2 * 16
    assert (estimate, mixed) == (overhead + 1 + 3 + 1 + 1, True)
    assert text_batch_estimate([("pic.JPEG", b"x")]) == (None, True)


@pytest.mark.parametrize(("estimate", "images", "changing", "reason"), [
    (89, False, False, None), (90, False, False, "near"), (100, False, False, "near"),
    (101, False, False, "over"), (2, True, False, "mixed"), (1, False, True, "unknown"),
    (None, True, True, None),
])
def test_boundaries_and_unestimated_images(estimate, images, changing, reason):
    budget = dict(model=MODEL, window=1100, remaining=100, source="Ollama loaded window")
    warning = attachment_warning(budget, estimate, images, changing)
    assert (warning and warning["reason"]) == reason
    if reason:
        assert warning["remaining_tokens"] == (None if changing else 100)


def test_advertised_capacity_cannot_confirm_that_text_fits_actual_local_allocation():
    state = observation()
    budget = context_budget(state, None, dict(model=MODEL, model_capacity=131072))
    warning = attachment_warning(budget, 12000, False, changing=False)
    assert (warning["reason"], warning["remaining_tokens"], warning["source"]) == (
        "capacity", 130972, "Ollama model capacity"
    )
    assert attachment_warning(budget, 150000, False, changing=False)["reason"] == "over"


@pytest.fixture
def route(tmp_path, monkeypatch):
    home, repo = tmp_path / "home", tmp_path / "repo"
    home.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    app = create_app(tmp_path / "state.json", home)
    deck, client = app.extensions["deck"], app.test_client()
    wid = client.post("/api/workspaces", json={"path": str(repo)}).json["id"]
    session = deck.session(wid)
    session.status, session.proc, session.obs.ready = "live", Mock(), True
    session.obs.data.update(observation())
    monkeypatch.setattr("app.live_context", lambda snapshot, routes: {"model": MODEL, "window": 2048})
    url = f"/api/{wid}/attachments"
    yield client, session, url
    session.proc.isalive.return_value = False
    deck.close()


def upload(session, content=b"log123\n" * 1100):
    files = [dict(name="sample.txt", data=base64.b64encode(content).decode())]
    return dict(files=files, session=session.attachments.generation, model=session.obs.data["model"])


def test_review_is_side_effect_free_and_confirmation_is_bound_to_original_contents(route):
    client, session, url = route
    body = upload(session)
    view = client.get(url.removesuffix("attachments") + "state").json
    assert view["supports_attachment_context"]
    review = client.post(url, json=body).json["context_review"]
    assert review["reason"] == "over" and review["remaining_tokens"] == view["context_budget"]["remaining"] == 1948
    assert not session.attachments.items and not session.pending
    session.proc.write.assert_not_called()
    body["context_confirmation"] = review["confirmation"]
    # Identical sizes, filenames and estimates still require approval of these exact bytes.
    swapped = dict(body, files=upload(session, b"abc456\n" * 1100)["files"])
    assert client.post(url, json=swapped).status_code == 409
    session.obs.data["tokens"]["data"]["used"] += 20
    assert client.post(url, json=body).status_code == 409
    assert not session.attachments.items and not session.pending
    del body["context_confirmation"]
    review = client.post(url, json=body).json["context_review"]
    body["context_confirmation"] = review["confirmation"]
    response = client.post(url, json=body)
    assert response.status_code == 200, response.json
    assert response.json["attachments"][0]["status"] == "attaching"
    assert len(session.attachments.items) == 1
    assert client.post(url, json=body).status_code == 409


def test_unknown_manual_confirmation_and_model_session_and_queue_checks(route):
    client, session, url = route
    body = upload(session, b"hello")
    session.obs.data["tokens"]["stale"] = True
    review = client.post(url, json=body).json["context_review"]
    assert (review["reason"], review["remaining_tokens"], review["window_tokens"]) == ("unknown", None, 2048)
    assert client.post(url, json={k: v for k, v in body.items() if k != "model"}).status_code == 409
    body["context_confirmation"] = review["confirmation"]
    session.obs.data["model"] = "different-model"
    assert client.post(url, json=body).status_code == 409
    assert client.post(url, json=dict(body, model="different-model")).status_code == 409
    session.obs.data["model"] = MODEL
    assert client.post(url, json=dict(body, session="old-session")).status_code == 400
    session.obs.data["tokens"]["stale"] = False
    session.pending.append("/add file.txt")
    assert client.post(url, json=upload(session, b"more")).json["context_review"]["remaining_tokens"] is None
    assert not session.attachments.items
    session.pending.clear()
    session.obs.data["tokens"]["stale"] = True
    response = client.post(url, json=body)
    assert response.status_code == 200, response.json
    assert response.json["attachments"][0]["name"] == "sample.txt"


def test_bigger_than_former_limit_attaches_when_it_fits_without_an_extra_command(route):
    client, session, url = route
    session.obs.data.update(observation("provider/wide"))
    session.obs.data["tokens"]["data"]["max"] = 120000
    body = upload(session, b"Aa1" * 90000)
    response = client.post(url, json=body)
    assert response.status_code == 200, response.json
    assert response.json["attachments"][0]["size"] == 270000
    assert len(session.attachments.items) == 1
    assert all("/tokens" not in cmd for cmd in session.pending)
