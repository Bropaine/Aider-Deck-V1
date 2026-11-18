
import pytest

from deck.files import confined, file_command, tree
from deck.state import State


def test_file_arguments_stay_inside_workspace(tmp_path):
    (tmp_path / "a file.py").touch()
    assert (
        file_command(tmp_path, "read-only", "*.py")
        == f'/read-only "{tmp_path / "a file.py"}"'
    )
    for unsafe in (
        "../keys",
        "D:etc",
        r"\\server\share",
        "C:/Windows",
        ".git/config",
        "x\r/clear",
        'foo"/bar',
    ):
        with pytest.raises(ValueError):
            file_command(tmp_path, "add", unsafe)
    with pytest.raises(ValueError):
        confined(tmp_path, "../escape")


def test_aiderignore_and_persistent_history(tmp_path):
    (tmp_path / "test.py").touch()
    (tmp_path / ".aiderignore").write_text("*.py\n")
    assert "test.py" not in {r["name"] for r in tree(tmp_path, "", False)}
    assert next(
        r["ignored"] for r in tree(tmp_path, "", True) if r["name"] == "test.py"
    )
    store = State(tmp_path / "state.json", tmp_path)
    wid = store.open(str(tmp_path))
    store.preferences(wid, {"enter_sends": False})
    for n in range(202):
        store.remember(wid, str(n))
    restored = State(store.file, tmp_path).entry(wid)
    assert restored["history"] == list(map(str, range(2, 202)))
    assert restored["prefs"]["enter_sends"] is False
