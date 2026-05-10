"""
MiniMax voice cloning management.

One-time setup: upload a reference audio file → get a persistent voice_id.
The voice_id is stored in the database (or settings) and reused for all synthesis.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
from loguru import logger

from app.config import settings


_BASE_URL = "https://api.minimax.io/v1"


class VoiceCloneError(Exception):
    pass


async def clone_voice(
    audio_path: Path,
    voice_name: str,
    group_id: str | None = None,
    api_key: str | None = None,
) -> str:
    """
    Upload a reference audio file to MiniMax and create a cloned voice.
    Returns the voice_id string.

    The caller must synthesize at least once within 168 hours to make the
    voice permanent in the MiniMax account.
    """
    api_key = api_key or settings.minimax_api_key
    group_id = group_id or settings.minimax_group_id

    if not api_key:
        raise VoiceCloneError("MINIMAX_API_KEY is not configured")

    if not audio_path.exists():
        raise VoiceCloneError(f"Reference audio file not found: {audio_path}")

    headers = {"Authorization": f"Bearer {api_key}"}

    # Step 1: Upload the audio file
    logger.info(f"Uploading reference voice: {audio_path.name}")
    with open(audio_path, "rb") as f:
        async with httpx.AsyncClient(timeout=120, verify=False) as client:
            upload_resp = await client.post(
                f"{_BASE_URL}/files/upload",
                headers=headers,
                params={"GroupId": group_id},
                files={"file": (audio_path.name, f, _mime_type(audio_path))},
                data={"purpose": "voice_clone"},
            )

    upload_resp.raise_for_status()
    upload_data = upload_resp.json()
    logger.debug(f"Upload response: {upload_data}")

    file_id = (
        upload_data.get("file_id")
        or (upload_data.get("file") or {}).get("file_id")
    )
    if not file_id:
        raise VoiceCloneError(f"No file_id in upload response: {upload_data}")

    # Step 2: Create the cloned voice
    voice_id_slug = _slugify(voice_name)
    logger.info(f"Creating cloned voice '{voice_name}' (id={voice_id_slug}) from file_id={file_id}")
    payload = {
        "file_id": int(file_id),  # MiniMax requires integer file_id
        "voice_id": voice_id_slug,
        "name": voice_name,
    }

    async with httpx.AsyncClient(timeout=60, verify=False) as client:
        clone_resp = await client.post(
            f"{_BASE_URL}/voice_clone",
            headers={**headers, "Content-Type": "application/json"},
            params={"GroupId": group_id},
            json=payload,
        )

    clone_resp.raise_for_status()
    clone_data = clone_resp.json()
    logger.debug(f"Clone response: {clone_data}")

    if clone_data.get("base_resp", {}).get("status_code", 0) != 0:
        msg = clone_data.get("base_resp", {}).get("status_msg", "Unknown error")
        raise VoiceCloneError(f"Voice clone API error: {msg} | response: {clone_data}")

    voice_id = (
        clone_data.get("voice_id")
        or clone_data.get("id")
        or voice_id_slug
    )

    logger.info(f"Voice cloned successfully: voice_id={voice_id}")
    return voice_id


async def list_voices(
    group_id: str | None = None,
    api_key: str | None = None,
) -> list[dict]:
    """List all custom voices available in the MiniMax account."""
    api_key = api_key or settings.minimax_api_key
    group_id = group_id or settings.minimax_group_id

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30, verify=False) as client:
        resp = await client.get(
            f"{_BASE_URL}/voice_clone/list",
            headers=headers,
            params={"GroupId": group_id},
        )

    resp.raise_for_status()
    data = resp.json()
    voices = data.get("voices", data.get("voice_list", []))
    return voices


async def warmup_voice(
    voice_id: str,
    group_id: str | None = None,
    api_key: str | None = None,
) -> bool:
    """
    Synthesize a short test phrase to keep the cloned voice alive
    (MiniMax expires unused clones after 168 h).
    Returns True on success.
    """
    from app.services.tts_minimax import MinimaxAPIClient
    import tempfile

    api_key = api_key or settings.minimax_api_key
    group_id = group_id or settings.minimax_group_id

    client = MinimaxAPIClient(api_key=api_key, group_id=group_id)

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        await client.synthesize_text(
            text="Questo è un test di riscaldamento vocale.",
            voice_id=voice_id,
            output_path=tmp_path,
        )
        logger.info(f"Voice warmup successful for voice_id={voice_id}")
        return True
    except Exception as exc:
        logger.error(f"Voice warmup failed: {exc}")
        return False
    finally:
        tmp_path.unlink(missing_ok=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mime_type(path: Path) -> str:
    ext = path.suffix.lower()
    return {
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".ogg": "audio/ogg",
        ".flac": "audio/flac",
    }.get(ext, "audio/mpeg")


def _slugify(name: str) -> str:
    import re
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9_\-]", "_", slug)
    slug = re.sub(r"_+", "_", slug)
    return slug[:64]
