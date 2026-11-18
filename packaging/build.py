"""Build one portable Aider Deck archive from the checked-out Git commit."""

import argparse
import hashlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON_VERSION = "3.12.16"
PYTHON_RELEASE = "20261110"
RELEASE_URL = "https://github.com/astral-sh/python-build-standalone/releases/download"
WINDOWS_TARGET = "windows-x86_64"
TARGETS = {
    "linux-x86_64": (
        "x86_64-unknown-linux-gnu",
        "6a9e62dad033790aef722dbd370e0ce76ce17b73627ba3ae679f537049350b39",
        ("manylinux_2_28_x86_64", "manylinux2014_x86_64"),
    ),
    "macos-arm64": (
        "aarch64-apple-darwin",
        "2f11b8aa3726bdbd87dd9eb76568412faddf4f47e26913fa45d6a2e424972acd",
        ("macosx_11_0_arm64",),
    ),
    WINDOWS_TARGET: (
        "x86_64-pc-windows-msvc",
        "355ee61b8207b91574491b1c6dc9b0ebb4408fc56bfafe498f3ef439314b52dc",
        ("win_amd64",),
    ),
}
APP_FILES = ("app.py", "deck", "static", "templates", "prompts",
             "presets.json", "LICENSE", "README.md")


def extract(archive, destination):
    with tarfile.open(archive) as tar:
        tar.extractall(destination, filter="data")


def build(target, output):
    triple, checksum, platforms = TARGETS[target]
    windows = target == WINDOWS_TARGET
    # pip evaluates the pywinpty requirement marker on the build host.
    if windows != (sys.platform == "win32"):
        raise ValueError("Build the Windows package on Windows and the Unix packages on Linux or macOS.")
    name = f"aider-deck-{target}"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output) as tmp:
        stage = Path(tmp)
        bundle = stage / name
        bundle.mkdir()
        source = stage / "source.tar"
        subprocess.run(
            ["git", "archive", "--format=tar", "-o", str(source), "HEAD", *APP_FILES],
            cwd=ROOT, check=True,
        )
        extract(source, bundle)
        for filename in (("run.bat" if windows else "run.sh"), "launch.py"):
            shutil.copy2(ROOT / "packaging" / filename, bundle)

        runtime = stage / "python.tar.gz"
        asset = f"cpython-{PYTHON_VERSION}+{PYTHON_RELEASE}-{triple}-install_only_stripped.tar.gz"
        subprocess.run(
            ["curl", "--fail", "--silent", "--show-error", "--location",
             "--retry", "2", "--output", str(runtime),
             f"{RELEASE_URL}/{PYTHON_RELEASE}/{asset}"],
            check=True,
        )
        with runtime.open("rb") as download:
            digest = hashlib.file_digest(download, "sha256").hexdigest()
        if digest != checksum:
            raise ValueError(f"Python runtime checksum mismatch for {asset}.")
        extract(runtime, bundle)

        python_minor = PYTHON_VERSION.rsplit(".", 1)[0]
        runtime_root = bundle / "python"
        lib = runtime_root / "Lib" if windows else runtime_root / "lib" / f"python{python_minor}"
        site = lib / "site-packages"
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
             "--only-binary=:all:", "--no-compile", "--implementation", "cp",
             "--python-version", python_minor, "--target", str(site),
             *(arg for platform in platforms for arg in ("--platform", platform)),
             "-r", str(ROOT / "requirements.txt")],
            check=True,
        )
        # pip-generated script shebangs point at the build machine.
        scripts = site / ("Scripts" if windows else "bin")
        if scripts.is_dir():
            shutil.rmtree(scripts)

        if windows:
            archive = Path(shutil.make_archive(str(stage / name), "zip", stage, name))
        else:
            archive = stage / f"{name}.tar.gz"
            with tarfile.open(archive, "w:gz", compresslevel=6) as tar:
                tar.add(
                    bundle, arcname=name,
                    filter=lambda member: member.replace(uid=0, gid=0, uname="", gname="", deep=False),
                )
        archive.chmod(0o644)
        result = output / archive.name
        archive.replace(result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", choices=TARGETS)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    print(build(args.target, args.output.resolve()))
