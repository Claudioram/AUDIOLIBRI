from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlmodel import Session

from app.auth import get_current_user
from app.config import settings
from app.database import Chapter, get_session
from app.models.schemas import ChapterTextRead, ChapterTextUpdate

router = APIRouter(prefix="/api/chapters", tags=["chapters"])


@router.get("/{chapter_id}/text", response_model=ChapterTextRead)
def get_chapter_text(
    chapter_id: str,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> ChapterTextRead:
    chapter = session.get(Chapter, chapter_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")
    return ChapterTextRead(
        id=chapter.id,
        title=chapter.title,
        text_content=chapter.text_content,
        raw_text=chapter.raw_text,
        char_count=chapter.char_count,
    )


@router.put("/{chapter_id}/text", response_model=ChapterTextRead)
def update_chapter_text(
    chapter_id: str,
    body: ChapterTextUpdate,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> ChapterTextRead:
    chapter = session.get(Chapter, chapter_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    chapter.text_content = body.text_content
    chapter.char_count = len(body.text_content)
    session.commit()
    session.refresh(chapter)

    return ChapterTextRead(
        id=chapter.id,
        title=chapter.title,
        text_content=chapter.text_content,
        raw_text=chapter.raw_text,
        char_count=chapter.char_count,
    )


@router.get("/{chapter_id}/audio")
def stream_chapter_audio(
    chapter_id: str,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> FileResponse:
    chapter = session.get(Chapter, chapter_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")
    if not chapter.audio_path:
        raise HTTPException(status_code=404, detail="Audio not yet generated")

    audio_full_path = settings.storage_path / chapter.audio_path
    if not audio_full_path.exists():
        raise HTTPException(status_code=404, detail="Audio file not found on disk")

    return FileResponse(
        path=str(audio_full_path),
        media_type="audio/mpeg",
        filename=f"chapter_{chapter.order:03d}.mp3",
    )
