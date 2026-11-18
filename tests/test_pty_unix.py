import os
import signal
import subprocess
import sys
import time
from textwrap import dedent

import pytest

if os.name != "posix":
    pytest.skip("Unix PTYs require POSIX", allow_module_level=True)

from deck.pty_unix import UnixPTY


def spawn(tmp_path, code, *args):
    return UnixPTY(
        [sys.executable, "-c", dedent(code), *args],
        tmp_path,
        {"DECK_PROBE": "ready"},
        120,
        32,
    )


def read_until(proc, done):
    deadline = time.monotonic() + 5
    output = ""
    while time.monotonic() < deadline:
        output += proc.read(blocking=False)
        if done(output):
            return output
        time.sleep(0.01)
    pytest.fail(f"Timed out reading Unix PTY. Output was {output!r}")


def test_spawn_uses_argv_cwd_and_environment(tmp_path):
    proc = spawn(tmp_path, """
        import os
        import sys

        print(os.getcwd())
        print(os.environ["DECK_PROBE"])
        print(sys.argv[1])
        """, "two words")
    try:
        assert proc.pid > 0
        assert read_until(proc, lambda _: proc.iseof()) == (
            f"{tmp_path.resolve()}\nready\ntwo words\n"
        )
        assert read_until(proc, lambda _: not proc.isalive()) == ""
        assert proc.get_exitstatus() == 0
    finally:
        proc.terminate()


def test_raw_round_trip_preserves_split_utf8_and_flushes_eof(tmp_path):
    proc = spawn(tmp_path, r"""
        import os
        import sys

        os.write(1, b"split: \xe2")
        data = sys.stdin.buffer.read(4)
        os.write(2, b"\x82\xac\nreceived: " + data + b"\ntail: \xf0\x9f")
        """)
    try:
        assert read_until(proc, lambda text: bool(text)) == "split: "
        assert proc.isalive()
        assert proc.read(blocking=False) == ""
        proc.write("é\r\x03")
        assert read_until(proc, lambda _: proc.iseof()) == (
            "€\nreceived: é\r\x03\ntail: �"
        )
        assert proc.read() == ""
    finally:
        proc.terminate()


def test_resize_changes_terminal_size_seen_by_child(tmp_path):
    proc = spawn(tmp_path, """
        import os

        def report():
            size = os.get_terminal_size(0)
            print(f"{size.columns}x{size.lines}", flush=True)

        report()
        os.read(0, 1)
        report()
        """)
    try:
        assert read_until(proc, lambda text: "\n" in text) == "120x32\n"
        proc.set_size(91, 27)
        proc.write("x")
        assert read_until(proc, lambda _: proc.iseof()) == "91x27\n"
    finally:
        proc.terminate()


def test_terminate_escalates_for_a_descendant_after_the_leader_exits(tmp_path):
    proc = spawn(tmp_path, """
        import subprocess
        import sys
        import time

        subprocess.Popen([sys.executable, "-c", sys.argv[1]])
        while True:
            time.sleep(1)
        """, dedent("""
        import os
        import signal

        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        print(os.getpid(), flush=True)
        while True:
            signal.pause()
        """))
    try:
        descendant = int(read_until(proc, lambda text: "\n" in text))
        proc.terminate()
        assert not proc.isalive()
        assert proc.get_exitstatus() == -signal.SIGTERM
        assert proc.iseof()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            state = subprocess.run(
                ["ps", "-o", "stat=", "-p", str(descendant)],
                capture_output=True, text=True, check=False,
            ).stdout.strip()
            if not state or state.startswith("Z"):
                return
            time.sleep(0.01)
        try:
            os.kill(descendant, signal.SIGKILL)
        except ProcessLookupError:
            pass
        pytest.fail(f"Descendant {descendant} is still running ({state}).")
    finally:
        if proc.isalive() or not proc.iseof():
            proc.terminate()
