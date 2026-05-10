import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from sqlmodel import Field, Relationship, SQLModel, create_engine, Session, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings


# ── Enums ─────────────────────────────────────────────────────────────────────

class BookStatus(str, Enum):
    uploading = "uploading"
    parsing = "parsing"
    ready_to_generate = "ready_to_generate"
    generating = "generating"
    completed = "completed"
    failed = "failed"


class ChapterStatus(str, Enum):
    pending = "pending"
    generating = "generating"
    completed = "completed"
    failed = "failed"


class JobType(str, Enum):
    parse_pdf = "parse_pdf"
    generate_chapter = "generate_chapter"
    assemble_m4b = "assemble_m4b"


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"


# ── Models ────────────────────────────────────────────────────────────────────

class Book(SQLModel, table=True):
    __tablename__ = "books"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    filename: str
    title: str
    author: Optional[str] = None
    total_pages: int = 0
    total_chapters: int = 0
    status: BookStatus = BookStatus.uploading
    created_at: datetime = Field(default_factory=datetime.utcnow)
    error_message: Optional[str] = None

    chapters: list["Chapter"] = Relationship(back_populates="book")
    jobs: list["Job"] = Relationship(back_populates="book")


class Chapter(SQLModel, table=True):
    __tablename__ = "chapters"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    book_id: str = Field(foreign_key="books.id", index=True)
    order: int
    title: str
    text_content: str = ""
    raw_text: str = ""
    char_count: int = 0
    audio_path: Optional[str] = None
    duration_seconds: Optional[float] = None
    status: ChapterStatus = ChapterStatus.pending
    error_message: Optional[str] = None
    generated_at: Optional[datetime] = None

    book: Optional[Book] = Relationship(back_populates="chapters")
    jobs: list["Job"] = Relationship(back_populates="chapter")


class Job(SQLModel, table=True):
    __tablename__ = "jobs"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    book_id: str = Field(foreign_key="books.id", index=True)
    chapter_id: Optional[str] = Field(default=None, foreign_key="chapters.id", index=True)
    type: JobType
    status: JobStatus = JobStatus.queued
    created_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None

    book: Optional[Book] = Relationship(back_populates="jobs")
    chapter: Optional[Chapter] = Relationship(back_populates="jobs")


# ── Engine setup ──────────────────────────────────────────────────────────────

def _ensure_dirs() -> None:
    settings.data_path.mkdir(parents=True, exist_ok=True)
    settings.pdfs_dir.mkdir(parents=True, exist_ok=True)
    settings.audio_dir.mkdir(parents=True, exist_ok=True)
    settings.m4b_dir.mkdir(parents=True, exist_ok=True)
    (settings.storage_path / "reference").mkdir(parents=True, exist_ok=True)


def get_sync_engine():
    _ensure_dirs()
    return create_engine(settings.db_url, connect_args={"check_same_thread": False})


def get_async_engine():
    _ensure_dirs()
    return create_async_engine(settings.async_db_url, connect_args={"check_same_thread": False})


def init_db() -> None:
    engine = get_sync_engine()
    SQLModel.metadata.create_all(engine)


def get_session():
    engine = get_sync_engine()
    with Session(engine) as session:
        yield session


async def get_async_session():
    engine = get_async_engine()
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session
