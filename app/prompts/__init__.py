"""Prompt fragments loaded into agent system prompts.

Keep prompts as plain markdown so they're easy to diff and edit. Load via
`load_prompt(name)` — returns the raw text with the YAML/attribution header
stripped.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent


@lru_cache(maxsize=32)
def load_prompt(name: str) -> str:
    path = PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"No prompt fragment named {name!r} at {path}")
    text = path.read_text(encoding="utf-8")
    # Strip leading attribution block (everything up to first "---" divider after header)
    if text.startswith("<!--"):
        end = text.find("-->")
        if end != -1:
            text = text[end + 3 :].lstrip()
    return text.strip()
