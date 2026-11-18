import threading

from flask import Flask

from deck import envfile
from deck.config_routes import config_bp


def test_dotenv_roundtrip_and_masking(tmp_path, monkeypatch):
    home = tmp_path / "home"
    space = tmp_path / "space"
    home.mkdir()
    space.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    (home / ".env").write_text("OPENAI_API_KEY=base-value\n")
    path = space / ".env"
    original = "# heading\r\nexport OPENAI_API_KEY = 'old value'  # account\r\n\r\nNOTE=keep\r\nCOPY=${NOTE}\r\n"
    path.write_bytes(original.encode())
    key = "new'value #test\\1234"
    envfile.write_values(space, {"OPENAI_API_KEY": key, "CUSTOM": "abc", "NOTE": ""})
    saved = path.read_bytes().decode()
    assert saved.startswith("# heading\r\nexport OPENAI_API_KEY = ")
    assert "  # account\r\n\r\nNOTE=keep\r\nCOPY=${NOTE}\r\n" in saved
    assert envfile.child_environment(space)["OPENAI_API_KEY"] == key
    assert envfile.child_environment(space)["COPY"] == "keep"
    rows = {row["name"]: row for row in envfile.key_rows(space)}
    assert rows["OPENAI_API_KEY"]["masked"].endswith("1234") and rows["OPENAI_API_KEY"]["shadows_home"]
    assert "abc" not in rows["CUSTOM"]["masked"]
    assert key not in str(rows)

    class Deck:
        def workspace(self, wid):
            return space
        def session(self, wid):
            return None
        def lock(self, wid):
            return threading.RLock()
    app = Flask(__name__)
    app.register_blueprint(config_bp)
    app.extensions["deck"] = Deck()
    client = app.test_client()
    url = "/api/x/config/keys"
    second_key = "sk-fake-integration-key-5678"
    for res in (client.post(url, json={"values":{"OPENAI_API_KEY":second_key}}),
                client.get(url), client.post(url+"/verify", json={"name":"OPENAI_API_KEY"})):
        assert res.status_code == 200
        assert second_key not in res.get_data(as_text=True)
    assert envfile.child_environment(space)["OPENAI_API_KEY"] == second_key
    assert client.post(url+"/verify", json={"name":"NOT_PRESENT_DECK_CONFIG"}).json["present"] is False
