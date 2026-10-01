"""The regex honesty gate: one list of claims the candidate may never make.

The list lives in the profile (honesty.banned and honesty.letter_only). Tests
scan the claim surface with it (profile text, CV copy, the writer's facts);
writer.py, reviewer.py and kit.py run it over every finished letter as the
FINAL step, after any model has had its say; lint.py runs it over the letter
and the CV text on disk.
"""
import re

from pipeline import profile


def banned() -> list[tuple[str, str]]:
    """(pattern, reason) pairs that may appear nowhere: sources, CV, letters."""
    return list(profile.get()["honesty"]["banned"])


def letter_only() -> list[tuple[str, str]]:
    """(pattern, reason) pairs that only matter in finished letters and PDFs."""
    return list(profile.get()["honesty"]["letter_only"])


def gate(text: str) -> list[str]:
    """Reasons a finished text may not ship; an empty list means clean."""
    hits = []
    for pattern, why in banned() + letter_only():
        m = re.search(pattern, text or "", flags=re.I)
        if m:
            hits.append(f"{why}: …{text[max(0, m.start() - 30):m.end() + 30]!r}…")
    return hits
