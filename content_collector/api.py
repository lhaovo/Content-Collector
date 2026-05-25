from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException
from sqlmodel import Session, select

from content_collector.database import get_session, new_session
from content_collector.models import Asset, ExtractedBlock, ExtractionJob, FolderScan, Post, PostDocument, PostGroupCandidate
from content_collector.workflows import ImportWorkflow

router = APIRouter(prefix="/api")


@router.post("/imports")
def create_import(
    background_tasks: BackgroundTasks,
    root_path: str = Form(...),
    auto_extract: bool = Form(False),
    session: Session = Depends(get_session),
):
    workflow = ImportWorkflow(session)
    scan = workflow.run(root_path=root_path, auto_extract=False)
    if auto_extract:
        background_tasks.add_task(_extract_scan_task, scan.id)
    return {"scan_id": scan.id, "file_count": scan.file_count, "status": scan.status}


@router.get("/scans")
def list_scans(session: Session = Depends(get_session)):
    scans = session.exec(select(FolderScan).order_by(FolderScan.created_at.desc())).all()
    return scans


@router.get("/candidates")
def list_candidates(session: Session = Depends(get_session)):
    candidates = session.exec(select(PostGroupCandidate).order_by(PostGroupCandidate.created_at.desc())).all()
    return candidates


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
        ImportWorkflow(session)._accept_high_confidence(scan, __import__("pathlib").Path(scan.root_path))
    return {"ok": True}


@router.get("/posts")
def list_posts(session: Session = Depends(get_session)):
    return session.exec(select(Post).order_by(Post.created_at.desc())).all()


@router.get("/posts/{post_id}")
def get_post(post_id: str, session: Session = Depends(get_session)):
    post = session.get(Post, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="post not found")
    assets = session.exec(select(Asset).where(Asset.post_id == post_id).order_by(Asset.sort_order)).all()
    blocks = session.exec(select(ExtractedBlock).where(ExtractedBlock.post_id == post_id).order_by(ExtractedBlock.sort_order)).all()
    documents = session.exec(select(PostDocument).where(PostDocument.post_id == post_id).order_by(PostDocument.created_at.desc())).all()
    return {"post": post, "assets": assets, "blocks": blocks, "documents": documents}


@router.post("/posts/{post_id}/extract")
def extract_post(post_id: str, background_tasks: BackgroundTasks):
    background_tasks.add_task(_extract_post_task, post_id)
    return {"ok": True, "post_id": post_id}


@router.get("/jobs")
def list_jobs(session: Session = Depends(get_session)):
    return session.exec(select(ExtractionJob).order_by(ExtractionJob.created_at.desc()).limit(200)).all()


def _extract_scan_task(scan_id: str) -> None:
    with new_session() as session:
        ImportWorkflow(session).extract_scan(scan_id)


def _extract_post_task(post_id: str) -> None:
    with new_session() as session:
        ImportWorkflow(session).extract_post(post_id)