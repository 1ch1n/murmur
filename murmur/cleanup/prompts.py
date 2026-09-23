from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional


# Per-category guidance injected into the cleanup prompt.
# Keep tight, long category prompts dilute the core rules.
CATEGORY_GUIDANCE: dict[str, str] = {
    "code": (
        "You are in a code editor. Preserve identifiers, snake_case, camelCase, "
        "punctuation, brackets, and operators exactly as spoken (e.g. 'open paren', "
        "'dot', 'underscore'). Do not add prose punctuation that would break code."
    ),
    "terminal": (
        "You are in a terminal. Output plain commands with no trailing period. "
        "Preserve flags (--foo, -x), pipes, and redirects literally."
    ),
    "chat": (
        "You are in a casual chat app. Drop trailing periods from one-line messages. "
        "Keep contractions. Preserve emojis and @mentions exactly."
    ),
    "mail": (
        "You are in an email client. Use full sentences, complete punctuation, "
        "and a polite tone, but do not formalize the user's natural voice."
    ),
    "doc": (
        "You are in a long-form document. Use clean paragraph structure, "
        "full punctuation, and Oxford-style commas where natural."
    ),
    "browser": (
        "You are in a browser. The active field could be anything; default to "
        "complete sentences but keep search-box phrasing terse if it reads like a query."
    ),
    "other": "",
}


@dataclass
class CleanupContext:
    target_app: Optional[str] = None          # process name e.g. "code.exe"
    target_category: str = "other"            # code / chat / mail / doc / terminal / browser / other
    window_title: Optional[str] = None
    dictionary: Iterable[str] = ()            # proper nouns / custom vocab


SYSTEM_PROMPT = (
    "You clean up voice dictation. Apply these rules and output ONLY the cleaned text "
    "with no preamble, no markdown, no quotes, no commentary.\n"
    "1. Remove filler: 'um', 'uh', 'like', 'you know', 'I mean', 'sort of', "
    "'kind of', 'basically', 'actually', 'right' (when used as filler), 'so' "
    "(sentence-initial).\n"
    "2. Add punctuation and casing from the natural pauses and intonation.\n"
    "3. Fix obvious grammar and disfluencies without changing meaning.\n"
    "4. Honor backtracking corrections: if the user says 'wait, actually change X to Y', "
    "apply Y and drop X.\n"
    "5. Preserve technical terms, proper nouns, code identifiers, URLs, file paths, "
    "and numbers exactly.\n"
    "6. Preserve the user's voice. Do not sanitize personality or formalize tone.\n"
)


def build_prompt(transcript: str, ctx: CleanupContext) -> str:
    parts: list[str] = [SYSTEM_PROMPT]

    cat_hint = CATEGORY_GUIDANCE.get(ctx.target_category, "")
    if cat_hint:
        parts.append(f"Context: {cat_hint}")

    if ctx.target_app:
        parts.append(f"Target application: {ctx.target_app}")

    if ctx.dictionary:
        vocab = ", ".join(sorted({w.strip() for w in ctx.dictionary if w.strip()}))
        if vocab:
            parts.append(
                "Preserve these terms exactly if you hear close variants: " + vocab
            )

    parts.append("\nRaw transcript:")
    parts.append(transcript)

    return "\n\n".join(parts)
