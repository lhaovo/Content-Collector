from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from sqlalchemy import Column
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


class FolderScan(SQLModel, table=True):
    __tablename__ = "folder_scans"

    id: str = Field(default_factory=lambda: new_id("scan"), primary_key=True)
    root_path: str = Field(index=True)
    file_count: int = 0
    status: str = Field(default="pending", index=True)
    scan_snapshot: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)


class PostGroupCandidate(SQLModel, table=True):
    __tablename__ = "post_group_candidates"

    id: str = Field(default_factory=lambda: new_id("candidate"), primary_key=True)
    scan_id: str = Field(index=True)
    source: str = Field(default="rule", index=True)
    confidence: float = Field(default=0.0, index=True)
    status: str = Field(default="pending", index=True)
    candidate_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    review_note: str = ""
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)


class Post(SQLModel, table=True):
    __tablename__ = "posts"

    id: str = Field(default_factory=lambda: new_id("post"), primary_key=True)
    scan_id: Optional[str] = Field(default=None, index=True)
    candidate_id: Optional[str] = Field(default=None, index=True)
    source_path: str = Field(index=True)
    title: str = ""
    status: str = Field(default="pending", index=True)
    content_hash: str = Field(default="", index=True)
    metadata_: dict = Field(default_factory=dict, sa_column=Column("metadata", JSON))
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)


class Asset(SQLModel, table=True):
    __tablename__ = "assets"

    id: str = Field(default_factory=lambda: new_id("asset"), primary_key=True)
    post_id: str = Field(index=True)
    asset_type: str = Field(index=True)
    path: str = Field(index=True)
    mime_type: str = ""
    file_name: str = ""
    file_size: int = 0
    file_hash: str = Field(default="", index=True)
    sort_order: int = 0
    role: str = "unknown"
    status: str = Field(default="pending", index=True)
    metadata_: dict = Field(default_factory=dict, sa_column=Column("metadata", JSON))
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)


class PostProcessingRun(SQLModel, table=True):
    __tablename__ = "post_processing_runs"

    id: str = Field(default_factory=lambda: new_id("run"), primary_key=True)
    post_id: str = Field(index=True)
    status: str = Field(default="queued", index=True)
    total_steps: int = 0
    completed_steps: int = 0
    failed_steps: int = 0
    current_step: str = ""
    error_message: str = ""
    metadata_: dict = Field(default_factory=dict, sa_column=Column("metadata", JSON))
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)


class ExtractionJob(SQLModel, table=True):
    __tablename__ = "extraction_jobs"

    id: str = Field(default_factory=lambda: new_id("job"), primary_key=True)
    post_id: str = Field(index=True)
    asset_id: Optional[str] = Field(default=None, index=True)
    model: str = ""
    input_type: str = Field(index=True)
    prompt_version: str = ""
    status: str = Field(default="pending", index=True)
    error_message: str = ""
    raw_response: dict = Field(default_factory=dict, sa_column=Column(JSON))
    metadata_: dict = Field(default_factory=dict, sa_column=Column("metadata", JSON))
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)


class ExtractedBlock(SQLModel, table=True):
    __tablename__ = "extracted_blocks"

    id: str = Field(default_factory=lambda: new_id("block"), primary_key=True)
    post_id: str = Field(index=True)
    asset_id: Optional[str] = Field(default=None, index=True)
    job_id: Optional[str] = Field(default=None, index=True)
    block_type: str = Field(index=True)
    text: str = ""
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    sort_order: int = 0
    confidence: Optional[float] = None
    metadata_: dict = Field(default_factory=dict, sa_column=Column("metadata", JSON))
    created_at: datetime = Field(default_factory=now_utc)


class PostDocument(SQLModel, table=True):
    __tablename__ = "post_documents"

    id: str = Field(default_factory=lambda: new_id("doc"), primary_key=True)
    post_id: str = Field(index=True)
    title: str = ""
    summary: str = ""
    body: list = Field(default_factory=list, sa_column=Column(JSON))
    outline: list = Field(default_factory=list, sa_column=Column(JSON))
    tags: list = Field(default_factory=list, sa_column=Column(JSON))
    entities: list = Field(default_factory=list, sa_column=Column(JSON))
    status: str = Field(default="draft", index=True)
    model: str = ""
    prompt_version: str = ""
    raw_response: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)