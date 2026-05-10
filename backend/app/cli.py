"""
CLI entry point: python -m app.cli generate --pdf libro.pdf

Phase-1 end-to-end pipeline (no web server, no queue):
  PDF → chapters → normalize → TTS → master → M4B
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Optional

import typer
from loguru import logger
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table

app = typer.Typer(name="audiobook", add_completion=False, help="Audiobook generator CLI")
console = Console()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _setup_logging(verbose: bool) -> None:
    logger.remove()
    level = "DEBUG" if verbose else "INFO"
    logger.add(sys.stderr, level=level, colorize=True,
               format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}")


# ── Commands ──────────────────────────────────────────────────────────────────

@app.command()
def generate(
    pdf: Path = typer.Argument(..., help="Path to the input PDF file"),
    output_dir: Optional[Path] = typer.Option(None, "--output", "-o", help="Output directory"),
    voice_id: Optional[str] = typer.Option(None, "--voice", "-v", help="MiniMax voice_id to use"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="MiniMax model override"),
    chapters_only: bool = typer.Option(False, "--chapters-only", help="Stop after TTS; skip M4B"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Parse and normalize only; no TTS calls"),
    verbose: bool = typer.Option(False, "--verbose"),
) -> None:
    """Convert a PDF to an M4B audiobook end-to-end."""
    _setup_logging(verbose)
    asyncio.run(_generate_async(pdf, output_dir, voice_id, model, chapters_only, dry_run))


@app.command()
def parse(
    pdf: Path = typer.Argument(..., help="Path to the PDF file"),
    verbose: bool = typer.Option(False, "--verbose"),
) -> None:
    """Parse a PDF and print its chapter structure (no TTS)."""
    _setup_logging(verbose)

    from app.services.pdf_parser import parse_pdf, extract_book_metadata

    if not pdf.exists():
        console.print(f"[red]File not found:[/red] {pdf}")
        raise typer.Exit(1)

    meta = extract_book_metadata(pdf)
    console.print(f"\n[bold]{meta['title']}[/bold]  —  {meta['author'] or 'Unknown author'}")
    console.print(f"Pages: {meta['total_pages']}\n")

    chapters = parse_pdf(pdf)

    table = Table(title="Chapters", show_lines=True)
    table.add_column("#", style="dim", width=4)
    table.add_column("Title", style="bold")
    table.add_column("Pages", justify="right")
    table.add_column("Chars", justify="right")

    for ch in chapters:
        table.add_row(
            str(ch.order),
            ch.title,
            f"{ch.start_page + 1}–{ch.end_page + 1}",
            str(ch.char_count),
        )

    console.print(table)


@app.command()
def clone_voice(
    audio: Path = typer.Argument(..., help="Reference audio file (WAV/MP3, 15–30s)"),
    name: str = typer.Argument(..., help="Name for the cloned voice"),
    verbose: bool = typer.Option(False, "--verbose"),
) -> None:
    """Upload a reference audio file and create a MiniMax cloned voice."""
    _setup_logging(verbose)
    asyncio.run(_clone_voice_async(audio, name))


@app.command()
def list_voices(
    verbose: bool = typer.Option(False, "--verbose"),
) -> None:
    """List available MiniMax voices."""
    _setup_logging(verbose)
    asyncio.run(_list_voices_async())


@app.command()
def warmup(
    voice_id: str = typer.Argument(..., help="Voice ID to warm up"),
    verbose: bool = typer.Option(False, "--verbose"),
) -> None:
    """Synthesize a short phrase to keep a cloned voice alive."""
    _setup_logging(verbose)
    asyncio.run(_warmup_async(voice_id))


# ── Async implementations ─────────────────────────────────────────────────────

async def _generate_async(
    pdf: Path,
    output_dir: Optional[Path],
    voice_id: Optional[str],
    model: Optional[str],
    chapters_only: bool,
    dry_run: bool,
) -> None:
    from app.config import settings
    from app.services.pdf_parser import parse_pdf, extract_book_metadata
    from app.services.text_normalizer import normalize_for_tts
    from app.services.tts_minimax import generate_chapter_audio
    from app.services.audio_master import (
        master_audio, assemble_m4b, get_audio_duration,
        ChapterMeta, extract_cover_from_pdf,
    )

    if not pdf.exists():
        console.print(f"[red]File not found:[/red] {pdf}")
        raise typer.Exit(1)

    # Resolve voice
    effective_voice_id = voice_id or settings.minimax_default_voice_id
    if not dry_run and not effective_voice_id:
        console.print("[red]No voice_id provided and MINIMAX_DEFAULT_VOICE_ID is not set.[/red]")
        console.print("Run [bold]audiobook clone-voice <audio> <name>[/bold] first.")
        raise typer.Exit(1)

    # Output directory
    if output_dir is None:
        output_dir = pdf.parent / pdf.stem
    output_dir.mkdir(parents=True, exist_ok=True)
    audio_dir = output_dir / "audio"
    audio_dir.mkdir(exist_ok=True)

    # Step 1: Parse PDF
    console.rule("[bold blue]Step 1: Parsing PDF")
    meta = extract_book_metadata(pdf)
    console.print(f"  Title:   {meta['title']}")
    console.print(f"  Author:  {meta['author'] or 'Unknown'}")
    console.print(f"  Pages:   {meta['total_pages']}")

    chapters = parse_pdf(pdf)
    console.print(f"  Chapters found: {len(chapters)}\n")

    # Step 2: Normalize text
    console.rule("[bold blue]Step 2: Normalizing text")
    normalized_chapters = []
    for ch in chapters:
        normalized = normalize_for_tts(ch.raw_text)
        normalized_chapters.append((ch, normalized))
        console.print(f"  [{ch.order:03d}] {ch.title[:60]}  ({len(normalized)} chars)")

    if dry_run:
        console.print("\n[yellow]Dry run complete — no TTS calls made.[/yellow]")
        # Write normalized texts to disk for inspection
        for ch, normalized in normalized_chapters:
            txt_path = audio_dir / f"chapter_{ch.order:03d}.txt"
            txt_path.write_text(normalized, encoding="utf-8")
            console.print(f"  Wrote {txt_path.name}")
        return

    # Step 3: TTS synthesis
    console.rule("[bold blue]Step 3: TTS synthesis (MiniMax)")
    mp3_paths: list[Path] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Synthesizing chapters…", total=len(normalized_chapters))

        for ch, normalized in normalized_chapters:
            mp3_path = audio_dir / f"chapter_{ch.order:03d}_raw.mp3"
            progress.update(task, description=f"[{ch.order}/{len(chapters)}] {ch.title[:40]}…")

            try:
                await generate_chapter_audio(
                    text=normalized,
                    voice_id=effective_voice_id,
                    output_path=mp3_path,
                    model=model,
                )
                mp3_paths.append((ch, mp3_path))
                logger.info(f"TTS done: {mp3_path.name}")
            except Exception as exc:
                logger.error(f"TTS failed for chapter {ch.order} ({ch.title}): {exc}")
                console.print(f"  [red]ERROR chapter {ch.order}:[/red] {exc}")
                mp3_paths.append((ch, None))

            progress.advance(task)

    # Step 4: Mastering
    console.rule("[bold blue]Step 4: Audio mastering (EBU R128)")
    mastered_chapters: list[ChapterMeta] = []

    for ch, raw_mp3 in mp3_paths:
        if raw_mp3 is None or not raw_mp3.exists():
            console.print(f"  [yellow]Skip chapter {ch.order} (no audio)[/yellow]")
            continue

        mastered_path = audio_dir / f"chapter_{ch.order:03d}.mp3"
        try:
            master_audio(raw_mp3, mastered_path)
            raw_mp3.unlink(missing_ok=True)
            duration = get_audio_duration(mastered_path)
            mastered_chapters.append(ChapterMeta(
                order=ch.order,
                title=ch.title,
                audio_path=mastered_path,
                duration_seconds=duration,
            ))
            console.print(f"  [{ch.order:03d}] {ch.title[:50]}  {duration:.1f}s")
        except Exception as exc:
            logger.error(f"Mastering failed for chapter {ch.order}: {exc}")
            console.print(f"  [red]Mastering ERROR chapter {ch.order}:[/red] {exc}")

    if chapters_only:
        console.print(f"\n[green]Done![/green] Audio files in {audio_dir}")
        return

    if not mastered_chapters:
        console.print("[red]No chapters were successfully synthesized; cannot assemble M4B.[/red]")
        raise typer.Exit(1)

    # Step 5: M4B assembly
    console.rule("[bold blue]Step 5: Assembling M4B")
    m4b_path = output_dir / f"{pdf.stem}.m4b"
    cover_path = extract_cover_from_pdf(pdf, output_dir / "cover.jpg")

    assemble_m4b(
        chapters=mastered_chapters,
        output_path=m4b_path,
        title=meta["title"],
        author=meta["author"] or "",
        cover_path=cover_path,
    )

    total_duration = sum(c.duration_seconds for c in mastered_chapters)
    h = int(total_duration // 3600)
    m = int((total_duration % 3600) // 60)
    s = int(total_duration % 60)

    console.print(f"\n[bold green]Audiobook ready![/bold green]")
    console.print(f"  File:     {m4b_path}")
    console.print(f"  Chapters: {len(mastered_chapters)}")
    console.print(f"  Duration: {h:02d}:{m:02d}:{s:02d}")
    console.print(f"  Size:     {m4b_path.stat().st_size / 1_048_576:.1f} MB")


async def _clone_voice_async(audio: Path, name: str) -> None:
    from app.services.voice_clone import clone_voice

    if not audio.exists():
        console.print(f"[red]File not found:[/red] {audio}")
        raise typer.Exit(1)

    console.print(f"Uploading reference audio: {audio.name}")
    voice_id = await clone_voice(audio, name)
    console.print(f"\n[green]Voice cloned successfully![/green]")
    console.print(f"  voice_id: [bold]{voice_id}[/bold]")
    console.print(f"\nAdd to .env:  MINIMAX_DEFAULT_VOICE_ID={voice_id}")
    console.print("[yellow]Remember: synthesize with this voice within 168h to make it permanent.[/yellow]")


async def _list_voices_async() -> None:
    from app.services.voice_clone import list_voices
    from app.config import settings

    voices = await list_voices()
    if not voices:
        console.print("No custom voices found.")
        return

    table = Table(title="Available Voices")
    table.add_column("voice_id", style="bold")
    table.add_column("Name")
    table.add_column("Default", justify="center")

    for v in voices:
        vid = v.get("voice_id") or v.get("id") or "?"
        vname = v.get("name") or v.get("voice_name") or "?"
        is_default = "✓" if vid == settings.minimax_default_voice_id else ""
        table.add_row(vid, vname, is_default)

    console.print(table)


async def _warmup_async(voice_id: str) -> None:
    from app.services.voice_clone import warmup_voice

    console.print(f"Warming up voice: {voice_id}")
    ok = await warmup_voice(voice_id)
    if ok:
        console.print("[green]Warmup successful.[/green]")
    else:
        console.print("[red]Warmup failed — check logs.[/red]")
        raise typer.Exit(1)


# ── Entrypoint ────────────────────────────────────────────────────────────────

def main() -> None:
    app()


if __name__ == "__main__":
    main()
