"""
Voice cloning management endpoints.
"""
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlmodel import Session

from app.auth import get_current_user
from app.config import settings
from app.database import get_session
from app.models.schemas import SetDefaultVoiceRequest, VoiceInfo

router = APIRouter(prefix="/api/voice", tags=["voice"])


@router.post("/clone")
async def clone_voice_endpoint(
    audio: UploadFile = File(...),
    name: str = Form(...),
    user: dict = Depends(get_current_user),
) -> dict:
    import tempfile
    from pathlib import Path
    from app.services.voice_clone import clone_voice

    suffix = Path(audio.filename or "audio.wav").suffix
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        content = await audio.read()
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        voice_id = await clone_voice(tmp_path, name)
    finally:
        tmp_path.unlink(missing_ok=True)

    return {"voice_id": voice_id, "name": name}


@router.get("/list", response_model=list[VoiceInfo])
async def list_voices(
    user: dict = Depends(get_current_user),
) -> list[VoiceInfo]:
    from app.services.voice_clone import list_voices as _list

    raw = await _list()
    return [
        VoiceInfo(
            voice_id=v.get("voice_id") or v.get("id") or "?",
            name=v.get("name") or v.get("voice_name") or "?",
            is_default=(v.get("voice_id") or v.get("id")) == settings.minimax_default_voice_id,
        )
        for v in raw
    ]


@router.post("/set-default")
def set_default_voice(
    body: SetDefaultVoiceRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    # In production this would persist to DB; for now just acknowledge
    return {"ok": True, "voice_id": body.voice_id,
            "note": "Set MINIMAX_DEFAULT_VOICE_ID in .env to persist across restarts"}
