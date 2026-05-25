from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable

from sqlmodel import Session, select

from content_collector.ai_gateway import AIGateway
from content_collector.grouping import group_by_rules
from content_collector.models import (
    Asset,
    ExtractedBlock,
    ExtractionJob,
    FolderScan,
    Post,
    PostDocument,
    PostGroupCandidate,
    now_utc,
)
from content_collector.scanner import file_sha256, guess_asset_type, guess_mime, scan_folder
from content_collector.schemas import CandidateGroup, FileInventory, GroupingResult
from content_collector.settings import settings


def inventory_hash(inventory: FileInventory) -> str:
    digest = hashlib.sha256()
    for file in inventory.files:
        digest.update(file.model_dump_json().encode("utf-8"))
    return digest.hexdigest()


class ImportWorkflow:
    def __init__(self, session: Session, ai: AIGateway | None = None):
        self.session = session
        self.ai = ai or AIGateway()

    def run(self, root_path: str, auto_extract: bool = False) -> FolderScan:
        root = Path(root_path).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise ValueError(f"输入路径不是有效文件夹：{root}")

        inventory = scan_folder(root)
        scan = FolderScan(
            root_path=root.as_posix(),
            file_count=len(inventory.files),
            status="scanned",
            scan_snapshot=inventory.model_dump(),
        )
        self.session.add(scan)
        self.session.commit()
        self.session.refresh(scan)

        grouping = group_by_rules(inventory)
        if settings.content_collector_enable_ai_grouping and grouping.needs_review:
            ai_grouping = self.ai.group_folder(inventory)
            if ai_grouping:
                grouping = merge_groupings(grouping, ai_grouping)

        self._save_candidates(scan, grouping)
        self._accept_high_confidence(scan, root)

        if auto_extract:
            self.extract_scan(scan.id)
        return scan

    def extract_scan(self, scan_id: str) -> None:
        posts = self.session.exec(select(Post).where(Post.scan_id == scan_id)).all()
        for post in posts:
            self.extract_post(post.id)

    def extract_post(self, post_id: str) -> None:
        post = self.session.get(Post, post_id)
        if not post:
            raise ValueError(f"帖子不存在：{post_id}")
        assets = self.session.exec(select(Asset).where(Asset.post_id == post_id).order_by(Asset.sort_order)).all()
        blocks: list[ExtractedBlock] = []

        for asset in assets:
            job = ExtractionJob(
                post_id=post.id,
                asset_id=asset.id,
                model=settings.content_collector_model,
                input_type=asset.asset_type,
                prompt_version=f"{asset.asset_type}_extract_v1",
                status="running",
                started_at=now_utc(),
            )
            self.session.add(job)
            self.session.commit()
            self.session.refresh(job)

            try:
                result = self._extract_asset(asset)
                job.status = "completed"
                job.finished_at = now_utc()
                job.raw_response = result.model_dump()
                asset.status = "completed"
                block = ExtractedBlock(
                    post_id=post.id,
                    asset_id=asset.id,
                    job_id=job.id,
                    block_type=result.block_type,
                    text=result.text,
                    sort_order=asset.sort_order,
                    metadata_=result.metadata,
                )
                self.session.add(block)
                blocks.append(block)
            except Exception as error:
                job.status = "failed"
                job.error_message = str(error)
                job.finished_at = now_utc()
                asset.status = "failed"

            job.updated_at = now_utc()
            asset.updated_at = now_utc()
            self.session.add(job)
            self.session.add(asset)
            self.session.commit()

        persisted_blocks = self.session.exec(select(ExtractedBlock).where(ExtractedBlock.post_id == post.id)).all()
        block_payloads = [
            {
                "asset_id": block.asset_id,
                "block_type": block.block_type,
                "text": block.text,
                "metadata": block.metadata_,
                "sort_order": block.sort_order,
            }
            for block in sorted(persisted_blocks, key=lambda item: item.sort_order)
        ]
        document = self.ai.assemble_post(post.title, block_payloads)
        post_doc = PostDocument(
            post_id=post.id,
            title=document.title,
            summary=document.summary,
            body=document.body,
            outline=document.outline,
            tags=document.tags,
            entities=document.entities,
            status="assembled",
            model=settings.content_collector_model,
            prompt_version="post_assembly_v1",
            raw_response=document.model_dump(),
        )
        post.status = "completed"
        post.updated_at = now_utc()
        self.session.add(post_doc)
        self.session.add(post)
        self.session.commit()

    def _save_candidates(self, scan: FolderScan, grouping: GroupingResult) -> None:
        for group in grouping.groups:
            status = "accepted" if group.confidence >= settings.content_collector_auto_accept_confidence else "pending"
            candidate = PostGroupCandidate(
                scan_id=scan.id,
                source="rule",
                confidence=group.confidence,
                status=status,
                candidate_json=group.model_dump(),
            )
            self.session.add(candidate)
        self.session.commit()

    def _accept_high_confidence(self, scan: FolderScan, root: Path) -> None:
        candidates = self.session.exec(
            select(PostGroupCandidate).where(
                PostGroupCandidate.scan_id == scan.id,
                PostGroupCandidate.status == "accepted",
            )
        ).all()
        inventory = FileInventory.model_validate(scan.scan_snapshot)
        item_by_path = {item.path: item for item in inventory.files}

        for candidate in candidates:
            exists = self.session.exec(select(Post).where(Post.candidate_id == candidate.id)).first()
            if exists:
                continue
            group = CandidateGroup.model_validate(candidate.candidate_json)
            paths = [file.path for file in group.files]
            content_hash = hashlib.sha256("\n".join(sorted(paths)).encode("utf-8")).hexdigest()
            post = Post(
                scan_id=scan.id,
                candidate_id=candidate.id,
                source_path=(root / group.group_name).as_posix() if group.group_name not in {".", ""} else root.as_posix(),
                title=group.title or group.group_name,
                status="pending",
                content_hash=content_hash,
                metadata_={"post_type": group.post_type, "reason": group.reason},
            )
            self.session.add(post)
            self.session.commit()
            self.session.refresh(post)

            for file in group.files:
                item = item_by_path.get(file.path)
                absolute = root / file.path
                asset = Asset(
                    post_id=post.id,
                    asset_type=item.asset_type if item else guess_asset_type(absolute),
                    path=absolute.as_posix(),
                    mime_type=item.mime if item else guess_mime(absolute),
                    file_name=absolute.name,
                    file_size=item.size if item else absolute.stat().st_size,
                    file_hash=file_sha256(absolute) if absolute.exists() else "",
                    sort_order=file.sort_order,
                    role=file.role,
                    status="pending",
                    metadata_={"relative_path": file.path},
                )
                self.session.add(asset)
            self.session.commit()

    def _extract_asset(self, asset: Asset):
        path = Path(asset.path)
        if asset.asset_type == "text":
            return self.ai.extract_text_asset(path)
        if asset.asset_type == "image":
            return self.ai.extract_image(path)
        if asset.asset_type == "video":
            return self.ai.extract_video(path)
        if asset.asset_type in {"document", "audio"}:
            return self.ai.extract_file(path)
        return self.ai.extract_file(path)


def merge_groupings(rule_grouping: GroupingResult, ai_grouping: GroupingResult) -> GroupingResult:
    rule_high = [group for group in rule_grouping.groups if group.confidence >= settings.content_collector_auto_accept_confidence]
    used_paths = {file.path for group in rule_high for file in group.files}
    ai_groups = []
    for group in ai_grouping.groups:
        files = [file for file in group.files if file.path not in used_paths]
        if files:
            group.files = files
            ai_groups.append(group)
    return GroupingResult(
        groups=[*rule_high, *ai_groups],
        needs_review=ai_grouping.needs_review,
        unassigned_files=ai_grouping.unassigned_files,
    )