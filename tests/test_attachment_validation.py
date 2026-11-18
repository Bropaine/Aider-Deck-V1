import base64
import codecs
from io import BytesIO
import subprocess

import pytest
from PIL import Image

from deck import attachment_validation as validation


@pytest.fixture
def project(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    root = tmp_path / "work"
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


def file(name, raw=b"ordinary text"):
    return {"name": name, "data": base64.b64encode(raw).decode("ascii")}


def image(fmt, size=(6, 5)):
    buf = BytesIO()
    with Image.new("L", size, 128) as picture:
        picture.save(buf, format=fmt)
    return buf.getvalue()


@pytest.mark.parametrize("name", [
    ".Env", ".ENV.backup", "id_rsa.pub", "Private.PEM", "store.p12", ".aider.chat.history.md",
    ".aider.tags.cache.v4", ".aider.conf.yml", ".aider.input.history", "old-Credential-notes.md",
    "credentials", "invoice-tOkeN.txt", "my-secret-settings", "deck-state-5177.json",
])
def test_targeted_private_names_are_rejected(project, name):
    with pytest.raises(ValueError, match="private filename"):
        validation.validated_files(project, [file(name)])


@pytest.mark.parametrize("name", [
    "", ".", "..", ".git", "NUL.txt", "CON", "c:relative.txt", "a.txt:stream",
    "../notes.txt", "dir\\notes.txt", "line\n/help", 'quote"name', "note?.txt",
    "wild*.txt", "note.", "notes. ", "\u202ename.txt",
])
def test_names_cannot_address_paths_devices_or_terminal_input(project, name):
    with pytest.raises(ValueError):
        validation.validated_files(project, [file(name)])


def test_parent_repo_ignore_applies_when_git_root_is_also_home(project, monkeypatch):
    monkeypatch.setenv("HOME", str(project))
    monkeypatch.setenv("USERPROFILE", str(project))
    nested = project / "work"
    nested.mkdir()
    (project / ".aiderignore").write_text("work/owner-notes.md\n")
    with pytest.raises(ValueError, match="ignore rules"):
        validation.validated_files(nested, [file("owner-notes.md")])


def test_workspace_custom_aiderignore_and_tracked_gitignored_basename(project):
    child = project / "src"
    child.mkdir()
    (child / "tracked.md").write_text("original")
    subprocess.run(["git", "add", "src/tracked.md"], cwd=project, check=True)
    (project / ".gitignore").write_text("/src/tracked.md\n")
    (project / "skip.rules").write_text("/src/custom.*\n!/src/custom.py\n")
    (project / ".aider.conf.yml").write_text("aiderignore: skip.rules\n")
    (child / ".aiderignore").write_text("cwd-only.txt\n")
    for name in ["tracked.md", "custom.md", "cwd-only.txt"]:
        with pytest.raises(ValueError, match="excluded"):
            validation.validated_files(child, [file(name)])
    assert validation.validated_files(child, [file("custom.py")]) == [("custom.py", b"ordinary text")]


def test_sensitive_values_in_filenames_or_text_are_blocked_without_echo(project):
    sample = "synthetic-saved-attachment-value"
    (project / ".env").write_text("FOO_API_KEY=" + sample)
    provider_shape = "sk-proj-" + "abCD1234" * 4
    for value in [sample, provider_shape]:
        for payload in [[file(value + ".md")], [file("meeting.txt", ("line one\n" + value).encode())]]:
            with pytest.raises(ValueError, match="credential") as exc:
                validation.validated_files(project, payload)
            assert value not in str(exc.value)


def test_text_code_including_bom_and_empty_files_remains_byte_exact(project):
    raw = codecs.BOM_UTF8 + "α = 'hello'\r\n\tprint(α)\n".encode()
    assert validation.validated_files(project, [file("café [1].py", raw), file("Makefile", b"")]) == [
        ("café [1].py", raw), ("Makefile", b""),
    ]
    large = b"a" * (256 * 1024 + 100)
    assert validation.validated_files(project, [file("large.txt", large)]) == [("large.txt", large)]


@pytest.mark.parametrize("payload", [
    [], [None], [{"name": "ok.txt"}], [file("note") for _ in range(9)],
    [{"name": "bad.txt", "data": "invalid**"}], [{"name": "bad.txt", "data": {"key": "value"}}],
    [file("bad.txt", b"data\x1b[2J")], [file("bad.txt", "hi".encode("utf-16"))],
    [file("bad.txt", codecs.BOM_UTF8 + b"%PDF-1.4\n")], [file("no.pdf")],
])
def test_malformed_batches_or_unsupported_content_fail(project, payload):
    with pytest.raises(ValueError):
        validation.validated_files(project, payload)


@pytest.mark.parametrize(("fmt", "filename"), [("PNG", "screen.PNG"), ("JPEG", "photo.JpEg")])
def test_images_require_complete_matching_payload(project, fmt, filename):
    raw = image(fmt)
    assert validation.validated_files(project, [file(filename, raw)]) == [(filename, raw)]
    for invalid in [file(filename, raw[:-1]), file(filename, b"text"), file("image.txt", raw),
                    file("wrong.jpg" if fmt == "PNG" else "wrong.png", raw)]:
        with pytest.raises(ValueError):
            validation.validated_files(project, [invalid])


def test_image_integrity_still_frame_and_dimensions(project):
    corrupted = bytearray(image("PNG"))
    offset = corrupted.index(b"IDAT") + len(b"IDAT")
    corrupted[offset] ^= 1
    with pytest.raises(ValueError):
        validation.validated_files(project, [file("corrupt.png", corrupted)])
    animated = BytesIO()
    with Image.new("RGB", (4, 3), "red") as first, Image.new("RGB", (4, 3), "blue") as second:
        first.save(animated, format="PNG", save_all=True, append_images=[second])
    with pytest.raises(ValueError, match="still"):
        validation.validated_files(project, [file("animated.png", animated.getvalue())])
    with pytest.raises(ValueError, match="megapixels"):
        validation.validated_files(project, [file("large.png", image("PNG", (4001, 4000)))])


def test_file_and_aggregate_limits(project, monkeypatch):
    for name, limit in [("code.py", validation.MAX_TOTAL_BYTES), ("photo.jpg", validation.MAX_IMAGE_BYTES)]:
        with pytest.raises(ValueError, match="up to"):
            validation.validated_files(project, [file(name, b"a" * (limit + 1))])
    monkeypatch.setattr(validation, "MAX_TOTAL_BYTES", 8)
    with pytest.raises(ValueError, match="total"):
        validation.validated_files(project, [file("one.txt", b"12345"), file("two.txt", b"6789")])


def test_large_upload_is_still_scanned_past_former_text_cap(project):
    value = b"sk-proj-" + b"abCD1234" * 4
    raw = b"a" * (256 * 1024 + 11) + b"\n" + value
    with pytest.raises(ValueError, match="credential"):
        validation.validated_files(project, [file("large.txt", raw)])
