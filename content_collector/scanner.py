from __future__ import annotations

import hashlib
import mimetypes
from datetime import datetime, timezone
from pathlib import Path

import filetype
from PIL import Image

from content_collector.schemas import FileInventory, FileItem

TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".csv", ".json", ".yaml", ".yml", ".html", ".htm"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
DOCUMENT_EXTENSIONS = {".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx"}
IGNORED_NAMES = {".ds_store", "thumbs.db"}


def guess_asset_type(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in TEXT_EXTENSIONS:
        return "text"
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in VIDEO_EXTENSIONS:
        return "video"
    if ext in AUDIO_EXTENSIONS:
        return "audio"
    if ext in DOCUMENT_EXTENSIONS:
        return "document"
    return "unknown"


def guess_mime(path: Path) -> str:
    kind = filetype.guess(str(path))
    if kind:
        return kind.mime
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def file_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_text_preview(path: Path, max_chars: int = 800) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return path.read_text(encoding=encoding)[:max_chars]
        except UnicodeDecodeError:
            continue
        except OSError:
            return ""
    return ""


def read_image_size(path: Path) -> tuple[int | None, int | None]:
    try:
        with Image.open(path) as image:
            return image.size
    except Exception:
        return None, None


def scan_folder(root: Path) -> FileInventory:
    files: list[FileItem] = []
    root = root.resolve()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name.lower() in IGNORED_NAMES:
            continue
        asset_type = guess_asset_type(path)
        stat = path.stat()
        relative = path.relative_to(root).as_posix()
        width = None
        height = None
        if asset_type == "image":
            width, height = read_image_size(path)
        files.append(
            FileItem(
                path=relative,
                name=path.name,
                extension=path.suffix.lower(),
                mime=guess_mime(path),
                size=stat.st_size,
                modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                asset_type=asset_type,
                text_preview=read_text_preview(path) if asset_type == "text" else "",
                width=width,
                height=height,
            )
        )
    return FileInventory(root=root.as_posix(), files=files)