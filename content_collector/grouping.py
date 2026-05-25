from __future__ import annotations

from collections import defaultdict
from pathlib import PurePosixPath

from content_collector.schemas import CandidateFile, CandidateGroup, FileInventory, GroupingResult

ROLE_BY_TYPE = {
    "text": "main_text",
    "image": "image",
    "video": "video",
    "audio": "audio",
    "document": "document",
}


def group_by_rules(inventory: FileInventory) -> GroupingResult:
    buckets: dict[str, list] = defaultdict(list)
    for item in inventory.files:
        parent = PurePosixPath(item.path).parent.as_posix()
        key = parent if parent != "." else PurePosixPath(item.path).stem.split("_")[0]
        buckets[key].append(item)

    groups: list[CandidateGroup] = []
    unassigned: list[str] = []
    needs_review: list[str] = []

    for key, items in buckets.items():
        if len(items) == 1 and items[0].asset_type == "unknown":
            unassigned.append(items[0].path)
            continue

        has_text_like = any(item.asset_type in {"text", "document"} for item in items)
        has_media = any(item.asset_type in {"image", "video", "audio"} for item in items)
        is_named_folder = key not in {".", ""}

        if len(items) > 1 and (has_text_like or has_media):
            confidence = 0.9
        elif is_named_folder and has_text_like:
            confidence = 0.88
        elif is_named_folder and has_media:
            confidence = 0.82
        else:
            confidence = 0.65

        if not is_named_folder:
            confidence -= 0.15
        if any(item.asset_type == "unknown" for item in items):
            confidence -= 0.12

        files = [
            CandidateFile(
                path=item.path,
                role=ROLE_BY_TYPE.get(item.asset_type, "unknown"),
                sort_order=index,
            )
            for index, item in enumerate(sorted(items, key=lambda file: file.path), start=1)
        ]
        group = CandidateGroup(
            group_name=key,
            title=key.replace("_", " ").strip("/"),
            files=files,
            confidence=max(0.0, min(confidence, 0.98)),
            reason="基于目录、文件类型和命名连续性生成",
        )
        groups.append(group)
        if group.confidence < 0.8:
            needs_review.extend(file.path for file in files)

    return GroupingResult(groups=groups, needs_review=needs_review, unassigned_files=unassigned)