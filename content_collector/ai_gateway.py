from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from content_collector.schemas import AssembledDocument, ExtractedAssetResult, FileInventory, GroupingResult
from content_collector.settings import settings


class AIGateway:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key if api_key is not None else settings.zhipu_api_key
        self.model = model or settings.content_collector_model
        self._client = None

    @property
    def client(self):
        if not self.api_key:
            return None
        if self._client is None:
            from zai import ZhipuAiClient

            self._client = ZhipuAiClient(api_key=self.api_key)
        return self._client

    def is_enabled(self) -> bool:
        return self.client is not None

    def group_folder(self, inventory: FileInventory) -> GroupingResult | None:
        if not self.is_enabled():
            return None
        response = self._chat_text(
            system="你是文件夹内容组织助手。只输出严格 JSON。",
            text=f"请根据这个 FileInventory 生成候选帖子分组：\n{inventory.model_dump_json()}",
            thinking=False,
        )
        data = _loads_json(response)
        return GroupingResult.model_validate(data)

    def extract_text_asset(self, path: Path) -> ExtractedAssetResult:
        text = path.read_text(encoding="utf-8", errors="ignore")
        return ExtractedAssetResult(block_type="text_extract", text=text, metadata={"source": "local_text"})

    def extract_image(self, path: Path) -> ExtractedAssetResult:
        if not self.is_enabled():
            return ExtractedAssetResult(block_type="image_extract", text="", metadata={"skipped": "missing_api_key"})
        encoded = base64.b64encode(path.read_bytes()).decode("utf-8")
        response = self._chat_multimodal([
            {"type": "image_url", "image_url": {"url": encoded}},
            {"type": "text", "text": "请提取图片文字、画面信息和社媒整理可用信息，输出严格 JSON。"},
        ])
        return _asset_result(response, "image_extract")

    def extract_video(self, path: Path) -> ExtractedAssetResult:
        if not self.is_enabled():
            return ExtractedAssetResult(block_type="video_extract", text="", metadata={"skipped": "missing_api_key"})
        return ExtractedAssetResult(block_type="video_extract", text="", metadata={"skipped": "local_video_url_required", "path": str(path)})

    def extract_file(self, path: Path) -> ExtractedAssetResult:
        if path.suffix.lower() in {".txt", ".md", ".markdown", ".html", ".htm", ".json", ".csv"}:
            return self.extract_text_asset(path)
        if not self.is_enabled():
            return ExtractedAssetResult(block_type="file_extract", text="", metadata={"skipped": "missing_api_key"})
        return ExtractedAssetResult(block_type="file_extract", text="", metadata={"skipped": "local_file_url_required", "path": str(path)})

    def assemble_post(self, title: str, blocks: list[dict[str, Any]]) -> AssembledDocument:
        if not self.is_enabled():
            body = [{"type": block.get("block_type", "paragraph"), "text": block.get("text", "")} for block in blocks]
            summary = "\n".join(block.get("text", "")[:200] for block in blocks if block.get("text"))[:500]
            return AssembledDocument(title=title or "未命名帖子", summary=summary, body=body)
        response = self._chat_text(
            system="你是社媒内容整理助手。只输出严格 JSON。",
            text=f"请组织为结构化帖子文档。标题候选：{title}\n内容块：{json.dumps(blocks, ensure_ascii=False)}",
            thinking=False,
        )
        data = _loads_json(response)
        return AssembledDocument.model_validate(data)

    def _chat_text(self, system: str, text: str, thinking: bool = False) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": text},
            ],
            thinking={"type": "enabled" if thinking else "disabled"},
        )
        return response.choices[0].message.content

    def _chat_multimodal(self, content: list[dict[str, Any]]) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": content}],
            thinking={"type": "disabled"},
        )
        return response.choices[0].message.content


def _loads_json(text: str) -> Any:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.removeprefix("json").strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end >= start:
        cleaned = cleaned[start : end + 1]
    return json.loads(cleaned)


def _asset_result(response: str, fallback_type: str) -> ExtractedAssetResult:
    try:
        data = _loads_json(response)
        return ExtractedAssetResult.model_validate(data)
    except Exception:
        return ExtractedAssetResult(block_type=fallback_type, text=response, metadata={"parser": "fallback"})