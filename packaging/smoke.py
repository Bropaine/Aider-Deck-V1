"""Launch an extracted package and verify that it starts Aider from PATH."""

import json
import os
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


def request(port, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = Request(f"http://127.0.0.1:{port}{path}", data=data,
                  headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=1) as response:
        return response.read()


def wait_for(check):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            if result := check():
                return result
        except (URLError, TimeoutError):
            pass
        time.sleep(0.1)
    raise RuntimeError("Timed out waiting for the packaged Deck server.")


def smoke(archive):
    with tempfile.TemporaryDirectory(prefix="deck package ") as tmp:
        root = Path(tmp)
        with tarfile.open(archive) as tar:
            tar.extractall(root, filter="data")
        bundle = root / archive.name.removesuffix(".tar.gz")
        home, bin_dir, workspace = (root / name for name in ("home", "probe bin", "workspace"))
        for directory in (home, bin_dir, workspace):
            directory.mkdir()

        probe = bin_dir / "aider"
        probe.write_text(
            "#!/bin/sh\n"
            "test \"$1\" = --no-gui || exit 2\n"
            "printf 'Aider v0.86.2\\nModel: deck-package-probe with diff edit format\\n'\n",
            encoding="utf-8",
        )
        probe.chmod(0o700)
        env = {
            "HOME": str(home),
            "PATH": str(bin_dir) + os.pathsep + os.defpath,
            "BROWSER": "true",
        }
        subprocess.run(["git", "init", "-q", str(workspace)], env=env, check=True)

        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        with (root / "server.log").open("w+", encoding="utf-8") as log:
            with subprocess.Popen(
                [str(bundle / "run.sh"), "--port", str(port)],
                env=env, cwd=root, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            ) as server:
                try:
                    health = wait_for(lambda: request(port, "/api/health"))
                    assert json.loads(health) == {"app": "aider-deck", "status": "ready"}
                    assert b"Aider" in request(port, "/")
                    assert request(port, "/static/vendor/xterm.js")
                    opened = json.loads(request(port, "/api/workspaces", {"path": str(workspace)}))
                    prefix = f"/api/{opened['id']}"
                    assert json.loads(request(port, prefix + "/session/start", {}))["ok"]
                    wait_for(lambda: json.loads(request(port, prefix + "/state"))["model"]
                             == "deck-package-probe")
                    assert not (bundle / ".aider-deck").exists()
                    print(f"{archive.name}: Bundled Deck served the UI and ran the Aider probe from PATH.")
                except Exception:
                    log.seek(0)
                    print(log.read(), file=sys.stderr)
                    raise
                finally:
                    if server.poll() is None:
                        server.terminate()


if __name__ == "__main__":
    smoke(Path(sys.argv[1]).resolve())
