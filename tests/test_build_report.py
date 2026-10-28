"""Tests for report/build_report.py and the structure of report/report.html.

The structure tests guard the report's required sections (Session 72's
roadmap step 3): Limitations including the Phase 5 closeout, Future steps,
and the AI/LLM section. The one real render test is skipped when Chrome is
not installed.
"""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

import pytest

from report.build_report import (
    HTML_PATH,
    chrome_pdf_command,
    find_chrome,
    render_pdf,
)

REQUIRED_SECTION_IDS = [
    "summary",
    "problem",
    "data-methods",
    "findings",
    "limitations",
    "gbfs-sampling",
    "phase5-closeout",
    "live-mode-caveats",
    "future-steps",
    "ai-llm",
]


class _IdCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.section_ids: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "section":
            for name, value in attrs:
                if name == "id":
                    self.section_ids.append(value)


def _section_ids(html: str) -> list[str]:
    parser = _IdCollector()
    parser.feed(html)
    return parser.section_ids


def test_report_has_every_required_section():
    ids = _section_ids(HTML_PATH.read_text(encoding="utf-8"))
    missing = [s for s in REQUIRED_SECTION_IDS if s not in ids]
    assert missing == []


def test_phase5_closeout_sits_inside_limitations():
    # The roadmap requires the closeout as a limitation, not a footnote
    # elsewhere: it must come after "limitations" and before the next h2 section.
    ids = _section_ids(HTML_PATH.read_text(encoding="utf-8"))
    assert ids.index("limitations") < ids.index("phase5-closeout") < ids.index("future-steps")


def test_find_chrome_returns_none_when_nothing_exists(tmp_path):
    assert find_chrome([str(tmp_path / "no-such-chrome"), "no-such-binary-xyz"]) is None


def test_find_chrome_accepts_existing_absolute_path(tmp_path):
    fake = tmp_path / "chrome"
    fake.write_text("")
    assert find_chrome([str(fake)]) == str(fake)


def test_chrome_pdf_command_prints_to_target_without_header_footer(tmp_path):
    html = tmp_path / "r.html"
    pdf = tmp_path / "r.pdf"
    cmd = chrome_pdf_command("chrome", html, pdf)
    assert cmd[0] == "chrome"
    assert f"--print-to-pdf={pdf}" in cmd
    assert "--no-pdf-header-footer" in cmd
    assert cmd[-1].startswith("file://")


@pytest.mark.skipif(find_chrome() is None, reason="Chrome not installed")
def test_render_pdf_writes_a_real_pdf(tmp_path):
    out = render_pdf(HTML_PATH, tmp_path / "report.pdf")
    assert out.read_bytes()[:5] == b"%PDF-"
