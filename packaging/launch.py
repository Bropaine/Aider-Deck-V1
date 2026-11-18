"""Start a packaged copy of Deck and optionally install the Aider CLI."""

import fcntl
import os
import shutil
import subprocess
import sys
from pathlib import Path


BUNDLE = Path(__file__).resolve().parent
PRIVATE = BUNDLE / ".aider-deck"
VENV = PRIVATE / "aider-venv"
AIDER = VENV / "bin" / "aider"
INSTALL_MARKER = VENV / "deck-install-location"
AIDER_PACKAGE = "aider-chat==0.86.2"


def private_aider_ready():
    return (
        AIDER.is_file()
        and os.access(AIDER, os.X_OK)
        and INSTALL_MARKER.is_file()
        and INSTALL_MARKER.read_text(encoding="utf-8") == str(BUNDLE)
    )


def confirm_install():
    if not sys.stdin.isatty():
        return False
    try:
        reply = input("Install the Aider CLI into this copy of Deck? [y/N] ")
    except EOFError:
        return False
    return reply.strip().lower() in {"y", "yes"}


def install_aider():
    PRIVATE.mkdir(mode=0o700, exist_ok=True)
    with (PRIVATE / "install.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if private_aider_ready():
            return
        subprocess.run(
            [sys.executable, "-I", "-m", "venv", "--clear", str(VENV)], check=True,
        )
        subprocess.run(
            [str(VENV / "bin" / "python"), "-I", "-m", "pip", "install",
             "--disable-pip-version-check", AIDER_PACKAGE],
            check=True,
        )
        if not AIDER.is_file() or not os.access(AIDER, os.X_OK):
            raise OSError("The Aider install did not create an executable.")
        INSTALL_MARKER.write_text(str(BUNDLE), encoding="utf-8")


def prepare_aider(request_install):
    if shutil.which("aider"):
        return
    if not private_aider_ready():
        if not request_install and not confirm_install():
            print(
                "Aider was not found on PATH. Run ./run.sh --install-aider to install it.",
                file=sys.stderr,
            )
            return
        install_aider()
    os.environ["PATH"] = str(AIDER.parent) + os.pathsep + os.environ.get("PATH", os.defpath)


def main():
    args = sys.argv[1:]
    request_install = "--install-aider" in args
    args = [arg for arg in args if arg != "--install-aider"]
    if "-h" in args or "--help" in args:
        print("Packaged launcher option: --install-aider installs a missing Aider CLI.", flush=True)
    else:
        try:
            prepare_aider(request_install)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise SystemExit(f"Aider could not be installed: {exc}") from None
    os.execv(sys.executable, [sys.executable, "-E", "-s", str(BUNDLE / "app.py"), "--open", *args])


if __name__ == "__main__":
    main()
