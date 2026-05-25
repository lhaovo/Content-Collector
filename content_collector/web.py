from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from content_collector.database import get_session
from content_collector.models import Asset, ExtractedBlock, ExtractionJob, FolderScan, Post, PostDocument, PostGroupCandidate
from content_collector.workflows import ImportWorkflow

router = APIRouter()


def templates(request: Request):
    return request.app.state.templates


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: Session = Depends(get_session)):
    stats = {
        "posts": len(session.exec(select(Post)).all()),
        "assets": len(session.exec(select(Asset)).all()),
        "jobs": len(session.exec(select(ExtractionJob)).all()),
        "failed_jobs": len(session.exec(select(ExtractionJob).where(ExtractionJob.status == "failed")).all()),
    }
    scans = session.exec(select(FolderScan).order_by(FolderScan.created_at.desc()).limit(5)).all()
    return templates(request).TemplateResponse("dashboard.html", {"request": request, "stats": stats, "scans": scans})


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
def posts(request: Request, session: Session = Depends(get_session)):
    items = session.exec(select(Post).order_by(Post.created_at.desc())).all()
    candidates = session.exec(select(PostGroupCandidate).where(PostGroupCandidate.status == "pending").limit(50)).all()
    return templates(request).TemplateResponse("posts.html", {"request": request, "posts": items, "candidates": candidates})


@router.get("/posts/{post_id}", response_class=HTMLResponse)
def post_detail(post_id: str, request: Request, session: Session = Depends(get_session)):
    post = session.get(Post, post_id)
    assets = session.exec(select(Asset).where(Asset.post_id == post_id).order_by(Asset.sort_order)).all()
    blocks = session.exec(select(ExtractedBlock).where(ExtractedBlock.post_id == post_id).order_by(ExtractedBlock.sort_order)).all()
    documents = session.exec(select(PostDocument).where(PostDocument.post_id == post_id).order_by(PostDocument.created_at.desc())).all()
    return templates(request).TemplateResponse(
        "post_detail.html",
        {"request": request, "post": post, "assets": assets, "blocks": blocks, "documents": documents},
    )


@router.post("/posts/{post_id}/extract")
def extract_post(post_id: str, session: Session = Depends(get_session)):
    ImportWorkflow(session).extract_post(post_id)
    return RedirectResponse(f"/posts/{post_id}", status_code=303)


@router.get("/jobs", response_class=HTMLResponse)
def jobs(request: Request, session: Session = Depends(get_session)):
    items = session.exec(select(ExtractionJob).order_by(ExtractionJob.created_at.desc()).limit(200)).all()
    return templates(request).TemplateResponse("jobs.html", {"request": request, "jobs": items})