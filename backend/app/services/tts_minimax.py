"""
MiniMax Speech-02 async TTS client.

Flow:
  1. POST /t2a_async_v2          → task_id
  2. Poll GET /query/t2a_async_query_v2  → wait for success status
  3. GET /files/retrieve?file_id=…      → download_url
  4. Download MP3 to disk
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Optional

import httpx
from loguru import logger

from app.config import settings
from app.services.text_normalizer import split_into_chunks


_BASE_URL = "https://api.minimax.io/v1"
_POLL_INTERVAL_SEC = 5
_POLL_MAX_ATTEMPTS = 120  # 10 minutes total


class MinimaxError(Exception):
    pass


class MinimaxRateLimitError(MinimaxError):
    pass


class MinimaxAPIClient:
    """Thin async wrapper around the MiniMax REST API."""

    def __init__(self, api_key: str, group_id: str):
        self.api_key = api_key
        self.group_id = group_id
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    # ── TTS async ─────────────────────────────────────────────────────────────

    async def synthesize_text(
        self,
        text: str,
        voice_id: str,
        output_path: Path,
        model: Optional[str] = None,
        speed: float = 1.0,
        emotion: str = "neutral",
    ) -> dict:
        """
        Synthesize text to MP3 using the async T2A v2 API.
        Returns metadata dict with duration info.
        Handles chunking automatically for text > max_chapter_chars.
        """
        model = model or settings.minimax_model

        chunks = split_into_chunks(text, max_chars=settings.max_chapter_chars)
        if len(chunks) == 1:
            return await self._synthesize_single(
                text=chunks[0],
                voice_id=voice_id,
                output_path=output_path,
                model=model,
                speed=speed,
                emotion=emotion,
            )

        # Multi-chunk: synthesize each part, then concatenate via ffmpeg
        logger.info(f"Text exceeds limit; splitting into {len(chunks)} chunks")
        chunk_paths: list[Path] = []
        for i, chunk in enumerate(chunks):
            chunk_path = output_path.parent / f"_chunk_{output_path.stem}_{i:03d}.mp3"
            await self._synthesize_single(
                text=chunk,
                voice_id=voice_id,
                output_path=chunk_path,
                model=model,
                speed=speed,
                emotion=emotion,
            )
            chunk_paths.append(chunk_path)

        _concat_mp3_files(chunk_paths, output_path)

        for p in chunk_paths:
            p.unlink(missing_ok=True)

        return {"output_path": str(output_path), "chunks": len(chunks)}

    async def _synthesize_single(
        self,
        text: str,
        voice_id: str,
        output_path: Path,
        model: str,
        speed: float,
        emotion: str,
    ) -> dict:
        """Single chunk: submit job → poll → download."""
        task_id = await self._submit_t2a_job(text, voice_id, model, speed, emotion)
        file_id = await self._poll_until_done(task_id)
        download_url = await self._get_file_url(file_id)
        await self._download_file(download_url, output_path)
        return {"task_id": task_id, "file_id": file_id, "output_path": str(output_path)}

    async def _submit_t2a_job(
        self,
        text: str,
        voice_id: str,
        model: str,
        speed: float,
        emotion: str,
    ) -> str:
        payload = {
            "model": model,
            "text": text,
            "voice_setting": {
                "voice_id": voice_id,
                "speed": speed,
                "vol": 1.0,
                "pitch": 0,
                "emotion": emotion,
            },
            "audio_setting": {
                "sample_rate": 32000,
                "bitrate": 128000,
                "format": "mp3",
                "channel": 1,
            },
            "language_boost": "Italian",
        }

        for attempt in range(1, 4):
            try:
                async with httpx.AsyncClient(timeout=60) as client:
                    resp = await client.post(
                        f"{_BASE_URL}/t2a_async_v2",
                        headers=self._headers,
                        params={"GroupId": self.group_id},
                        json=payload,
                    )

                if resp.status_code == 429:
                    wait = 60 * attempt
                    logger.warning(f"Rate limit hit; waiting {wait}s before retry")
                    await asyncio.sleep(wait)
                    continue

                resp.raise_for_status()
                data = resp.json()
                logger.debug(f"T2A submit response: {data}")

                if data.get("base_resp", {}).get("status_code", 0) != 0:
                    msg = data.get("base_resp", {}).get("status_msg", "Unknown error")
                    raise MinimaxError(f"MiniMax API error: {msg} | full response: {data}")

                task_id = data.get("task_id")
                if not task_id:
                    raise MinimaxError(f"No task_id in response: {data}")

                return task_id

            except (httpx.NetworkError, httpx.TimeoutException) as exc:
                backoff = [5, 30, 120][attempt - 1]
                logger.warning(f"Network error on attempt {attempt}: {exc}; retrying in {backoff}s")
                await asyncio.sleep(backoff)

        raise MinimaxError("Failed to submit TTS job after 3 attempts")

    async def _poll_until_done(self, task_id: str) -> str:
        """Poll the async query endpoint until the job completes. Returns file_id."""
        for attempt in range(_POLL_MAX_ATTEMPTS):
            await asyncio.sleep(_POLL_INTERVAL_SEC)

            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(
                    f"{_BASE_URL}/query/t2a_async_query_v2",
                    headers=self._headers,
                    params={"task_id": task_id, "GroupId": self.group_id},
                )

            resp.raise_for_status()
            data = resp.json()
            logger.debug(f"Poll {attempt + 1}/{_POLL_MAX_ATTEMPTS}: task_id={task_id} status={data}")

            status = data.get("status") or data.get("task_status")
            if status in ("Success", "completed", "success", 2):
                file_id = (
                    data.get("file_id")
                    or data.get("output_file_id")
                    or (data.get("audio_file") or {}).get("file_id")
                )
                if not file_id:
                    raise MinimaxError(f"Job done but no file_id: {data}")
                return file_id

            if status in ("Failed", "failed", "error", -1):
                raise MinimaxError(f"TTS job failed: {data}")

        raise MinimaxError(f"TTS job timed out after {_POLL_MAX_ATTEMPTS} polls (task_id={task_id})")

    async def _get_file_url(self, file_id: str) -> str:
        """Resolve a file_id to a download URL."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{_BASE_URL}/files/retrieve",
                headers=self._headers,
                params={"file_id": file_id, "GroupId": self.group_id},
            )

        resp.raise_for_status()
        data = resp.json()
        logger.debug(f"File retrieve response: {data}")

        url = (
            data.get("download_url")
            or data.get("url")
            or (data.get("file") or {}).get("download_url")
        )
        if not url:
            raise MinimaxError(f"No download URL in file retrieve response: {data}")
        return url

    async def _download_file(self, url: str, dest: Path) -> None:
        import tarfile
        import tempfile

        dest.parent.mkdir(parents=True, exist_ok=True)

        # Download to a temp file first so we can inspect/extract if needed
        with tempfile.NamedTemporaryFile(delete=False, suffix=".download") as tmp:
            tmp_path = Path(tmp.name)

        async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                with open(tmp_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        f.write(chunk)

        # MiniMax returns a .tar archive containing the audio file — extract it
        if tarfile.is_tarfile(tmp_path):
            with tarfile.open(tmp_path) as tar:
                audio_members = [
                    m for m in tar.getmembers()
                    if m.name.lower().endswith((".mp3", ".wav", ".aac", ".m4a"))
                ]
                if not audio_members:
                    raise MinimaxError("tar archive contains no audio file")
                # Pick the largest audio file (the actual speech, not metadata)
                audio_member = max(audio_members, key=lambda m: m.size)
                extracted = tar.extractfile(audio_member)
                if extracted is None:
                    raise MinimaxError(f"Could not read {audio_member.name} from tar")
                dest.write_bytes(extracted.read())
                logger.debug(f"Extracted {audio_member.name} from tar ({audio_member.size // 1024} KB)")
        else:
            # Plain audio file — just move it
            tmp_path.rename(dest)

        tmp_path.unlink(missing_ok=True)
        logger.info(f"Downloaded audio to {dest} ({dest.stat().st_size / 1024:.1f} KB)")

    # ── Voice listing ──────────────────────────────────────────────────────────

    async def list_voices(self) -> list[dict]:
        """List available custom voices for this group."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{_BASE_URL}/voice_clone/list",
                headers=self._headers,
                params={"GroupId": self.group_id},
            )

        resp.raise_for_status()
        data = resp.json()
        return data.get("voices", data.get("voice_list", []))


# ── Module-level convenience function ─────────────────────────────────────────

async def generate_chapter_audio(
    text: str,
    voice_id: str,
    output_path: Path,
    model: Optional[str] = None,
) -> dict:
    """High-level convenience: create client from settings and synthesize."""
    if not settings.minimax_api_key:
        raise MinimaxError("MINIMAX_API_KEY is not configured")

    client = MinimaxAPIClient(
        api_key=settings.minimax_api_key,
        group_id=settings.minimax_group_id,
    )
    return await client.synthesize_text(
        text=text,
        voice_id=voice_id,
        output_path=output_path,
        model=model,
    )


# ── FFmpeg concat helper ──────────────────────────────────────────────────────

def _concat_mp3_files(parts: list[Path], output: Path) -> None:
    """Concatenate MP3 files using ffmpeg concat demuxer."""
    import subprocess
    import tempfile

    concat_list = output.parent / f"_concat_{output.stem}.txt"
    with open(concat_list, "w") as f:
        for p in parts:
            f.write(f"file '{p.resolve()}'\n")

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_list),
        "-c", "copy",
        str(output),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    concat_list.unlink(missing_ok=True)

    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg concat failed:\n{result.stderr}")
