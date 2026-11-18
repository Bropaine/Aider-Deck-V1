"""Launch an extracted package and verify that it starts an Aider session."""

import json
import os
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen
from zipfile import ZipFile


def request(port, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = Request(f"http://127.0.0.1:{port}{path}", data=data,
                  headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=1) as response:
        return response.read()


def wait_for(check, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if result := check():
                return result
        except (URLError, TimeoutError):
            pass
        time.sleep(0.1)
    raise RuntimeError("Timed out waiting for the packaged Deck server.")


def unix_probe(bin_dir, home, bundle):
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
    return [str(bundle / "run.sh")], env, "deck-package-probe"


def windows_probe(home, bundle, workspace):
    system_root = Path(os.environ["SystemRoot"])
    system_bin = system_root / "System32"
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("Git is required by the Windows smoke test.")
    env = {
        "SystemRoot": str(system_root),
        "COMSPEC": str(system_bin / "cmd.exe"),
        "PATH": os.pathsep.join((str(system_bin), str(Path(git).parent))),
        "PATHEXT": ".COM;.EXE;.BAT;.CMD",
        "HOME": str(home),
        "USERPROFILE": str(home),
        "TEMP": str(home),
        "TMP": str(home),
        "BROWSER": f'"{sys.executable}" -c "pass" %s',
    }
    assert shutil.which("aider", path=env["PATH"]) is None
    model = "openai/gpt-4o"
    (workspace / ".aider.conf.yml").write_text(
        f"model: {model}\nshow-model-warnings: false\ncheck-update: false\n"
        "analytics: false\ngitignore: false\nmap-tokens: 0\n",
        encoding="utf-8",
    )
    command = [env["COMSPEC"], "/d", "/c", "call", str(bundle / "run.bat"), "--install-aider"]
    return command, env, model


def stop_server(server):
    if server.poll() is not None:
        return
    if os.name == "nt":
        taskkill = Path(os.environ["SystemRoot"]) / "System32" / "taskkill.exe"
        subprocess.run([str(taskkill), "/PID", str(server.pid), "/T", "/F"], check=True)
    else:
        server.terminate()


def smoke(archive):
    with tempfile.TemporaryDirectory(prefix="deck package ") as tmp:
        root = Path(tmp)
        windows = archive.suffix == ".zip"
        if windows:
            with ZipFile(archive) as zip_file:
                zip_file.extractall(root)
            bundle = root / archive.stem
        else:
            with tarfile.open(archive) as tar:
                tar.extractall(root, filter="data")
            bundle = root / archive.name.removesuffix(".tar.gz")
        home, bin_dir, workspace = (root / name for name in ("home", "probe bin", "workspace"))
        for directory in (home, bin_dir, workspace):
            directory.mkdir()
        if windows:
            command, env, model = windows_probe(home, bundle, workspace)
        else:
            command, env, model = unix_probe(bin_dir, home, bundle)
        subprocess.run(["git", "init", "-q", str(workspace)], env=env, check=True)

        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        with (root / "server.log").open("w+", encoding="utf-8") as log:
            with subprocess.Popen(
                [*command, "--port", str(port)],
                env=env, cwd=root, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            ) as server:
                try:
                    def health_check():
                        if server.poll() is not None:
                            raise RuntimeError(f"Deck exited before serving HTTP ({server.returncode}).")
                        return request(port, "/api/health")

                    health = wait_for(health_check, timeout=300 if windows else 15)
                    assert json.loads(health) == {"app": "aider-deck", "status": "ready"}
                    assert b"Aider" in request(port, "/")
                    assert request(port, "/static/vendor/xterm.js")
                    opened = json.loads(request(port, "/api/workspaces", {"path": str(workspace)}))
                    prefix = f"/api/{opened['id']}"
                    assert json.loads(request(port, prefix + "/session/start", {}))["ok"]
                    wait_for(
                        lambda: json.loads(request(port, prefix + "/state"))["model"] == model,
                        timeout=45 if windows else 15,
                    )
                    private = bundle / ".aider-deck"
                    if windows:
                        venv = private / "aider-venv"
                        assert (venv / "deck-install-location").read_text(encoding="utf-8") == str(bundle.resolve())
                        version = subprocess.check_output(
                            [str(venv / "Scripts" / "aider.exe"), "--version"],
                            env=env, cwd=workspace, text=True, timeout=30,
                        ).strip()
                        assert version == "aider 0.86.2", version
                        print(f"{archive.name}: Bundled Deck installed {version} and ran it through ConPTY.")
                    else:
                        assert not private.exists()
                        print(f"{archive.name}: Bundled Deck served the UI and ran the Aider probe from PATH.")
                except Exception:
                    log.seek(0)
                    print(log.read(), file=sys.stderr)
                    raise
                finally:
                    stop_server(server)


if __name__ == "__main__":
    smoke(Path(sys.argv[1]).resolve())
