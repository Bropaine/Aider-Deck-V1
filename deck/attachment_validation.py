"""Validate desktop file selections before creating any temporary references."""

import base64
import binascii
from io import BytesIO
from pathlib import Path
import re

from PIL import Image

from . import aider_config, files, git_service, staged_scan

MAX_FILES = 8
MAX_TOTAL_BYTES = 4 * 1024 * 1024
MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_PIXELS = 16_000_000
IMAGE_FORMAT = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG"}
PNG_END = bytes.fromhex("00000000 49454e44 ae426082")
UNSUPPORTED = {".pdf", ".gif", ".webp", ".tif", ".tiff", ".bmp", ".ico", ".heic", ".avif",
               ".zip", ".doc", ".docx", ".xlsx", ".pptx", ".exe", ".dll"}
TEXT_CONTROLS = re.compile(r"[\x00-\x08\x0b\x0e-\x1f\x7f-\x9f]")


def _filename(entry):
    name = entry.get("name") if isinstance(entry, dict) else None
    if (not isinstance(name, str) or not name or len(name) > 120 or not name.isprintable()
            or name != name.strip() or "/" in name or "\\" in name):
        raise ValueError("Choose a file with a single, printable filename up to 120 characters.")
    files.relative_path(Path(), name)
    if name in {".", ".."} or name.endswith("."):
        raise ValueError("Choose a valid filename.")
    folded = name.casefold()
    if (staged_scan.private_file(name) or any(term in folded for term in ("secret", "credential", "token"))):
        raise ValueError("A private filename in the selection is blocked.")
    return name


def _check_image(raw, expected):
    try:
        # Pillow verifies the preceding chunks but tolerates a missing IEND CRC.
        if expected == "PNG" and not raw.endswith(PNG_END):
            raise ValueError("Choose a complete PNG image.")
        with Image.open(BytesIO(raw), formats=[expected]) as im:
            if im.width * im.height > MAX_PIXELS:
                raise ValueError("Choose an image of 16 megapixels or less.")
            if getattr(im, "n_frames", 1) != 1:
                raise ValueError("Choose a still PNG or JPEG image.")
            im.verify()
        # Verify checks PNG integrity; loading also catches truncated JPEG data.
        with Image.open(BytesIO(raw), formats=[expected]) as im:
            im.load()
    except (OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ValueError("Choose a complete PNG or JPEG whose contents match its extension.") from exc


def validated_files(root, payload):
    if not isinstance(payload, list) or not 1 <= len(payload) <= MAX_FILES:
        raise ValueError("Choose between one and eight files.")
    names = [_filename(entry) for entry in payload]
    try:
        repo = git_service.repository(root)
    except git_service.GitError:
        repo = root
    configured = aider_config.effective(root)["aiderignore"][0] or ".aiderignore"
    ignorefile = repo / Path(configured).expanduser()
    # The browser provides basenames. Apply rules as if these were new files
    # in this workspace, even if a tracked file happens to share a name.
    relative_names = [(root / name).relative_to(repo).as_posix() for name in names]
    if (set(relative_names) & files.ignored_names(repo, relative_names, include_tracked=True, ignorefile=ignorefile)
            or (root != repo and set(names) & files.ignored_names(root, names, include_tracked=True))):
        raise ValueError("A filename is excluded by this workspace's ignore rules.")
    known_values = aider_config.secret_values(root)
    result, total = [], 0
    for entry, name in zip(payload, names):
        if staged_scan.scan_text(name.encode(), "filename", known_values):
            raise ValueError("A filename appears to contain a credential.")
        suffix = Path(name).suffix.casefold()
        if suffix in UNSUPPORTED:
            raise ValueError("Choose a UTF-8 text file or a PNG or JPEG image.")
        image_format = IMAGE_FORMAT.get(suffix)
        # Text has a token budget. This byte ceiling only bounds the upload
        # transport and the temporary files kept for a session.
        available = MAX_TOTAL_BYTES - total
        limit = min(MAX_IMAGE_BYTES, available) if image_format else available
        data = entry.get("data")
        if not isinstance(data, str):
            raise ValueError("Expected base64-encoded file contents.")
        size_error = ("Images can be up to 2 MiB each." if image_format and limit == MAX_IMAGE_BYTES
                      else "Attachments can total up to 4 MiB per session.")
        if len(data) > 4 * ((limit + 2) // 3):
            raise ValueError(size_error)
        try:
            raw = base64.b64decode(data, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("Invalid attachment encoding.") from exc
        if len(raw) > limit:
            raise ValueError(size_error)
        total += len(raw)
        if image_format:
            _check_image(raw, image_format)
        else:
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise ValueError("Choose a UTF-8 text file or a PNG or JPEG image.") from exc
            if TEXT_CONTROLS.search(text) or text.startswith("%PDF-"):
                raise ValueError("Choose a UTF-8 text file or a PNG or JPEG image.")
            if staged_scan.scan_text(raw, "attachment", known_values):
                raise ValueError("A text attachment appears to contain a credential. Remove it and try again.")
        result.append((name, raw))
    return result
