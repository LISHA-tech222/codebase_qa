"""
Parse citations out of an LLM's answer and validate each one against the
actual chunks that were retrieved — catches the model inventing a
plausible-looking [file.py:10-20] that doesn't match anything it was
actually given.
"""

import re

CITATION_RE = re.compile(r"\[([^\[\]:]+):(\d+)-(\d+)\]")


def parse_citations(answer: str) -> list[tuple[str, int, int]]:
    return [
        (file_path, int(start), int(end))
        for file_path, start, end in CITATION_RE.findall(answer)
    ]


def validate_citations(answer: str, retrieved_chunks: list[dict]) -> dict:
    """
    Returns a report: which citations are valid (exactly match a
    retrieved chunk's file_path + line range) vs. invalid (hallucinated
    or altered).
    """
    known = {
        (c["file_path"], c["start_line"], c["end_line"])
        for c in retrieved_chunks
    }
    cited = parse_citations(answer)

    valid = [c for c in cited if c in known]
    invalid = [c for c in cited if c not in known]

    return {
        "total_citations": len(cited),
        "valid": valid,
        "invalid": invalid,
        "has_hallucinated_citation": len(invalid) > 0,
    }


def strip_invalid_citations(answer: str, retrieved_chunks: list[dict]) -> str:
    """
    Remove any citation bracket that doesn't match a retrieved chunk,
    silently. The surrounding prose is left intact — only the bogus
    [file:start-end] tag itself is dropped, so a hallucinated citation
    doesn't get shown to the user as if it were verified, but the
    sentence it was attached to still reads naturally.

    Note: this doesn't un-say a false CLAIM the model made (e.g. "there's
    a reset_all method") — it only removes the fabricated citation tag.
    A claim with no citation at all is a separate signal worth surfacing
    in the UI (e.g. visually distinct from cited claims), not something
    this function tries to solve.
    """
    known = {
        (c["file_path"], c["start_line"], c["end_line"])
        for c in retrieved_chunks
    }

    def _replace(match: re.Match) -> str:
        file_path, start, end = match.group(1), int(match.group(2)), int(match.group(3))
        if (file_path, start, end) in known:
            return match.group(0)  # keep valid citations as-is
        return ""  # drop invalid ones

    cleaned = CITATION_RE.sub(_replace, answer)
    # collapse any double-space left behind where a citation was removed
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    # tidy "word ." left behind when a citation sat right before punctuation
    cleaned = re.sub(r" +([.,;:])", r"\1", cleaned)
    return cleaned
