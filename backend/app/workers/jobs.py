"""
RQ job functions executed by the worker.
Each function runs in a separate process, so it creates its own DB session.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from loguru import logger
from sqlmodel import Session, select

from app.config import settings
from app.database import (
    Book, BookStatus, Chapter, ChapterStatus,
    Job, JobStatus, JobType, get_sync_engine,
)


def _get_session() -> Session:
    return Session(get_sync_engine())


# ── Job 1: Parse PDF and create chapters ──────────────────────────────────────

def parse_pdf_job(book_id: str) -> None:
    with _get_session() as session:
        book = session.get(Book, book_id)
        if not book:
            logger.error(f"parse_pdf_job: book {book_id} not found")
            return

        _mark_job_running(session, book_id, JobType.parse_pdf)
        book.status = BookStatus.parsing
        session.commit()

    try:
        from app.services.pdf_parser import parse_pdf
        from app.services.text_normalizer import normalize_for_tts

        pdf_path = settings.pdfs_dir / f"{book_id}.pdf"
        chapters_data = parse_pdf(pdf_path)

        with _get_session() as session:
            book = session.get(Book, book_id)

            for ch_data in chapters_data:
                normalized = normalize_for_tts(ch_data.raw_text)
                chapter = Chapter(
                    book_id=book_id,
                    order=ch_data.order,
                    title=ch_data.title,
                    raw_text=ch_data.raw_text,
                    text_content=normalized,
                    char_count=len(normalized),
                )
                session.add(chapter)

            book.total_chapters = len(chapters_data)
            book.status = BookStatus.ready_to_generate
            session.commit()
            _mark_job_done(session, book_id, JobType.parse_pdf)

        logger.info(f"Parsed {len(chapters_data)} chapters for book {book_id}")

    except Exception as exc:
        logger.exception(f"parse_pdf_job failed for book {book_id}: {exc}")
        with _get_session() as session:
            book = session.get(Book, book_id)
            if book:
                book.status = BookStatus.failed
                book.error_message = str(exc)
                session.commit()
            _mark_job_done(session, book_id, JobType.parse_pdf, error=str(exc))


# ── Job 2: Generate audio for a single chapter ────────────────────────────────

def generate_chapter_job(chapter_id: str) -> None:
    import asyncio

    with _get_session() as session:
        chapter = session.get(Chapter, chapter_id)
        if not chapter:
            logger.error(f"generate_chapter_job: chapter {chapter_id} not found")
            return

        book_id = chapter.book_id
        chapter.status = ChapterStatus.generating
        session.commit()
        _mark_job_running(session, book_id, JobType.generate_chapter, chapter_id)

    try:
        from app.services.tts_minimax import generate_chapter_audio
        from app.services.audio_master import master_audio, get_audio_duration

        with _get_session() as session:
            chapter = session.get(Chapter, chapter_id)
            text = chapter.text_content
            book_id = chapter.book_id
            order = chapter.order

        voice_id = settings.minimax_default_voice_id
        if not voice_id:
            raise ValueError("MINIMAX_DEFAULT_VOICE_ID is not set")

        audio_book_dir = settings.audio_dir / book_id
        audio_book_dir.mkdir(parents=True, exist_ok=True)
        raw_mp3 = audio_book_dir / f"chapter_{order:03d}_raw.mp3"
        mastered_mp3 = audio_book_dir / f"chapter_{order:03d}.mp3"

        asyncio.run(generate_chapter_audio(
            text=text,
            voice_id=voice_id,
            output_path=raw_mp3,
        ))

        master_audio(raw_mp3, mastered_mp3)
        raw_mp3.unlink(missing_ok=True)

        duration = get_audio_duration(mastered_mp3)
        relative_path = str(mastered_mp3.relative_to(settings.storage_path))

        with _get_session() as session:
            chapter = session.get(Chapter, chapter_id)
            chapter.status = ChapterStatus.completed
            chapter.audio_path = relative_path
            chapter.duration_seconds = duration
            chapter.generated_at = datetime.utcnow()
            session.commit()
            _mark_job_done(session, book_id, JobType.generate_chapter, chapter_id)

        logger.info(f"Chapter {order} generated: {mastered_mp3.name} ({duration:.1f}s)")

        # Check if all chapters are done → enqueue M4B assembly
        _maybe_enqueue_assemble(book_id)

    except Exception as exc:
        logger.exception(f"generate_chapter_job failed for chapter {chapter_id}: {exc}")
        with _get_session() as session:
            chapter = session.get(Chapter, chapter_id)
            if chapter:
                chapter.status = ChapterStatus.failed
                chapter.error_message = str(exc)
                session.commit()
            _mark_job_done(session, book_id, JobType.generate_chapter, chapter_id, error=str(exc))


# ── Job 3: Assemble M4B ───────────────────────────────────────────────────────

def assemble_m4b_job(book_id: str) -> None:
    with _get_session() as session:
        book = session.get(Book, book_id)
        if not book:
            logger.error(f"assemble_m4b_job: book {book_id} not found")
            return
        _mark_job_running(session, book_id, JobType.assemble_m4b)

    try:
        from app.services.audio_master import assemble_m4b, ChapterMeta, extract_cover_from_pdf

        with _get_session() as session:
            book = session.get(Book, book_id)
            chapters = session.exec(
                select(Chapter)
                .where(Chapter.book_id == book_id, Chapter.status == ChapterStatus.completed)
                .order_by(Chapter.order)
            ).all()

            if not chapters:
                raise ValueError("No completed chapters to assemble")

            chapter_metas = [
                ChapterMeta(
                    order=ch.order,
                    title=ch.title,
                    audio_path=settings.storage_path / ch.audio_path,
                    duration_seconds=ch.duration_seconds or 0.0,
                )
                for ch in chapters
            ]

            title = book.title
            author = book.author or ""

        pdf_path = settings.pdfs_dir / f"{book_id}.pdf"
        cover_path = extract_cover_from_pdf(pdf_path, settings.m4b_dir / f"{book_id}_cover.jpg")

        m4b_output = settings.m4b_dir / f"{book_id}.m4b"
        assemble_m4b(
            chapters=chapter_metas,
            output_path=m4b_output,
            title=title,
            author=author,
            cover_path=cover_path,
        )

        with _get_session() as session:
            book = session.get(Book, book_id)
            book.status = BookStatus.completed
            session.commit()
            _mark_job_done(session, book_id, JobType.assemble_m4b)

        logger.info(f"M4B assembled for book {book_id}: {m4b_output}")

    except Exception as exc:
        logger.exception(f"assemble_m4b_job failed for book {book_id}: {exc}")
        with _get_session() as session:
            book = session.get(Book, book_id)
            if book:
                book.status = BookStatus.failed
                book.error_message = str(exc)
                session.commit()
            _mark_job_done(session, book_id, JobType.assemble_m4b, error=str(exc))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mark_job_running(
    session: Session, book_id: str, job_type: JobType,
    chapter_id: str | None = None,
) -> None:
    job = session.exec(
        select(Job).where(
            Job.book_id == book_id,
            Job.type == job_type,
            Job.status == JobStatus.queued,
        )
    ).first()
    if job:
        job.status = JobStatus.running
        job.started_at = datetime.utcnow()
        if chapter_id:
            job.chapter_id = chapter_id
        session.commit()


def _mark_job_done(
    session: Session, book_id: str, job_type: JobType,
    chapter_id: str | None = None,
    error: str | None = None,
) -> None:
    job = session.exec(
        select(Job).where(
            Job.book_id == book_id,
            Job.type == job_type,
            Job.status == JobStatus.running,
        )
    ).first()
    if job:
        job.status = JobStatus.failed if error else JobStatus.completed
        job.completed_at = datetime.utcnow()
        job.error_message = error
        session.commit()


def _maybe_enqueue_assemble(book_id: str) -> None:
    """Enqueue assemble_m4b_job if all chapters are completed."""
    with _get_session() as session:
        chapters = session.exec(
            select(Chapter).where(Chapter.book_id == book_id)
        ).all()

        if not chapters:
            return
        if all(ch.status == ChapterStatus.completed for ch in chapters):
            try:
                import redis as redis_lib
                from rq import Queue as RQQueue
                r = redis_lib.from_url(settings.redis_url)
                q = RQQueue(connection=r)
                q.enqueue(assemble_m4b_job, book_id, job_timeout=1800)
                logger.info(f"Enqueued assemble_m4b_job for book {book_id}")
            except Exception as exc:
                logger.error(f"Failed to enqueue assemble job: {exc}")
