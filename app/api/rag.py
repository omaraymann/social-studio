from pathlib import PurePath
from urllib.parse import urlparse

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from app.config import settings
from app.core.security import current_user
from app.models import User
from rag.ingestion import get_job, list_user_documents, list_user_jobs, process_upload, start_job
from rag.pipeline import search_user_chunks
from rag.vector_store import connect_rag_database, ensure_studio_tables

router = APIRouter(prefix="/rag", tags=["campaign knowledge base"])
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ALLOWED_SUFFIXES = {".pdf", ".txt", ".md", ".markdown"}


class RetrievalRequest(BaseModel):
    query: str = Field(min_length=3, max_length=20_000)
    document_ids: list[str] = Field(default_factory=list, max_length=100)
    top_k: int = Field(default=5, ge=1, le=10)
    min_similarity: float = Field(default=0.70, ge=0, le=1)


def _db_error(exc: Exception):
    return HTTPException(status_code=503, detail=f"RAG database unavailable: {exc}")


@router.post("/documents", status_code=status.HTTP_202_ACCEPTED)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    document_title: str = Form(default="", max_length=300),
    source_url: str = Form(default="", max_length=2048),
    reuse_notes: str = Form(default="", max_length=1000),
    user: User = Depends(current_user),
):
    filename = PurePath(file.filename or "document").name
    suffix = PurePath(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(status_code=415, detail="Upload a PDF, TXT, or Markdown file")
    if source_url and (urlparse(source_url).scheme not in {"http", "https"} or not urlparse(source_url).hostname):
        raise HTTPException(status_code=422, detail="Source URL must use HTTP or HTTPS")
    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Each document must be 10 MB or smaller")
    try:
        job_id = start_job(user.id, filename, settings.rag_db_password)
    except Exception as exc:
        raise _db_error(exc) from exc
    background_tasks.add_task(process_upload, job_id, user.id, filename, raw, settings.rag_db_password,
                              source_url, reuse_notes, document_title)
    return {"id": job_id, "filename": filename, "status": "queued", "step": "uploaded"}


@router.get("/documents")
def documents(user: User = Depends(current_user)):
    try:
        return list_user_documents(user.id, settings.rag_db_password)
    except Exception as exc:
        raise _db_error(exc) from exc


@router.get("/jobs/{job_id}")
def ingestion_job(job_id: str, user: User = Depends(current_user)):
    try:
        result = get_job(job_id, user.id, settings.rag_db_password)
    except Exception as exc:
        raise _db_error(exc) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Ingestion job not found")
    return result


@router.get("/jobs")
def ingestion_jobs(user: User = Depends(current_user)):
    try:
        return list_user_jobs(user.id, settings.rag_db_password)
    except Exception as exc:
        raise _db_error(exc) from exc


@router.post("/retrieve")
def retrieve(request: RetrievalRequest, user: User = Depends(current_user)):
    try:
        with connect_rag_database(settings.rag_db_password) as connection:
            ensure_studio_tables(connection)
            rows = search_user_chunks(connection, user.id, request.query, request.document_ids,
                                      request.top_k, request.min_similarity)
    except Exception as exc:
        raise _db_error(exc) from exc
    return {"chunks": rows, "model": "BAAI/bge-small-en-v1.5", "top_k": request.top_k,
            "min_similarity": request.min_similarity, "source_only_fallback": not rows}
