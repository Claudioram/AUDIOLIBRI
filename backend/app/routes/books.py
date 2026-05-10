import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlmodel import Session, select

from app.auth import get_current_user
from app.config import settings
from app.database import Book, BookStatus, Chapter, ChapterStatus, Job, JobStatus, JobType, get_session
from app.models.schemas import BookDetail, BookRead

router = APIRouter(prefix="/api/books", tags=["books"])


@router.post("/upload", response_model=BookRead, status_code=status.HTTP_201_CREATED)
async def upload_book(
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> BookRead:
    # Validate file type
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    # Check size limit
    max_bytes = settings.max_pdf_size_mb * 1_048_576
    content = await file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(status_code=413, detail=f"File exceeds {settings.max_pdf_size_mb} MB limit")

    book_id = str(uuid.uuid4())
    pdf_dest = settings.pdfs_dir / f"{book_id}.pdf"
    settings.pdfs_dir.mkdir(parents=True, exist_ok=True)
    pdf_dest.write_bytes(content)

    # Quick metadata extraction
    try:
        from app.services.pdf_parser import extract_book_metadata
        meta = extract_book_metadata(pdf_dest)
    except Exception:
        meta = {"title": Path(file.filename).stem, "author": None, "total_pages": 0}

    book = Book(
        id=book_id,
        filename=file.filename,
        title=meta["title"],
        author=meta["author"],
        total_pages=meta["total_pages"],
        status=BookStatus.parsing,
    )
    session.add(book)

    job = Job(
        book_id=book_id,
        type=JobType.parse_pdf,
        status=JobStatus.queued,
    )
    session.add(job)
    session.commit()
    session.refresh(book)

    # Enqueue parse job
    try:
        from app.workers.jobs import parse_pdf_job
        import redis as redis_lib
        from rq import Queue as RQQueue
        r = redis_lib.from_url(settings.redis_url)
        q = RQQueue(connection=r)
        q.enqueue(parse_pdf_job, book_id, job_timeout=600)
    except Exception as exc:
        # If Redis isn't running, mark for manual processing
        book.status = BookStatus.failed
        book.error_message = f"Could not enqueue job: {exc}"
        session.commit()

    return BookRead.model_validate(book)


@router.get("", response_model=list[BookRead])
def list_books(
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> list[BookRead]:
    books = session.exec(select(Book).order_by(Book.created_at.desc())).all()
    return [BookRead.model_validate(b) for b in books]


@router.get("/{book_id}", response_model=BookDetail)
def get_book(
    book_id: str,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> BookDetail:
    book = session.get(Book, book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    return BookDetail.model_validate(book)


@router.delete("/{book_id}", status_code=204)
def delete_book(
    book_id: str,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> None:
    book = session.get(Book, book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    # Remove files
    pdf = settings.pdfs_dir / f"{book_id}.pdf"
    pdf.unlink(missing_ok=True)

    audio_dir = settings.audio_dir / book_id
    if audio_dir.exists():
        shutil.rmtree(audio_dir)

    m4b = settings.m4b_dir / f"{book_id}.m4b"
    m4b.unlink(missing_ok=True)

    # Delete chapters and jobs first (FK)
    chapters = session.exec(select(Chapter).where(Chapter.book_id == book_id)).all()
    for ch in chapters:
        jobs = session.exec(select(Job).where(Job.chapter_id == ch.id)).all()
        for j in jobs:
            session.delete(j)
        session.delete(ch)

    book_jobs = session.exec(select(Job).where(Job.book_id == book_id)).all()
    for j in book_jobs:
        session.delete(j)

    session.delete(book)
    session.commit()


@router.post("/{book_id}/generate")
def generate_book(
    book_id: str,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> dict:
    book = session.get(Book, book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    if book.status not in (BookStatus.ready_to_generate,):
        raise HTTPException(status_code=400, detail=f"Book is not ready to generate (status={book.status})")

    chapters = session.exec(
        select(Chapter)
        .where(Chapter.book_id == book_id, Chapter.status == ChapterStatus.pending)
        .order_by(Chapter.order)
    ).all()

    if not chapters:
        raise HTTPException(status_code=400, detail="No pending chapters to generate")

    book.status = BookStatus.generating
    session.commit()

    enqueued = 0
    try:
        import redis as redis_lib
        from rq import Queue as RQQueue
        from app.workers.jobs import generate_chapter_job
        r = redis_lib.from_url(settings.redis_url)
        q = RQQueue(connection=r)
        for ch in chapters:
            q.enqueue(generate_chapter_job, str(ch.id), job_timeout=3600)
            enqueued += 1
    except Exception as exc:
        book.status = BookStatus.failed
        book.error_message = str(exc)
        session.commit()
        raise HTTPException(status_code=500, detail=f"Failed to enqueue jobs: {exc}")

    return {"enqueued": enqueued}


@router.post("/{book_id}/regenerate/{chapter_id}")
def regenerate_chapter(
    book_id: str,
    chapter_id: str,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> dict:
    chapter = session.get(Chapter, chapter_id)
    if not chapter or chapter.book_id != book_id:
        raise HTTPException(status_code=404, detail="Chapter not found")

    chapter.status = ChapterStatus.pending
    chapter.error_message = None
    session.commit()

    try:
        import redis as redis_lib
        from rq import Queue as RQQueue
        from app.workers.jobs import generate_chapter_job
        r = redis_lib.from_url(settings.redis_url)
        q = RQQueue(connection=r)
        q.enqueue(generate_chapter_job, chapter_id, job_timeout=3600)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to enqueue job: {exc}")

    return {"ok": True}


@router.get("/{book_id}/m4b")
def download_m4b(
    book_id: str,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> FileResponse:
    book = session.get(Book, book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    if book.status != BookStatus.completed:
        raise HTTPException(status_code=400, detail="M4B not yet available")

    m4b_path = settings.m4b_dir / f"{book_id}.m4b"
    if not m4b_path.exists():
        raise HTTPException(status_code=404, detail="M4B file not found on disk")

    safe_title = "".join(c for c in book.title if c.isalnum() or c in " _-")[:80]
    return FileResponse(
        path=str(m4b_path),
        media_type="audio/mp4",
        filename=f"{safe_title}.m4b",
    )
