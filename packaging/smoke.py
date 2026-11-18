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


MODEL = "deck-package-probe"


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


def write_unix_probe(bin_dir):
    probe = bin_dir / "aider"
    probe.write_text(
        "#!/bin/sh\n"
        "test \"$1\" = --no-gui || exit 2\n"
        f"printf 'Aider v0.86.2\\nModel: {MODEL} with diff edit format\\n'\n",
        encoding="utf-8",
    )
    probe.chmod(0o700)


def unix_probe(bin_dir, home, bundle):
    write_unix_probe(bin_dir)
    env = {
        "HOME": str(home),
        "PATH": str(bin_dir) + os.pathsep + os.defpath,
        "BROWSER": "true",
    }
    return [str(bundle / "run.sh")], env


def windows_probe(bin_dir, home, bundle):
    system_root = Path(os.environ["SystemRoot"])
    system_bin = system_root / "System32"
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("Git is required by the Windows smoke test.")
    env = {
        "SystemRoot": str(system_root),
        "COMSPEC": str(system_bin / "cmd.exe"),
        "PATH": os.pathsep.join((str(bin_dir), str(system_bin), str(Path(git).parent))),
        "PATHEXT": ".COM;.EXE;.BAT;.CMD",
        "HOME": str(home),
        "USERPROFILE": str(home),
        "TEMP": str(home),
        "TMP": str(home),
        "BROWSER": f'"{sys.executable}" -c "pass" %s',
    }
    assert shutil.which("aider", path=env["PATH"]) is None
    source = bin_dir / "probe.cs"
    source.write_text(
        "using System;\n"
        "class Probe {\n"
        "    static int Main(string[] args) {\n"
        '        if (args.Length == 0 || args[0] != "--no-gui") return 2;\n'
        '        Console.WriteLine("Aider v0.86.2");\n'
        f'        Console.WriteLine("Model: {MODEL} with diff edit format");\n'
        "        return 0;\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    compiler = system_root / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
    subprocess.run(
        [str(compiler), "/nologo", f"/out:{bin_dir / 'aider.exe'}", str(source)],
        check=True,
    )
    return [env["COMSPEC"], "/d", "/c", "call", str(bundle / "run.bat")], env


def verify_session(port, workspace):
    opened = json.loads(request(port, "/api/workspaces", {"path": str(workspace)}))
    prefix = f"/api/{opened['id']}"
    assert json.loads(request(port, prefix + "/session/start", {}))["ok"]
    wait_for(lambda: json.loads(request(port, prefix + "/state"))["model"] == MODEL)


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
            command, env = windows_probe(bin_dir, home, bundle)
        else:
            command, env = unix_probe(bin_dir, home, bundle)
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

                    health = wait_for(health_check)
                    assert json.loads(health) == {"app": "aider-deck", "status": "ready"}
                    assert b"Aider" in request(port, "/")
                    assert request(port, "/static/vendor/xterm.js")
                    verify_session(port, workspace)
                    assert not (bundle / ".aider-deck" / "aider-venv").exists()
                    print(f"{archive.name}: Bundled Deck served the UI and ran the Aider probe from PATH.")
                except Exception:
                    log.seek(0)
                    print(log.read(), file=sys.stderr)
                    raise
                finally:
                    stop_server(server)


if __name__ == "__main__":
    smoke(Path(sys.argv[1]).resolve())
