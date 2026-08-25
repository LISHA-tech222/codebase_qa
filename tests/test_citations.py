import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from validate_citations import parse_citations, validate_citations, strip_invalid_citations

CHUNKS = [
    {"file_path": "colorama/colorama/ansitowin32.py", "start_line": 72, "end_line": 277},
    {"file_path": "colorama/colorama/ansitowin32.py", "start_line": 175, "end_line": 182},
]


def test_parse_citations_extracts_all_tags():
    text = "See [a.py:1-5] and also [b.py:10-20]."
    assert parse_citations(text) == [("a.py", 1, 5), ("b.py", 10, 20)]


def test_validate_citations_flags_hallucinated_citation():
    """Regression test: an LLM answer with a mix of real + fabricated
    citations must correctly separate valid from invalid, matching the
    manual test that found this behavior in Step 6."""
    answer = (
        "Real claim [colorama/colorama/ansitowin32.py:72-277]. "
        "Fake claim [colorama/colorama/ansitowin32.py:300-310]."
    )
    report = validate_citations(answer, CHUNKS)
    assert report["has_hallucinated_citation"] is True
    assert (("colorama/colorama/ansitowin32.py", 300, 310) in report["invalid"])
    assert (("colorama/colorama/ansitowin32.py", 72, 277) in report["valid"])


def test_validate_citations_all_valid_when_grounded():
    answer = "Claim one [colorama/colorama/ansitowin32.py:72-277]."
    report = validate_citations(answer, CHUNKS)
    assert report["has_hallucinated_citation"] is False


def test_strip_invalid_citations_removes_only_the_fake_one():
    answer = (
        "Real claim [colorama/colorama/ansitowin32.py:72-277]. "
        "Fake claim [colorama/colorama/ansitowin32.py:300-310]."
    )
    cleaned = strip_invalid_citations(answer, CHUNKS)
    assert "[colorama/colorama/ansitowin32.py:72-277]" in cleaned
    assert "300-310" not in cleaned


def test_strip_invalid_citations_leaves_clean_punctuation():
    """Regression test: removing a citation shouldn't leave a stray
    space before the following period (caught during Step 6 build)."""
    answer = "Fake claim [colorama/colorama/ansitowin32.py:300-310]."
    cleaned = strip_invalid_citations(answer, CHUNKS)
    assert " ." not in cleaned
    assert cleaned.strip().endswith("claim.")
