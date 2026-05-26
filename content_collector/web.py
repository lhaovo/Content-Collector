from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from content_collector.database import get_session, new_session
from content_collector.models import Asset, ExtractedBlock, ExtractionJob, FolderScan, Post, PostDocument, PostGroupCandidate, PostProcessingRun, now_utc
from content_collector.workflows import ImportWorkflow

router = APIRouter()

STATUS_LABELS = {
    "pending": "待处理",
    "queued": "排队中",
    "processing": "处理中",
    "scanned": "已扫描",
    "accepted": "已确认",
    "running": "处理中",
    "completed": "已完成",
    "failed": "失败",
    "partial_failed": "部分失败",
    "assembled": "已整理",
    "draft": "草稿",
}

ASSET_TYPE_LABELS = {
    "text": "文本",
    "image": "图片",
    "video": "视频",
    "audio": "音频",
    "document": "文档",
    "unknown": "未知",
}

ROLE_LABELS = {
    "main_text": "正文",
    "image": "图片",
    "video": "视频",
    "audio": "音频",
    "document": "文档",
    "unknown": "未知",
}

BLOCK_TYPE_LABELS = {
    "text_extract": "文本抽取",
    "image_extract": "图片抽取",
    "video_extract": "视频抽取",
    "file_extract": "文件抽取",
}


COMMON_LABELS = {
    "status_labels": STATUS_LABELS,
    "asset_type_labels": ASSET_TYPE_LABELS,
    "role_labels": ROLE_LABELS,
    "block_type_labels": BLOCK_TYPE_LABELS,
}


def page_context(**values):
    return {**COMMON_LABELS, **values}


def build_run_progress(session: Session, runs: list[PostProcessingRun]) -> list[dict]:
    if not runs:
        return []

    post_ids = [run.post_id for run in runs]
    posts = session.exec(select(Post).where(Post.id.in_(post_ids))).all()
    assets = session.exec(select(Asset).where(Asset.post_id.in_(post_ids))).all()
    jobs = session.exec(select(ExtractionJob).where(ExtractionJob.post_id.in_(post_ids))).all()
    post_by_id = {post.id: post for post in posts}
    assets_by_post = defaultdict(list)
    jobs_by_post = defaultdict(list)

    for asset in assets:
        assets_by_post[asset.post_id].append(asset)
    for job in jobs:
        jobs_by_post[job.post_id].append(job)

    rows = []
    for run in runs:
        post = post_by_id.get(run.post_id)
        if not post:
            continue
        post_assets = assets_by_post[run.post_id]
        post_jobs = jobs_by_post[run.post_id]
        asset_total = len(post_assets) or run.total_steps
        completed_assets = sum(1 for asset in post_assets if asset.status == "completed")
        failed_assets = sum(1 for asset in post_assets if asset.status == "failed")
        running_jobs = sum(1 for job in post_jobs if job.status == "running")
        is_active = run.status in {"queued", "processing"} or running_jobs > 0
        display_failed_steps = failed_assets
        display_completed_steps = completed_assets
        progress = int((display_completed_steps / max(asset_total, 1)) * 100)

        if is_active:
            stage = "处理中" if run.status == "processing" or running_jobs else "排队中"
        elif display_failed_steps:
            stage = "部分失败"
            progress = 100
        else:
            stage = "已完成"
            progress = 100

        rows.append(
            {
                "run": run,
                "post": post,
                "stage": stage,
                "progress": progress,
                "is_active": is_active,
                "display_total_steps": asset_total,
                "display_completed_steps": display_completed_steps,
                "display_failed_steps": display_failed_steps,
            }
        )
    return rows


def build_post_progress(session: Session, posts: list[Post]) -> list[dict]:
    if not posts:
        return []

    post_ids = [post.id for post in posts]
    assets = session.exec(select(Asset).where(Asset.post_id.in_(post_ids))).all()
    jobs = session.exec(select(ExtractionJob).where(ExtractionJob.post_id.in_(post_ids))).all()
    documents = session.exec(select(PostDocument).where(PostDocument.post_id.in_(post_ids))).all()

    assets_by_post = defaultdict(list)
    jobs_by_post = defaultdict(list)
    documents_by_post = defaultdict(list)

    for asset in assets:
        assets_by_post[asset.post_id].append(asset)
    for job in jobs:
        jobs_by_post[job.post_id].append(job)
    for document in documents:
        documents_by_post[document.post_id].append(document)

    rows = []
    for post in posts:
        post_assets = assets_by_post[post.id]
        post_jobs = jobs_by_post[post.id]
        total_steps = len(post_assets) + 1
        completed_assets = sum(1 for asset in post_assets if asset.status == "completed")
        has_document = len(documents_by_post[post.id]) > 0
        failed_jobs = sum(1 for job in post_jobs if job.status == "failed")
        running_jobs = sum(1 for job in post_jobs if job.status == "running")
        is_active = post.status in {"queued", "processing"} or running_jobs > 0
        completed_steps = completed_assets + (1 if post.status == "completed" and has_document else 0)
        progress = 100 if post.status in {"completed", "partial_failed"} else int((completed_steps / max(total_steps, 1)) * 100)

        if post.status == "completed":
            stage = "已完成"
        elif post.status == "partial_failed" or failed_jobs:
            stage = "部分失败"
        elif running_jobs or post.status == "processing":
            stage = "处理中"
        elif post.status == "queued":
            stage = "排队中"
        else:
            stage = "待处理"

        rows.append(
            {
                "post": post,
                "asset_count": len(post_assets),
                "completed_assets": completed_assets,
                "job_count": len(post_jobs),
                "failed_jobs": failed_jobs,
                "is_active": is_active,
                "stage": stage,
                "progress": progress,
            }
        )
    return rows


def _extract_post_task(post_id: str, run_id: str | None = None) -> None:
    with new_session() as session:
        ImportWorkflow(session).extract_post(post_id, run_id=run_id)


def create_processing_run(session: Session, post: Post) -> PostProcessingRun:
    asset_count = len(session.exec(select(Asset).where(Asset.post_id == post.id)).all())
    run = PostProcessingRun(
        post_id=post.id,
        status="queued",
        total_steps=asset_count,
        current_step="排队中",
    )
    post.status = "queued"
    post.updated_at = now_utc()
    session.add(run)
    session.add(post)
    session.commit()
    session.refresh(run)
    return run


def delete_post_tree(session: Session, post_id: str) -> None:
    for model in (ExtractedBlock, ExtractionJob, PostDocument, PostProcessingRun, Asset):
        items = session.exec(select(model).where(model.post_id == post_id)).all()
        for item in items:
            session.delete(item)
    post = session.get(Post, post_id)
    if post:
        session.delete(post)


def templates(request: Request):
    return request.app.state.templates


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: Session = Depends(get_session)):
    unimported_count = len(session.exec(select(PostGroupCandidate).where(PostGroupCandidate.status == "pending")).all())
    imported_count = len(session.exec(select(Post).where(Post.status != "completed")).all())
    processed_count = len(session.exec(select(Post).where(Post.status == "completed")).all())
    stats = {
        "未入库候选": unimported_count,
        "已入库待处理": imported_count,
        "已处理内容": processed_count,
        "失败任务": len(session.exec(select(ExtractionJob).where(ExtractionJob.status == "failed")).all()),
    }
    scans = session.exec(select(FolderScan).order_by(FolderScan.created_at.desc()).limit(5)).all()
    return templates(request).TemplateResponse(request, "dashboard.html", page_context(stats=stats, scans=scans))


@router.get("/pick-directory")
def pick_directory():
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        selected = filedialog.askdirectory(title="选择要导入的文件夹", mustexist=True)
        return {"path": selected}
    finally:
        root.destroy()


@router.post("/imports")
def import_folder(root_path: str = Form(...), auto_extract: bool = Form(False), session: Session = Depends(get_session)):
    ImportWorkflow(session).run(root_path, auto_extract=auto_extract)
    return RedirectResponse("/posts", status_code=303)


@router.post("/candidates/{candidate_id}/accept")
def accept_candidate(candidate_id: str, session: Session = Depends(get_session)):
    candidate = session.get(PostGroupCandidate, candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="candidate not found")
    candidate.status = "accepted"
    session.add(candidate)
    session.commit()
    scan = session.get(FolderScan, candidate.scan_id)
    if scan:
        ImportWorkflow(session)._accept_high_confidence(scan, Path(scan.root_path))
    return RedirectResponse("/posts", status_code=303)


@router.get("/posts", response_class=HTMLResponse)
def posts(request: Request, tab: str = "unimported", session: Session = Depends(get_session)):
    active_tab = tab if tab in {"unimported", "imported", "processed"} else "unimported"
    imported_posts = session.exec(
        select(Post).where(Post.status != "completed").order_by(Post.created_at.desc())
    ).all()
    processed_posts = session.exec(
        select(Post).where(Post.status == "completed").order_by(Post.updated_at.desc())
    ).all()
    candidates = session.exec(select(PostGroupCandidate).where(PostGroupCandidate.status == "pending").limit(50)).all()
    return templates(request).TemplateResponse(
        request,
        "posts.html",
        page_context(
            active_tab=active_tab,
            imported_posts=imported_posts,
            processed_posts=processed_posts,
            candidates=candidates,
        ),
    )


@router.post("/posts/bulk")
def bulk_posts(
    background_tasks: BackgroundTasks,
    action: str = Form(...),
    tab: str = Form("unimported"),
    candidate_ids: list[str] = Form(default=[]),
    post_ids: list[str] = Form(default=[]),
    session: Session = Depends(get_session),
):
    active_tab = tab if tab in {"unimported", "imported", "processed"} else "unimported"

    if action == "delete":
        for candidate_id in candidate_ids:
            candidate = session.get(PostGroupCandidate, candidate_id)
            if candidate:
                session.delete(candidate)
        for post_id in post_ids:
            delete_post_tree(session, post_id)
        session.commit()
        return RedirectResponse(f"/posts?tab={active_tab}", status_code=303)

    if action == "process":
        for post_id in post_ids:
            post = session.get(Post, post_id)
            if not post:
                continue
            run = create_processing_run(session, post)
            background_tasks.add_task(_extract_post_task, post_id, run.id)
        session.commit()
        return RedirectResponse("/jobs", status_code=303)

    raise HTTPException(status_code=400, detail="unsupported bulk action")


@router.get("/posts/{post_id}", response_class=HTMLResponse)
def post_detail(post_id: str, request: Request, session: Session = Depends(get_session)):
    post = session.get(Post, post_id)
    assets = session.exec(select(Asset).where(Asset.post_id == post_id).order_by(Asset.sort_order)).all()
    blocks = session.exec(select(ExtractedBlock).where(ExtractedBlock.post_id == post_id).order_by(ExtractedBlock.sort_order)).all()
    documents = session.exec(select(PostDocument).where(PostDocument.post_id == post_id).order_by(PostDocument.created_at.desc())).all()
    asset_by_id = {asset.id: asset for asset in assets}
    return templates(request).TemplateResponse(
        request,
        "post_detail.html",
        page_context(post=post, assets=assets, asset_by_id=asset_by_id, blocks=blocks, documents=documents),
    )


@router.post("/posts/{post_id}/extract")
def extract_post(post_id: str, background_tasks: BackgroundTasks, session: Session = Depends(get_session)):
    post = session.get(Post, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="post not found")
    run = create_processing_run(session, post)
    background_tasks.add_task(_extract_post_task, post_id, run.id)
    return RedirectResponse("/jobs", status_code=303)


@router.get("/jobs", response_class=HTMLResponse)
def jobs(request: Request, session: Session = Depends(get_session)):
    runs = session.exec(select(PostProcessingRun).order_by(PostProcessingRun.created_at.desc()).limit(200)).all()
    post_progress = build_run_progress(session, runs)
    recent_jobs = session.exec(select(ExtractionJob).order_by(ExtractionJob.created_at.desc()).limit(50)).all()
    return templates(request).TemplateResponse(
        request,
        "jobs.html",
        page_context(post_progress=post_progress, recent_jobs=recent_jobs),
    )