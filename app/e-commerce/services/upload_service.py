# Copyright (C) 2026 Mohammadhossein Borazjani
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""File uploads: product photos, saved safely.

Three rules, all enforced here:
1. NEVER trust the client's filename — the saved name is a server-made
   UUID plus the validated extension. A filename like
   ``../../etc/passwd`` becomes nothing at all.
2. NEVER trust the extension alone — the first bytes must match the
   format (magic bytes). A ``.png`` that is actually a script is
   rejected.
3. NEVER load the whole file into memory unbounded — read in 1 MB
   chunks, count as you go, and delete the partial file the moment the
   size limit is crossed.

Files live under ``uploads/`` (never ``static/``): uploaded content is
user data, not app code, and it is served back through a route that
checks the path — not by the static file server.
"""

from __future__ import annotations

import os
from pathlib import Path
import uuid

from fastapi import UploadFile

# Photos only, 5 MB max. Big enough for a phone photo, small enough
# that one upload cannot fill the disk.
MAX_UPLOAD_SIZE = 5 * 1024 * 1024
ALLOWED_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp"})
_CHUNK_SIZE = 1024 * 1024


class UploadError(Exception):
    """A rejected upload: ``detail`` is the message, ``status_code`` the HTTP code."""

    def __init__(self, detail: str, status_code: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def get_upload_dir() -> Path:
    """Where uploaded files live. Override ``BANDEH_UPLOAD_DIR`` in tests."""
    return Path(os.environ.get("BANDEH_UPLOAD_DIR", Path(__file__).parent.parent / "uploads"))


def _sniff_content(header: bytes, ext: str) -> bool:
    """True when the first bytes match the claimed image format."""
    if ext in {".jpg", ".jpeg"}:
        return header.startswith(b"\xff\xd8\xff")
    if ext == ".png":
        return header.startswith(b"\x89PNG\r\n\x1a\n")
    if ext == ".webp":
        return header.startswith(b"RIFF") and header[8:12] == b"WEBP"
    return False


async def save_upload(file: UploadFile, upload_dir: Path) -> str:
    """Validate and save an uploaded image. Returns the server-made filename.

    Raises UploadError (415 wrong type, 400 fake content, 413 too big).
    The caller stores the returned name in ``Product.image_path``.
    """
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise UploadError(f"only {sorted(ALLOWED_EXTENSIONS)} are allowed", status_code=415)

    header = await file.read(12)
    if not _sniff_content(header, ext):
        raise UploadError("file content does not match its extension", status_code=400)

    # Server-made name: no part of the client's filename survives.
    filename = f"{uuid.uuid4().hex}{ext}"
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / filename

    size = len(header)
    try:
        with dest.open("wb") as out:
            out.write(header)
            while True:
                chunk = await file.read(_CHUNK_SIZE)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPLOAD_SIZE:
                    raise UploadError("file is larger than 5 MB", status_code=413)
                out.write(chunk)
    except UploadError:
        # A too-big file must not leave a partial file on disk.
        dest.unlink(missing_ok=True)
        raise
    return filename


__all__ = ["ALLOWED_EXTENSIONS", "MAX_UPLOAD_SIZE", "UploadError", "get_upload_dir", "save_upload"]
