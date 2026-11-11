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
"""Background image work (stub): thumbnails and webp variants.

Resizing is slow — it must never block the upload request. The route
schedules the job with FastAPI ``BackgroundTasks`` and returns at once.
This module is deliberately a STUB: it records the job so the flow is
real and testable, but no pixels are resized yet. A real worker (arq,
Celery, or a separate process watching this list's future queue) picks
these jobs up later. The stub's job is to prove the seam exists and the
request does not wait.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal, TypedDict

logger = logging.getLogger(__name__)


class ImageJob(TypedDict):
    """Queued thumbnail/webp variant job payload."""

    filename: str
    dir: str
    status: Literal["pending"] | str


# Jobs waiting for a worker. A real queue replaces this list; the shape
# (filename + dir + status) is what the worker will consume.
PENDING_JOBS: list[ImageJob] = []


def schedule_image_variants(filename: str, upload_dir: Path) -> None:
    """Queue thumbnail/webp work for an uploaded image. Returns at once."""
    PENDING_JOBS.append(
        {"filename": filename, "dir": str(upload_dir), "status": "pending"}
    )
    logger.info("image variants queued for %s", filename)


__all__ = ["PENDING_JOBS", "ImageJob", "schedule_image_variants"]
