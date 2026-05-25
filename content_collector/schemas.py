from __future__ import annotations

from pydantic import BaseModel, Field


class FileItem(BaseModel):
    path: str
    name: str
    extension: str
    mime: str
    size: int
    modified_at: str
    asset_type: str
    text_preview: str = ""
    width: int | None = None
    height: int | None = None


class FileInventory(BaseModel):
    root: str
    files: list[FileItem] = Field(default_factory=list)


class CandidateFile(BaseModel):
    path: str
    role: str = "unknown"
    sort_order: int = 0


class CandidateGroup(BaseModel):
    group_name: str
    post_type: str = "social_post"
    title: str = ""
    files: list[CandidateFile] = Field(default_factory=list)
    confidence: float = 0.0
    reason: str = ""


class GroupingResult(BaseModel):
    groups: list[CandidateGroup] = Field(default_factory=list)
    needs_review: list[str] = Field(default_factory=list)
    unassigned_files: list[str] = Field(default_factory=list)


class ExtractedAssetResult(BaseModel):
    block_type: str
    text: str
    metadata: dict = Field(default_factory=dict)


class AssembledDocument(BaseModel):
    title: str
    summary: str = ""
    body: list[dict] = Field(default_factory=list)
    outline: list[dict] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    entities: list[dict] = Field(default_factory=list)