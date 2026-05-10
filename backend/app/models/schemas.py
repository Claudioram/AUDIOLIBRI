from datetime import datetime
from typing import Optional
from pydantic import BaseModel

from app.database import BookStatus, ChapterStatus, JobStatus, JobType


# ── Auth ──────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    password: str


class LoginResponse(BaseModel):
    ok: bool


# ── Book ──────────────────────────────────────────────────────────────────────

class BookRead(BaseModel):
    id: str
    filename: str
    title: str
    author: Optional[str]
    total_pages: int
    total_chapters: int
    status: BookStatus
    created_at: datetime
    error_message: Optional[str]

    model_config = {"from_attributes": True}


class BookDetail(BookRead):
    chapters: list["ChapterRead"] = []


# ── Chapter ───────────────────────────────────────────────────────────────────

class ChapterRead(BaseModel):
    id: str
    book_id: str
    order: int
    title: str
    char_count: int
    audio_path: Optional[str]
    duration_seconds: Optional[float]
    status: ChapterStatus
    error_message: Optional[str]
    generated_at: Optional[datetime]

    model_config = {"from_attributes": True}


class ChapterTextRead(BaseModel):
    id: str
    title: str
    text_content: str
    raw_text: str
    char_count: int


class ChapterTextUpdate(BaseModel):
    text_content: str


# ── Job ───────────────────────────────────────────────────────────────────────

class JobRead(BaseModel):
    id: str
    book_id: str
    chapter_id: Optional[str]
    type: JobType
    status: JobStatus
    created_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    error_message: Optional[str]

    model_config = {"from_attributes": True}


# ── Voice ─────────────────────────────────────────────────────────────────────

class VoiceCloneRequest(BaseModel):
    name: str
    voice_id: Optional[str] = None


class VoiceInfo(BaseModel):
    voice_id: str
    name: str
    is_default: bool


class SetDefaultVoiceRequest(BaseModel):
    voice_id: str


# ── Internal pipeline ─────────────────────────────────────────────────────────

class ChapterData(BaseModel):
    """Internal: output of pdf_parser, before DB insertion."""
    order: int
    title: str
    raw_text: str
    start_page: int
    end_page: int


BookDetail.model_rebuild()
