"""Capability 2: answer questions about the user's notes (a folder of .md/.txt files)."""

from __future__ import annotations

import re
from pathlib import Path

from ..config import ROOT

EXTENSIONS = {".md", ".txt"}


def _note_files(notes_dir: Path) -> list[Path]:
    if not notes_dir.exists():
        return []
    return sorted(p for p in notes_dir.rglob("*") if p.is_file() and p.suffix.lower() in EXTENSIONS)


def search_notes(notes_dir: Path, query: str, limit: int = 8) -> list[tuple[Path, int, str]]:
    """Return (file, line_number, line) for lines matching any query word, best files first."""
    words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 2]
    if not words:
        return []
    hits: list[tuple[int, Path, int, str]] = []
    for path in _note_files(notes_dir):
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            low = line.lower()
            score = sum(1 for w in words if w in low)
            if score:
                hits.append((score, path, n, line.strip()))
    hits.sort(key=lambda h: (-h[0], str(h[1]), h[2]))
    return [(p, n, line) for _, p, n, line in hits[:limit]]


def register(registry, config) -> None:
    notes_dir = config.path("notes", "dir", ROOT / "notes")

    @registry.add(
        "search_notes",
        "Use this to find passages in the user's personal notes that mention a topic. Use it before answering any "
        "question about their notes, plans, projects, or things they wrote down. Returns matching lines with file names.",
        {"type": "object", "properties": {"query": {"type": "string", "description": "Keywords to look for."}},
         "required": ["query"]},
    )
    def _search(args):
        hits = search_notes(notes_dir, args["query"])
        if not hits:
            files = _note_files(notes_dir)
            if not files:
                return f"There are no notes yet in {notes_dir}. Tell the user where to put .md or .txt files."
            return "No notes mention that."
        return "\n".join(f"{p.relative_to(notes_dir)}:{n}: {line}" for p, n, line in hits)

    @registry.add(
        "read_note",
        "Use this to read one whole note by its file name (as returned by search_notes) when a snippet isn't enough.",
        {"type": "object", "properties": {"name": {"type": "string", "description": "Relative file name, e.g. projects.md"}},
         "required": ["name"]},
    )
    def _read(args):
        target = (notes_dir / args["name"]).resolve()
        if not target.is_relative_to(notes_dir.resolve()):
            raise ValueError("that path is outside the notes folder")
        if not target.exists():
            raise FileNotFoundError(f"no note called {args['name']}")
        text = target.read_text(encoding="utf-8", errors="replace")
        return text[:8000] + ("\n[truncated]" if len(text) > 8000 else "")
