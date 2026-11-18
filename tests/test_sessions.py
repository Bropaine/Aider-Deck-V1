import argparse
import os
from unittest.mock import Mock

import pytest
from deck.sessions import (
    AiderSession,
    BUFFER_SIZE,
    Observations,
    OutputRing,
    Redactor,
    build_launch_args,
    format_message,
    parse_readout,
)


@pytest.mark.parametrize("architect", [True, False])
def test_launch_options_have_no_shared_destination_collision(architect):
    # These match the installed Aider 0.86.2 parser destinations and actions.
    parser = argparse.ArgumentParser()
    parser.add_argument("--edit-format", "--chat-mode")
    parser.add_argument(
        "--architect", dest="edit_format", action="store_const", const="architect"
    )
    parser.add_argument("--editor-edit-format")
    assert (
        parser.parse_args(["--edit-format", "diff", "--architect"]).edit_format
        == "architect"
    )
    assert (
        parser.parse_args(["--architect", "--edit-format", "diff"]).edit_format
        == "diff"
    )
    argv = build_launch_args({"architect": architect, "edit-format": "diff"})
    parsed, _ = parser.parse_known_args(argv[1:])
    assert "--chat-mode" not in argv
    assert ("--architect" in argv) == architect
    assert ("--edit-format" in argv) != architect
    assert parsed.edit_format == ("architect" if architect else "diff")
    assert parsed.editor_edit_format == "editor-diff"
    assert "--no-auto-commits" in argv


def test_settings_does_not_overwrite_live_announcements_when_format_diverges():
    obs = Observations()
    obs.feed("Aider v0.86.2\r\nModel: openai/custom with diff edit format\r\n")
    assert obs.data["active_format"] == "diff"
    obs.feed(
        "> /settings\r\nAider v0.86.2\r\nMain model: openai/custom with whole edit format\r\n"
    )
    obs.feed("Option settings:\r\n  - edit_format: diff\r\n>  \r\n")
    assert obs.data["settings"]["data"]["edit-format"] == "diff"
    assert obs.data["active_format"] == "whole"
    obs.feed("> /settings\r\nunknown version of settings output\r\n>  \r\n")
    assert obs.data["settings"]["stale"]
    assert "unknown version" in obs.data["settings"]["raw"]


def test_registered_model_switch_uses_stream_and_ignores_editor_announcement():
    obs = Observations()
    obs.feed(
        "Aider v0.86.2\nMain model: first with architect edit format\n"
        "Editor model: second with editor-diff edit format\n"
    )
    assert (obs.data["model"], obs.data["active_format"]) == ("first", "architect")
    obs.feed("Aider v0.86.2\nModel: second with diff edit for")
    obs.feed("mat\n")
    assert (obs.data["model"], obs.data["active_format"]) == ("second", "diff")


def test_main_switch_keeps_the_announced_weak_role_even_with_unset_launch_setting():
    obs = Observations()
    obs.feed("Aider v0.86.2\nModel: openai/gpt-4.1 with diff edit format\nGit repo: .git\n>  \n")
    assert obs.data["weak_model"] == "openai/gpt-4.1"
    obs.feed("> /model ollama_chat/nemotron-3.5-bolt:e4b\nAider v0.86.2\n"
             "Main model: ollama_chat/nemotron-\n3.5-bolt:e4b with diff edit\nformat, prompt\ncache\n")
    assert obs.data["model"] == "ollama_chat/nemotron-3.5-bolt:e4b"
    assert obs.data["weak_model"] is None
    obs.feed("Weak model: openai/\ngpt-")
    assert obs.data["weak_model"] is None
    obs.feed("4.1\nGit repo: .git with 2 files\n>  \n")
    assert obs.data["weak_model"] == "openai/gpt-4.1"
    obs.feed("> /settings\nOption settings:\n  - weak_model: None\n>  \n")
    assert obs.data["weak_model"] == "openai/gpt-4.1"


def test_distinct_incomplete_weak_role_is_unknown_and_native_same_model_is_explicit():
    obs = Observations()
    obs.feed("Aider v0.86.2\nMain model: local with architect edit format\n"
             "Editor model: other with editor-\ndiff edit format\nGit repo: none\n> \n")
    assert obs.data["weak_model"] is None and obs.data["editor_format"] == "editor-diff"
    obs.feed("> explain this log\nWeak model: quoted-text\nGit repo: none\n> \n")
    assert obs.data["weak_model"] is None
    obs.feed("Aider v0.86.2\nModel: local with diff edit format\n")
    assert obs.data["weak_model"] == "local"


def test_multiline_braces_and_blank_lines_remain_inside_once_mode_message():
    source = "first\ncode {\n}\n\nlast"
    first, *body, last = format_message(source, "ask").split("\n")
    assert last == first[1:] + "}"
    assert "\n".join(body) == "/ask " + source


def test_replayed_stream_is_redacted_even_when_a_key_spans_reads():
    key = "sk-masked-key-0123456"
    for cut in range(1, len(key)):
        mask = Redactor([key])
        first = mask.feed("A:" + key[:cut])
        second = mask.feed(key[cut:] + ".")
        assert first == "A:"
        assert first + second == "A:••••."
    ring = OutputRing()
    ring.append("a" * (BUFFER_SIZE + 10))
    ring.append("more output")
    end, data, reset = ring.read(1)
    assert reset and len(data) == BUFFER_SIZE
    assert ring.read(end - 6) == (end, "output", False)


def test_readouts_keep_source_values_and_paths_with_spaces():
    rows = parse_readout(
        "files",
        "Repo files not in the chat:\n  other.txt\nRead-only files:\n  a b.txt\nFiles in chat:\n  foo.py",
    )
    assert rows == [
        dict(path="a b.txt", read_only=True),
        dict(path="foo.py", read_only=False),
    ]
    tokens = parse_readout(
        "tokens",
        "$ 0.00   2 system messages\n$ 0.00  1 a b.txt   /drop to remove\n"
        "$ 0.00 3 tokens total\n 1,200 tokens max context window size\n",
    )
    assert tokens == dict(system=2, files=1, used=3, max=1200,
                          file_rows=[dict(path="a b.txt", tokens=1, read_only=False)])
    assert parse_readout("tokens", "Tokens: 100 sent") is None
    assert parse_readout("tokens", "12 tokens total") == {"used": 12, "file_rows": []}


def test_tokens_parse_native_model_and_file_rows_before_aggregate_names():
    rows = parse_readout("tokens", (
        "Approximate context window usage for ollama_chat/MyModel:q4, in tokens:\n\n"
        "$ 0.0000      350 system messages\n"
        "$ 0.0000        5 chat history                use /clear to clear\n"
        "$ 0.0000       40 repository map              use --map-tokens to resize\n"
        "$ 0.0000       12 My Mixed File.txt (read-only) /drop to remove\n"
        "$ 0.0000    1,280 system messages              /drop to remove\n"
        "$ 0.0000        1 123 tokens total             /drop to remove\n"
        "$ 0.0000    1,688 tokens total\n      4,096 tokens max context window size"
    ))
    assert rows == {
        "model": "ollama_chat/MyModel:q4", "system": 350, "history": 5,
        "repo_map": 40, "files": 1293, "used": 1688, "max": 4096,
        "file_rows": [
            {"path": "My Mixed File.txt", "read_only": True, "tokens": 12},
            {"path": "system messages", "read_only": False, "tokens": 1280},
            {"path": "123 tokens total", "read_only": False, "tokens": 1},
        ],
    }


def test_wrapped_token_rows_map_to_exact_reported_names_and_roles():
    attached = [
        dict(path="veryLongFileWith  Two Spaces.TXT", read_only=True),
        dict(path="notes (read-only)", read_only=False),
    ]
    report = parse_readout("tokens", (
        "Approximate context window usage for\nollama_chat/sample, in tokens:\n"
        "$0.0000 20 veryLongFile\nWith Two Spaces.TXT (read-only) /drop to\nremove\n"
        "$0.0000 31 notes (read-only) /drop to remove\n"
        "$0.0000 51 tokens total"
    ), attached=attached)
    assert report["model"] == "ollama_chat/sample"
    assert report["file_rows"] == [dict(**attached[0], tokens=20), dict(**attached[1], tokens=31)]


def test_manual_token_report_becomes_stale_on_native_attachment_and_model_changes():
    obs = Observations()
    obs.feed("Aider v0.86.2\nModel: first with diff edit format\n>  \n")
    assert obs.data["tokens"]["stale"]
    obs.feed("> /tokens\nApproximate context window usage for first, in tokens:\n"
             "$0.0000 3 tokens total\n>  \n")
    assert not obs.data["tokens"]["stale"]
    obs.feed("> /add note.txt\n>  \n")
    assert obs.data["tokens"]["stale"]
    obs.feed("> /tokens\nApproximate context window usage for first, in tokens:\n"
             "$0.0000 1 note.txt /drop to remove\n$0.0000 4 tokens total\n>  \n")
    assert not obs.data["tokens"]["stale"]
    obs.feed("Aider v0.86.2\nModel: second with diff edit format\n>  \n")
    assert obs.data["tokens"]["stale"]
    obs.feed("> /tokens\nApproximate context window usage for first, in tokens:\n"
             "$0.0000 4 tokens total\n>  \n")
    assert obs.data["tokens"]["stale"] and "first" in obs.data["tokens"]["raw"]


def test_tokens_run_only_when_requested_and_replies_update_the_meter_passively(tmp_path):
    session = AiderSession(tmp_path)
    session.status, session.proc = "live", Mock()
    obs = session.obs
    obs.feed("Aider v0.86.2\nModel: first with diff edit format\n>  \n")
    session._dispatch()
    session.proc.write.assert_not_called()
    session.command("/tokens")
    session.proc.write.assert_called_once_with("/tokens\r")
    obs.feed("> /tokens\nApproximate context window usage for first, in tokens:\n"
             "$0.0000 87 tokens total\n>  \n")
    assert obs.data["tokens"]["data"]["used"] == 87
    for header, total in [
        ("32 sent, 8 received", 40),
        ("1.2k sent, 300 cache write, 200 cache hit, 1,005 received", 2205),
    ]:
        session.proc.write.reset_mock()
        obs.feed("> next question\nAnswer\nTokens: " + header + ".\n>  \n")
        session._dispatch()
        session.proc.write.assert_not_called()
        assert obs.data["tokens"]["stale"]
        assert obs.data["reply_tokens"] == {"model": "first", "used": total}
    obs.feed("> /add note.txt\n>  \n")
    session._dispatch()
    session.proc.write.assert_not_called()
    assert obs.data["reply_tokens"] is None
    obs.feed("> next question\nTokens: 10 sent, 2 received.\n>  \n")
    obs.feed("Aider v0.86.2\nModel: second with diff edit format\n>  \n")
    session._dispatch()
    session.proc.write.assert_not_called()
    assert obs.data["reply_tokens"] is None


def test_native_footer_wraps_across_cache_fields_and_hard_terminal_rows():
    obs = Observations()
    obs.feed("Aider v0.86.2\nMain model: primary with architect edit format\n"
             "Editor model: editor with editor-diff edit format\n> plan\n")
    obs.feed("Tokens: 1.\r\n2k sent, 12 cache wr\r\nite,\r\n"
             "800 cache hit, 30 re\r\nceived.\r\n")
    assert obs.data["reply_tokens"] == {"model": "primary", "used": 1230}
    # An Architect editor can print its own footer without announcing itself.
    obs.feed("Implementing the plan\nTokens: 222 sent, 111 received.\n>  \n")
    assert obs.data["reply_tokens"] == {"model": "primary", "used": 1230}


def test_secret_split_by_terminal_color_and_wrapping_remains_private():
    key = "sk-color-and-wrap-12345"
    stream = (
        "prefix:" + key[:8] + "\x1b[32m" + key[8:14] + "\r\n" + key[14:] + "\x1b[0m."
    )
    for cut in range(len(stream)):
        mask = Redactor([key])
        combined = mask.feed(stream[:cut]) + mask.feed(stream[cut:], final=True)
        assert combined == "prefix:••••\x1b[0m."


def test_quoted_prompt_in_model_output_cannot_send_a_queued_command():
    obs = Observations()
    obs.feed("\x1b[?2004h> /ask question\r\n\x1b[?2004l")
    obs.feed("A Markdown quote follows:\n> \n")
    assert not obs.ready
    obs.feed("\x1b[?2004hask> \r\n")
    assert obs.ready and obs.data["mode"] == "ask"


def test_terminal_replies_cannot_become_aider_messages_during_live_or_replay(tmp_path):
    from unittest.mock import Mock

    session = AiderSession(tmp_path)
    session.status = "live"
    session.proc = Mock()
    session.write("\x1b[?1;2c\x1b[>0;276;0c")
    session.proc.write.assert_not_called()
    assert not session.typing
    session.write("\x1b[?1;2creal input\x1b[A")
    session.proc.write.assert_called_once_with("real input\x1b[A")
    assert session.typing


def test_composer_quotes_literal_tabs_at_fancy_aider_prompt(tmp_path):
    from unittest.mock import Mock

    session = AiderSession(tmp_path)
    session.status = "live"
    session.proc = Mock()
    session.obs.bracketed = session.obs.ready = True
    session.send("example\n\tindented\n}")
    raw = session.proc.write.call_args.args[0]
    assert "\r\x1b[200~\tindented\x1b[201~\r}\r" in raw


def test_fresh_terminal_advertises_its_own_terminal_and_drops_previous_screen(
    tmp_path, monkeypatch
):
    if os.name == "nt":
        import winpty

        proc = Mock(pid=123)
        monkeypatch.setattr(winpty, "PTY", Mock(return_value=proc))
        executable = "aider.exe"
        monkeypatch.setattr("deck.sessions.threading.Thread", Mock())
    else:
        probe = tmp_path / "aider probe"
        probe.write_text(
            "#!/bin/sh\nprintf '%s|%s|%s\\n' \"$KEEP\" \"$TERM\" \"${Term-unset}\"\n",
            encoding="utf-8",
        )
        probe.chmod(0o700)
        executable = str(probe)

    monkeypatch.setattr("deck.sessions.shutil.which", lambda _: executable)
    session = AiderSession(tmp_path)
    session.output.append("previous session's question")
    cursor = session.output.end
    launcher = {"Term": "dumb", "KEEP": "value"}
    with session.lock:
        session.start({}, launcher)
        try:
            assert launcher["Term"] == "dumb"
            assert session.output.read(cursor) == (cursor + 1, "", True)
            assert session.snapshot()["input_state"] == "starting"
            if os.name == "nt":
                assert proc.spawn.call_args.kwargs["env"].split("\0") == [
                    "KEEP=value",
                    "TERM=xterm-256color",
                    "",
                ]
            else:
                assert session.lock.wait_for(lambda: session.output.end > cursor + 1, 5)
                _, output, reset = session.output.read(cursor)
                assert reset and output == "value|xterm-256color|unset\n"
        finally:
            if os.name != "nt":
                session.close()


def test_queued_mode_waits_for_real_empty_prompt_and_can_be_replaced(tmp_path):
    session = AiderSession(tmp_path)
    session.status, session.proc = "live", Mock()
    session.obs.feed(
        "\x1b[?2004hAdd .env to .gitignore (rec\r\nommended)? (Y)es/(N)o [Yes]\r\n: "
    )
    session.mode("help")
    session.mode("ask")
    state = session.snapshot()
    assert state["input_state"] == "waiting" and state["mode"] is None
    assert (
        state["input_prompt"]
        == "Add .env to .gitignore (recommended)? (Y)es/(N)o [Yes]:"
    )
    assert state["pending_mode"] == "ask"
    session.proc.write.assert_not_called()
    session.write("n\r")
    session.obs.feed(
        "\r\n\x1b[?2004lAider v0.86.2\r\nModel: local with diff edit format\r\n"
    )
    session._dispatch()
    session.proc.write.assert_called_once_with("n\r")
    session.obs.feed("\x1b[?2004h>  \r\n")
    session._dispatch()
    assert session.proc.write.call_args.args == ("/chat-mode ask\r",)
    assert session.snapshot()["mode"] == "code"
    assert session.snapshot()["pending_mode"] == "ask"


def test_queued_effort_is_bound_to_the_announced_model(tmp_path):
    session = AiderSession(tmp_path)
    session.status, session.proc = "live", Mock()
    session.obs.data["model"] = "first"
    session.command("/reasoning-effort high", expected_model="first")
    session.command("/ls")
    session.obs.data["model"] = "second"
    with pytest.raises(ValueError, match="model changed"):
        session.command("/reasoning-effort high", expected_model="first")
    session.obs.ready = True
    session._dispatch()
    session.proc.write.assert_called_once_with("/ls\r")
    assert "Main model changed" in session.read_since(None, timeout=0)[1]


def test_startup_ignored_effort_normalizes_wrapping_and_excludes_chat_output():
    obs = Observations()
    obs.feed("Warning: openai/gpt-5.6-\r\nterra does not \r\nsupport '\x1b[33mreasoning_")
    obs.feed("effort\x1b[0m', ignor\r\ning.\r\n")
    assert obs.data["ignored_effort_model"] == "openai/gpt-5.6-terra"
    obs.feed("Aider v0.86.2\nModel: openai/gpt-5.6-terra with diff edit format\n")
    obs.feed("Warning: unrelated does not support 'reasoning_effort', ignoring.\n")
    assert obs.data["ignored_effort_model"] == "openai/gpt-5.6-terra"
    fresh = Observations()
    fresh.feed("Warning: unrelated does not support 'thinking_tokens', ignoring.\n")
    fresh.feed("Aider v0.86.2\nWarning: model does not support 'reasoning_effort', ignoring.\n")
    assert fresh.data["ignored_effort_model"] is None


def test_context_prefill_is_preserved_for_raw_input(tmp_path):
    session = AiderSession(tmp_path)
    session.status, session.proc = "live", Mock()
    session.obs.track_context("/context hello\nworld")
    session.obs.feed("\x1b[?2004h> hello\r\nworld\r\n")
    assert session.snapshot()["input_state"] == "ready"
    session._dispatch()
    session.proc.write.assert_not_called()
    session.write("!")
    session.mode("ask")
    session.proc.write.assert_called_once_with("!")
    assert session.snapshot()["input_state"] == "typing"
    session.write("\x03")
    assert session.snapshot()["pending_mode"] is None
