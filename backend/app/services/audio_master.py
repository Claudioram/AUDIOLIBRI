"""
Audio mastering and M4B assembly.

Mastering: EBU R128 loudnorm to -18 LUFS (ACX/Audible standard).
Assembly: FFmpeg concat → AAC M4A → mp4chaps chapter marks → M4B.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from loguru import logger


@dataclass
class ChapterMeta:
    order: int
    title: str
    audio_path: Path
    duration_seconds: float


# ── Public API ────────────────────────────────────────────────────────────────

def master_audio(input_path: Path, output_path: Optional[Path] = None) -> Path:
    """
    Apply EBU R128 loudnorm + gentle compression to an MP3 file.
    Returns the output path (in-place if output_path is None).
    """
    if output_path is None:
        output_path = input_path.parent / f"{input_path.stem}_mastered.mp3"

    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-af",
        (
            "loudnorm=I=-18:LRA=11:TP=-2,"
            "acompressor=threshold=-20dB:ratio=2:attack=5:release=50"
        ),
        "-b:a", "128k",
        "-ar", "44100",
        "-ac", "2",
        str(output_path),
    ]

    result = _run(cmd, description=f"loudnorm {input_path.name}")
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg mastering failed for {input_path.name}:\n{result.stderr}")

    logger.info(f"Mastered: {input_path.name} → {output_path.name}")
    return output_path


def assemble_m4b(
    chapters: list[ChapterMeta],
    output_path: Path,
    title: str = "",
    author: str = "",
    cover_path: Optional[Path] = None,
) -> Path:
    """
    Assemble a list of mastered MP3 chapters into a navigable M4B audiobook.

    Steps:
      1. Concat all MP3s into a single AAC M4A
      2. Generate CHAPTERS.txt (mp4chaps format)
      3. Embed chapter markers with mp4chaps
      4. Add ID3 metadata (title, author, genre) and optional cover art
      5. Rename to .m4b
    """
    if not chapters:
        raise ValueError("No chapters to assemble")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_m4a = output_path.with_suffix(".tmp.m4a")
    tmp_m4b = output_path.with_suffix(".tmp.m4b")

    # 1. Concat all chapter MP3s
    concat_file = output_path.parent / "_concat.txt"
    _write_concat_file(chapters, concat_file)

    concat_cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_file),
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(tmp_m4a),
    ]
    result = _run(concat_cmd, "concat MP3s → M4A")
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg concat failed:\n{result.stderr}")

    # 2. Generate chapter markers file
    chapters_txt = output_path.parent / "_CHAPTERS.txt"
    _write_chapters_txt(chapters, chapters_txt)

    # 3. Embed chapters with mp4chaps (if available) or ffmpeg metadata
    if _command_exists("mp4chaps"):
        # mp4chaps modifies in-place; copy first
        import shutil
        shutil.copy2(tmp_m4a, tmp_m4b)
        chapters_txt.rename(tmp_m4b.parent / (tmp_m4b.stem + ".chapters.txt"))
        chaps_cmd = ["mp4chaps", "-i", str(tmp_m4b)]
        result = _run(chaps_cmd, "embed chapters with mp4chaps")
        if result.returncode != 0:
            logger.warning(f"mp4chaps failed; chapters won't be embedded: {result.stderr}")
    else:
        logger.warning("mp4chaps not found; using ffmpeg metadata chapters instead")
        tmp_m4b = _embed_chapters_ffmpeg(tmp_m4a, chapters, tmp_m4b)

    # 4. Add ID3 metadata + cover art
    meta_output = output_path.with_suffix(".meta.m4b")
    _add_metadata(tmp_m4b, meta_output, title=title, author=author, cover_path=cover_path)

    # 5. Rename to final .m4b
    meta_output.rename(output_path)

    # Cleanup temp files
    for p in [concat_file, chapters_txt, tmp_m4a, tmp_m4b]:
        p.unlink(missing_ok=True)
    # Also remove any lingering .chapters.txt
    for p in output_path.parent.glob("*.chapters.txt"):
        p.unlink(missing_ok=True)

    logger.info(f"M4B assembled: {output_path} ({output_path.stat().st_size / 1_048_576:.1f} MB)")
    return output_path


def get_audio_duration(path: Path) -> float:
    """Return the duration of an audio file in seconds via ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not result.stdout.strip():
        logger.warning(f"Could not determine duration for {path}: {result.stderr}")
        return 0.0
    return float(result.stdout.strip())


def extract_cover_from_pdf(pdf_path: Path, output_path: Optional[Path] = None) -> Optional[Path]:
    """Extract the first page of a PDF as a JPEG cover image."""
    try:
        import fitz
    except ImportError:
        return None

    if output_path is None:
        output_path = pdf_path.parent / f"{pdf_path.stem}_cover.jpg"

    try:
        doc = fitz.open(str(pdf_path))
        page = doc[0]
        pix = page.get_pixmap(dpi=150)
        pix.save(str(output_path))
        return output_path
    except Exception as exc:
        logger.warning(f"Could not extract cover from PDF: {exc}")
        return None


# ── Internal helpers ──────────────────────────────────────────────────────────

def _write_concat_file(chapters: list[ChapterMeta], dest: Path) -> None:
    with open(dest, "w") as f:
        for ch in chapters:
            f.write(f"file '{ch.audio_path.resolve()}'\n")


def _write_chapters_txt(chapters: list[ChapterMeta], dest: Path) -> None:
    """Write mp4chaps-format chapter file (HH:MM:SS.mmm Title)."""
    elapsed = 0.0
    with open(dest, "w") as f:
        for ch in chapters:
            h = int(elapsed // 3600)
            m = int((elapsed % 3600) // 60)
            s = elapsed % 60
            f.write(f"{h:02d}:{m:02d}:{s:06.3f} {ch.title}\n")
            elapsed += ch.duration_seconds


def _embed_chapters_ffmpeg(
    input_m4a: Path,
    chapters: list[ChapterMeta],
    output: Path,
) -> Path:
    """
    Embed chapter markers using ffmpeg's -metadata chapter syntax
    when mp4chaps is not available.
    """
    meta_file = input_m4a.parent / "_ffmeta.txt"
    elapsed_ms = 0

    with open(meta_file, "w") as f:
        f.write(";FFMETADATA1\n")
        for ch in chapters:
            start_ms = int(elapsed_ms)
            end_ms = int(elapsed_ms + ch.duration_seconds * 1000)
            f.write("[CHAPTER]\n")
            f.write("TIMEBASE=1/1000\n")
            f.write(f"START={start_ms}\n")
            f.write(f"END={end_ms}\n")
            f.write(f"title={ch.title}\n")
            elapsed_ms = end_ms

    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_m4a),
        "-i", str(meta_file),
        "-map_metadata", "1",
        "-c", "copy",
        str(output),
    ]
    result = _run(cmd, "embed chapters via ffmpeg metadata")
    meta_file.unlink(missing_ok=True)

    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg chapter embedding failed:\n{result.stderr}")

    return output


def _add_metadata(
    input_path: Path,
    output_path: Path,
    title: str = "",
    author: str = "",
    cover_path: Optional[Path] = None,
) -> None:
    cmd = ["ffmpeg", "-y", "-i", str(input_path)]

    if cover_path and cover_path.exists():
        cmd += ["-i", str(cover_path)]
        cmd += ["-map", "0", "-map", "1"]
        cmd += ["-disposition:v:0", "attached_pic"]
    else:
        cmd += ["-map", "0"]

    if title:
        cmd += ["-metadata", f"title={title}"]
    if author:
        cmd += ["-metadata", f"artist={author}", "-metadata", f"album_artist={author}"]

    cmd += [
        "-metadata", "genre=Audiobook",
        "-metadata", "media_type=2",
        "-c", "copy",
        str(output_path),
    ]

    result = _run(cmd, "add metadata")
    if result.returncode != 0:
        # Non-fatal: just copy the file without metadata
        import shutil
        logger.warning(f"Metadata embedding failed; copying as-is: {result.stderr}")
        shutil.copy2(input_path, output_path)


def _command_exists(cmd: str) -> bool:
    result = subprocess.run(["which", cmd], capture_output=True)
    return result.returncode == 0


def _run(cmd: list[str], description: str = "") -> subprocess.CompletedProcess:
    logger.debug(f"Running: {' '.join(cmd[:4])}… [{description}]")
    return subprocess.run(cmd, capture_output=True, text=True)
