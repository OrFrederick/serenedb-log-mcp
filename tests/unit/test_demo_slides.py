"""`docs/demo/slides.md` (T24): every number on a slide comes from the file its line cites.

A slide line that holds a number must cite `docs/results/<file>` or `docs/worked-investigation.md`,
and every number of the line must appear in a cited file exactly as written on the slide. Identifiers
are not numbers: ticket and PR ids (T18, #86), names with digits (Loghub-2.0, E<n>) and the slide
headings.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
SLIDES = ROOT / "docs" / "demo" / "slides.md"
SOURCES = re.compile(r"docs/results/[\w.-]+\.md|docs/worked-investigation\.md")
NUMBER = re.compile(r"(?<![\w.,#<-])\d[\d,]*(?:\.\d+)?%?(?![\w-])")
CODE = re.compile(r"`[^`]*`")


def slide_lines() -> list[str]:
    """The bullet lines of the slides, without the preamble and the headings."""
    text = SLIDES.read_text(encoding="utf-8")
    body = text[text.index("## Slide 1") :]
    return [line for line in body.splitlines() if line.startswith("- ")]


def numbers(line: str) -> list[str]:
    plain = CODE.sub("", SOURCES.sub("", line))
    return [n.rstrip(".,") for n in NUMBER.findall(plain)]


def test_the_number_pattern_skips_identifiers() -> None:
    line = (
        "- T18 and PR #86 on Loghub-2.0 cite [E<n>]: 4 of 4, 2,066 at 99.99991% and 4/5 (docs/results/x.md)."
    )

    assert numbers(line) == ["4", "4", "2,066", "99.99991%", "4", "5"]


def test_every_number_on_a_slide_cites_a_results_file_that_holds_it() -> None:
    checked = 0
    for line in slide_lines():
        found = numbers(line)
        if not found:
            continue
        cited = SOURCES.findall(line)
        assert cited, f"a slide number without a source: {line}"
        texts = [(ROOT / path).read_text(encoding="utf-8") for path in cited if (ROOT / path).exists()]
        assert texts, f"the cited file does not exist yet: {line}"
        for number in found:
            pattern = re.compile(rf"(?<![\d.,]){re.escape(number)}(?![\d,]|\.\d)")
            assert any(pattern.search(text) for text in texts), f"{number} is not in {cited}: {line}"
            checked += 1
    assert checked > 0
