from __future__ import annotations

import hashlib
import mimetypes
import re
import shutil
import time
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

from sqlmodel import Session, select

from content_collector.models import Asset, ExtractedBlock, Post, now_utc
from content_collector.scanner import file_sha256, guess_mime
from content_collector.settings import settings

XLSX_NS = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
DOWNLOAD_STATUS = {"remote_pending", "download_failed"}


@dataclass(frozen=True)
class XlsxImportResult:
    total_rows: int
    created_posts: int
    updated_posts: int
    created_assets: int
    remote_assets: int


@dataclass(frozen=True)
class DownloadResult:
    total_assets: int
    downloaded: int
    skipped: int
    failed: int


def upload_root() -> Path:
    return _resolve(settings.content_collector_upload_dir) / "social-xlsx"


def save_uploaded_xlsx(filename: str, content: bytes) -> Path:
    suffix = Path(filename).suffix.lower()
    if suffix != ".xlsx":
        raise ValueError("只支持 .xlsx 文件")
    root = upload_root()
    root.mkdir(parents=True, exist_ok=True)
    safe_stem = re.sub(r"[^0-9A-Za-z._-]+", "_", Path(filename).stem).strip("._") or "social-export"
    target = root / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{safe_stem}.xlsx"
    target.write_bytes(content)
    return target


def import_social_xlsx(session: Session, xlsx_path: str | Path, platform: str = "xhs") -> XlsxImportResult:
    path = Path(xlsx_path).expanduser().resolve()
    if not path.exists() or path.suffix.lower() != ".xlsx":
        raise ValueError(f"不是有效的 xlsx 文件：{path}")

    rows = _read_xlsx(path)
    if not rows:
        return XlsxImportResult(0, 0, 0, 0, 0)

    headers = rows[0]
    data_rows = rows[1:]
    index = {name: pos for pos, name in enumerate(headers)}
    required = ["笔记ID", "笔记链接", "博主ID", "博主昵称", "笔记标题", "视频/图片下载链接"]
    missing = [name for name in required if name not in index]
    if missing:
        raise ValueError(f"xlsx 缺少字段：{', '.join(missing)}")

    created_posts = 0
    updated_posts = 0
    created_assets = 0
    remote_assets = 0

    for row_number, row in enumerate(data_rows, start=2):
        note_id = _cell(row, index, "笔记ID")
        author_id = _cell(row, index, "博主ID")
        title = _cell(row, index, "笔记标题") or note_id
        media_urls = _split_media_urls(_cell(row, index, "视频/图片下载链接"))
        content_hash = _post_hash(platform, author_id, note_id)

        existing = session.exec(select(Post).where(Post.content_hash == content_hash)).first()
        metadata = {
            **(existing.metadata_ if existing else {}),
            "source": "social-media-copilot-xlsx",
            "source_xlsx": path.as_posix(),
            "source_row": row_number,
            "platform": platform,
            "note_id": note_id,
            "note_url": _cell(row, index, "笔记链接"),
            "author_id": author_id,
            "author_url": _cell(row, index, "博主链接"),
            "author_name": _cell(row, index, "博主昵称"),
            "post_type": _cell(row, index, "笔记类型"),
            "stats": {
                "likes": _int_or_none(_cell(row, index, "点赞数")),
                "favorites": _int_or_none(_cell(row, index, "收藏数")),
                "comments": _int_or_none(_cell(row, index, "评论数")),
                "shares": _int_or_none(_cell(row, index, "分享数")),
            },
            "published_at": _excel_date(_cell(row, index, "发布时间")),
            "updated_at": _excel_date(_cell(row, index, "更新时间")),
            "ip_location": _cell(row, index, "IP地址"),
            "media_url_count": len(media_urls),
        }

        if existing:
            post = existing
            post.source_path = metadata["note_url"] or post.source_path
            post.title = title
            post.metadata_ = metadata
            post.updated_at = now_utc()
            updated_posts += 1
        else:
            post = Post(
                source_path=metadata["note_url"],
                title=title,
                status="pending",
                content_hash=content_hash,
                metadata_=metadata,
            )
            session.add(post)
            session.commit()
            session.refresh(post)
            created_posts += 1

        session.add(post)
        session.commit()
        _upsert_detail_block(session, post, _cell(row, index, "笔记详情"))

        existing_assets = session.exec(select(Asset).where(Asset.post_id == post.id)).all()
        existing_urls = {_remote_url(asset) for asset in existing_assets if _remote_url(asset)}
        for offset, media_url in enumerate(media_urls, start=1):
            if media_url in existing_urls:
                continue
            asset_type = _asset_type(media_url)
            session.add(
                Asset(
                    post_id=post.id,
                    asset_type=asset_type,
                    path=media_url,
                    file_name=_remote_file_name(note_id, offset, media_url, asset_type),
                    sort_order=offset,
                    role=asset_type,
                    status="remote_pending",
                    metadata_={"remote_url": media_url, "source": "xlsx-media-url"},
                )
            )
            created_assets += 1
            remote_assets += 1
        session.commit()

    return XlsxImportResult(
        total_rows=len(data_rows),
        created_posts=created_posts,
        updated_posts=updated_posts,
        created_assets=created_assets,
        remote_assets=remote_assets,
    )


def list_social_accounts(session: Session) -> list[dict]:
    posts = session.exec(select(Post).order_by(Post.updated_at.desc())).all()
    accounts: dict[tuple[str, str], dict] = {}
    for post in posts:
        metadata = post.metadata_ or {}
        platform = metadata.get("platform")
        author_id = metadata.get("author_id")
        if not platform or not author_id:
            continue
        key = (platform, author_id)
        item = accounts.setdefault(
            key,
            {
                "platform": platform,
                "author_id": author_id,
                "author_name": metadata.get("author_name") or author_id,
                "post_count": 0,
                "updated_at": post.updated_at,
            },
        )
        item["post_count"] += 1
        item["updated_at"] = max(item["updated_at"], post.updated_at)
    return sorted(accounts.values(), key=lambda item: item["updated_at"], reverse=True)


def list_social_posts(session: Session, platform: str, author_id: str) -> list[dict]:
    posts = session.exec(select(Post).order_by(Post.created_at.desc())).all()
    rows: list[dict] = []
    for post in posts:
        metadata = post.metadata_ or {}
        if metadata.get("platform") != platform or metadata.get("author_id") != author_id:
            continue
        assets = session.exec(select(Asset).where(Asset.post_id == post.id)).all()
        rows.append(
            {
                "post": post,
                "asset_count": len(assets),
                "downloaded_count": sum(1 for asset in assets if asset.status == "downloaded"),
                "pending_count": sum(1 for asset in assets if asset.status in DOWNLOAD_STATUS),
                "failed_count": sum(1 for asset in assets if asset.status == "download_failed"),
            }
        )
    return rows


def download_account_assets(session: Session, platform: str, author_id: str, delay_seconds: float = 2.0) -> DownloadResult:
    posts = session.exec(select(Post).order_by(Post.created_at.asc())).all()
    post_ids = [
        post.id
        for post in posts
        if (post.metadata_ or {}).get("platform") == platform and (post.metadata_ or {}).get("author_id") == author_id
    ]
    if not post_ids:
        return DownloadResult(0, 0, 0, 0)

    assets = session.exec(select(Asset).where(Asset.post_id.in_(post_ids)).order_by(Asset.sort_order)).all()
    pending = [asset for asset in assets if asset.status in DOWNLOAD_STATUS and _remote_url(asset)]
    downloaded = 0
    failed = 0
    skipped = len(assets) - len(pending)

    for asset in pending:
        post = session.get(Post, asset.post_id)
        if not post:
            skipped += 1
            continue
        try:
            local_path = _download_asset(post, asset)
            asset.path = local_path.as_posix()
            asset.file_name = local_path.name
            asset.file_size = local_path.stat().st_size
            asset.file_hash = file_sha256(local_path)
            asset.mime_type = guess_mime(local_path)
            asset.status = "downloaded"
            downloaded += 1
        except Exception as error:
            asset.status = "download_failed"
            asset.metadata_ = {**(asset.metadata_ or {}), "download_error": str(error)}
            failed += 1
        asset.updated_at = now_utc()
        session.add(asset)
        session.commit()
        if delay_seconds > 0:
            time.sleep(delay_seconds)

    return DownloadResult(len(assets), downloaded, skipped, failed)


def _download_asset(post: Post, asset: Asset) -> Path:
    metadata = post.metadata_ or {}
    platform = metadata.get("platform") or "unknown"
    author_id = metadata.get("author_id") or "unknown"
    note_id = metadata.get("note_id") or post.id
    url = _remote_url(asset)
    if not url:
        raise ValueError("素材没有远程链接")

    target_dir = _resolve(settings.content_collector_media_dir) / "social" / platform / author_id / note_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target = _unique_path(target_dir / (asset.file_name or _remote_file_name(note_id, asset.sort_order, url, asset.asset_type)))

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
            ),
            "Referer": metadata.get("note_url") or "https://www.xiaohongshu.com/",
            "Accept": "*/*",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        content_type = response.headers.get("Content-Type", "")
        with target.open("wb") as file:
            shutil.copyfileobj(response, file)

    if target.suffix == "":
        extension = mimetypes.guess_extension(content_type.split(";")[0].strip())
        if extension:
            renamed = _unique_path(target.with_suffix(extension))
            target.rename(renamed)
            target = renamed
    return target


def _read_xlsx(path: Path) -> list[list[str]]:
    with zipfile.ZipFile(path) as archive:
        shared = _shared_strings(archive)
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        rows: list[list[str]] = []
        for row in sheet.findall(".//a:sheetData/a:row", XLSX_NS):
            values: list[str] = []
            for cell in row.findall("a:c", XLSX_NS):
                idx = _column_index(cell.attrib.get("r", "A1"))
                while len(values) <= idx:
                    values.append("")
                value_node = cell.find("a:v", XLSX_NS)
                value = "" if value_node is None or value_node.text is None else value_node.text
                if cell.attrib.get("t") == "s" and value:
                    value = shared[int(value)]
                values[idx] = value
            rows.append(values)
        return rows


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    return ["".join(text.text or "" for text in item.findall(".//a:t", XLSX_NS)) for item in root.findall("a:si", XLSX_NS)]


def _column_index(reference: str) -> int:
    letters = "".join(char for char in reference if char.isalpha())
    value = 0
    for char in letters:
        value = value * 26 + ord(char.upper()) - 64
    return value - 1


def _cell(row: list[str], index: dict[str, int], name: str) -> str:
    pos = index.get(name)
    if pos is None or pos >= len(row):
        return ""
    return str(row[pos]).strip()


def _split_media_urls(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"\s+", value or "") if part.strip().startswith(("http://", "https://"))]


def _post_hash(platform: str, author_id: str, note_id: str) -> str:
    return hashlib.sha256(f"{platform}:{author_id}:{note_id}".encode("utf-8")).hexdigest()


def _upsert_detail_block(session: Session, post: Post, detail: str) -> None:
    if not detail:
        return
    existing = session.exec(
        select(ExtractedBlock).where(
            ExtractedBlock.post_id == post.id,
            ExtractedBlock.block_type == "social_note_detail",
        )
    ).first()
    if existing:
        existing.text = detail
        existing.metadata_ = {**(existing.metadata_ or {}), "source": "xlsx"}
        session.add(existing)
    else:
        session.add(
            ExtractedBlock(
                post_id=post.id,
                block_type="social_note_detail",
                text=detail,
                sort_order=0,
                confidence=1.0,
                metadata_={"source": "xlsx"},
            )
        )
    session.commit()


def _remote_url(asset: Asset) -> str:
    return (asset.metadata_ or {}).get("remote_url") or (asset.path if asset.path.startswith(("http://", "https://")) else "")


def _asset_type(url: str) -> str:
    lower_url = url.lower()
    path = urllib.parse.urlparse(url).path.lower()
    if ".mp4" in path or "video" in lower_url:
        return "video"
    if any(ext in path for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif")) or "sns-img" in lower_url:
        return "image"
    return "unknown"


def _remote_file_name(note_id: str, offset: int, url: str, asset_type: str) -> str:
    suffix = Path(urllib.parse.urlparse(url).path).suffix
    if not suffix:
        suffix = ".mp4" if asset_type == "video" else ".jpg" if asset_type == "image" else ".bin"
    return f"{note_id}-{offset:03d}{suffix}"


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    counter = 2
    while True:
        candidate = path.with_name(f"{path.stem}-{counter}{path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def _int_or_none(value: str) -> int | None:
    if not value:
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def _excel_date(value: str) -> str:
    if not value:
        return ""
    try:
        number = float(value)
    except ValueError:
        return value
    converted = datetime(1899, 12, 30, tzinfo=timezone.utc) + timedelta(days=number)
    return converted.isoformat()


def _resolve(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()
