"""
Tests for tts_minimax.py.

These tests do NOT call the real MiniMax API.
They test the client logic (payload construction, retry, chunking) using mocks.
"""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_client():
    from app.services.tts_minimax import MinimaxAPIClient
    return MinimaxAPIClient(api_key="test_key", group_id="test_group")


# ── MinimaxAPIClient unit tests ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_submit_job_returns_task_id():
    client = _make_client()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "task_id": "task_abc123",
        "base_resp": {"status_code": 0, "status_msg": "Success"},
    }

    with patch("httpx.AsyncClient") as mock_httpx:
        mock_httpx.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_resp)
        task_id = await client._submit_t2a_job(
            text="Ciao mondo",
            voice_id="voice_001",
            model="speech-02-hd",
            speed=1.0,
            emotion="neutral",
        )

    assert task_id == "task_abc123"


@pytest.mark.asyncio
async def test_poll_until_done_success():
    client = _make_client()

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "status": "Success",
        "file_id": "file_xyz789",
    }

    with patch("httpx.AsyncClient") as mock_httpx:
        mock_httpx.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_resp)
        with patch("asyncio.sleep", new_callable=AsyncMock):
            file_id = await client._poll_until_done("task_abc123")

    assert file_id == "file_xyz789"


@pytest.mark.asyncio
async def test_poll_until_done_failed_raises():
    from app.services.tts_minimax import MinimaxError

    client = _make_client()

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"status": "Failed", "error": "Something went wrong"}

    with patch("httpx.AsyncClient") as mock_httpx:
        mock_httpx.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_resp)
        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(MinimaxError, match="TTS job failed"):
                await client._poll_until_done("task_fail")


@pytest.mark.asyncio
async def test_submit_job_raises_on_api_error():
    from app.services.tts_minimax import MinimaxError

    client = _make_client()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "base_resp": {"status_code": 1002, "status_msg": "Invalid voice_id"},
    }

    with patch("httpx.AsyncClient") as mock_httpx:
        mock_httpx.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_resp)
        with pytest.raises(MinimaxError, match="Invalid voice_id"):
            await client._submit_t2a_job(
                text="Test",
                voice_id="bad_voice",
                model="speech-02-hd",
                speed=1.0,
                emotion="neutral",
            )


@pytest.mark.asyncio
async def test_get_file_url_parses_response():
    client = _make_client()

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"download_url": "https://cdn.minimax.io/audio123.mp3"}

    with patch("httpx.AsyncClient") as mock_httpx:
        mock_httpx.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_resp)
        url = await client._get_file_url("file_xyz789")

    assert url == "https://cdn.minimax.io/audio123.mp3"


@pytest.mark.asyncio
async def test_get_file_url_raises_on_missing_url():
    from app.services.tts_minimax import MinimaxError

    client = _make_client()

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"some_other_key": "value"}

    with patch("httpx.AsyncClient") as mock_httpx:
        mock_httpx.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_resp)
        with pytest.raises(MinimaxError, match="No download URL"):
            await client._get_file_url("file_no_url")


# ── Chunking integration ──────────────────────────────────────────────────────

def test_split_into_chunks_single():
    from app.services.text_normalizer import split_into_chunks

    text = "Short text."
    result = split_into_chunks(text, max_chars=100)
    assert result == [text]


def test_split_into_chunks_multi():
    from app.services.text_normalizer import split_into_chunks

    para = "Parola " * 100  # ~700 chars
    text = "\n\n".join([para] * 5)
    chunks = split_into_chunks(text, max_chars=1000)
    assert len(chunks) > 1
    for ch in chunks:
        assert len(ch) < 1500


# ── Voice clone helpers ───────────────────────────────────────────────────────

def test_slugify():
    from app.services.voice_clone import _slugify

    assert _slugify("Narratore Italiano") == "narratore_italiano"
    assert _slugify("Voice #1 (test)") == "voice__1__test_"
    assert len(_slugify("x" * 100)) <= 64


def test_mime_type_detection():
    from app.services.voice_clone import _mime_type

    assert _mime_type(Path("audio.wav")) == "audio/wav"
    assert _mime_type(Path("audio.mp3")) == "audio/mpeg"
    assert _mime_type(Path("audio.m4a")) == "audio/mp4"
    assert _mime_type(Path("unknown.xyz")) == "audio/mpeg"  # fallback
