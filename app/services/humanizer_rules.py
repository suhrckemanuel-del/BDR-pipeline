"""
humanizer_rules.py — 29-rule anti-AI text filter.

Based on github.com/blader/humanizer and Wikipedia's AI Cleanup project.
Strips statistically common LLM output patterns that signal mass-generated text.

Two-pass process:
  1. Phrase-level replacements (must run before word-level to avoid partial matches)
  2. Word-level substitutions
  3. Structural sentence fixes
  4. Whitespace normalisation

Usage:
    from app.services.humanizer_rules import humanize, humanize_sequence
    clean_body = humanize(raw_body)
    clean_seq  = humanize_sequence(sequence)
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Rule sets — ordered: phrase-level first, word-level second
# ---------------------------------------------------------------------------

# Phrase-level (Rule 23, 24, 25, 26, 27, 28, 29 and others)
PHRASE_RULES: list[tuple[str, str]] = [
    # Opener killers
    (r"I hope this (?:message |email )?finds you well\.?\s*", ""),
    (r"I hope this (?:message |email )?finds you\.?\s*", ""),
    (r"I wanted to reach out\b", "I'm writing"),
    (r"I am reaching out\b", "I'm writing"),
    # Touch-base / circle-back
    (r"\btouch base\b", "connect"),
    (r"\bcircle back\b", "follow up"),
    # Temporal filler (Rule 28, 29)
    (r"\bmoving forward\b", ""),
    (r"\bgoing forward\b", ""),
    # Hedge collapse (Rule 24)
    (r"\bcould potentially\b", "may"),
    (r"\bmight potentially\b", "may"),
    (r"\bmay potentially\b", "may"),
    # Phrase filler (Rule 23)
    (r"\bin order to\b", "to"),
    (r"\bthe fact that\b", ""),
    (r"\bit goes without saying\b", ""),
    (r"\bneedless to say\b", ""),
    (r"\bplease don'?t hesitate to\b", ""),
    (r"\bfeel free to\b", ""),
    (r"\bI would like to\b", "I want to"),
    (r"\bat the end of the day\b", ""),
    (r"\ball in all\b", ""),
    (r"\bwhat's more\b", ""),
    (r"\bon top of that\b", ""),
    (r"\brest assured\b", ""),
]

# Word-level substitutions (Rule 7, 9, 11, 15–22)
WORD_RULES: list[tuple[str, str]] = [
    # Filler adverbs / connectors (Rule 7)
    (r"\bactually\b", ""),
    (r"\badditionally\b", ""),
    (r"\bfurthermore\b", ""),
    (r"\bmoreover\b", ""),
    (r"\bnotably\b", ""),
    (r"\bessentially\b", ""),
    (r"\bactively\b", ""),
    (r"\bultimately\b", ""),
    (r"\boverall\b", ""),
    # Buzzwords (Rule 15–22)
    (r"\butilize[sd]?\b", "use"),
    (r"\butilization\b", "use"),
    (r"\bleveraging\b", "using"),
    (r"\bleveraged\b", "used"),
    (r"\bleverages?\b(?!\s+ratio)", "use"),  # keep "leverage ratio"
    (r"\bsynerg(?:y|ies|istic)\b", ""),
    (r"\bholistic\b", ""),
    (r"\bparadigm\b", ""),
    (r"\binnovati(?:ve|on|ons)\b", ""),
    (r"\btransformati(?:ve|on)\b", ""),
    (r"\bseamlessly?\b", ""),
    (r"\bcutting[-\s]edge\b", ""),
    (r"\bgame[-\s]chang(?:ing|er)\b", ""),
    (r"\bworld[-\s]class\b", ""),
    (r"\bbest[-\s]in[-\s]class\b", ""),
    (r"\bempower(?:ing|s|ed|ment)?\b", ""),
    (r"\bunlock(?:s|ing|ed)?\b", ""),
    (r"\becosystem\b", ""),
    (r"\brobust\b", ""),
    (r"\bscalable\b", ""),
    (r"\blandscape\b", ""),        # Rule 7
    (r"\bshowcas(?:ing|es?)\b", "showing"),
    (r"\bstreamlin(?:e[sd]?|ing)\b", "simplify"),
    (r"\bpivot(?:ing|ed|s)?\b", "shift"),
]

# Structural fixes (Rule 9, 14)
STRUCTURAL_RULES: list[tuple[str, str]] = [
    # Rule 9: "not just X, it's/it is Y" → state directly
    (r"\bnot just ([^,\.]{3,40}),\s*it(?:'?s| is)\b", r"\1, and"),
    # Rule 14: em dash / en dash → comma
    (r"\s*[—–]\s*", ", "),
    # Trailing comma before sentence-end punctuation
    (r",\s*([\.!?])", r"\1"),
    # Leading comma at sentence start (per line)
    (r"^,\s*", ""),
    # Collapse any run of commas (result of multiple word removals) into one
    (r",(\s*,)+", ","),
    # Space before punctuation
    (r" +([,\.;:!?])", r"\1"),
    # Multiple spaces
    (r" {2,}", " "),
    # Double periods
    (r"\.{2,}", "."),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def humanize(text: str) -> str:
    """Apply all 29 rules to a single text block."""
    if not text:
        return text

    result = text

    # Pass 1 — phrase-level
    for pattern, replacement in PHRASE_RULES:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)

    # Pass 2 — word-level
    for pattern, replacement in WORD_RULES:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)

    # Pass 3 — structural
    for pattern, replacement in STRUCTURAL_RULES:
        result = re.sub(pattern, replacement, result, flags=re.MULTILINE | re.IGNORECASE)

    # Pass 4 — final whitespace normalisation
    lines = []
    for line in result.splitlines():
        line = line.strip()
        # Drop lines that are now empty or just punctuation after rule stripping
        if line in ("", ",", ".", " "):
            lines.append("")
        else:
            lines.append(line)
    result = "\n".join(lines).strip()

    # Collapse more than 2 consecutive blank lines
    result = re.sub(r"\n{3,}", "\n\n", result)

    return result


def humanize_sequence(sequence) -> object:
    """Apply humanize() to every touch in an OutreachSequence. Returns the sequence."""
    for touch in sequence.touches:
        touch.body = humanize(touch.body)
        if touch.subject:
            touch.subject = humanize(touch.subject)
        touch.word_count = len(touch.body.replace("\n", " ").split())
    return sequence


def humanize_angle_draft(draft) -> object:
    """Apply humanize() to an AngleDraft email body, DM, and subject."""
    draft.email_body = humanize(draft.email_body)
    draft.dm = humanize(draft.dm)
    draft.email_subject = humanize(draft.email_subject)
    return draft
