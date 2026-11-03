"""The pages must not type the data window in: every date shown comes from the data files.

Why: the project is meant to be read for months after its data ends
(user, 2026-11-03). A typed "Sep 2025 – Aug 2026" goes stale the day the
window rolls; a label built from flows.json / reliability.json does not.
Comments are ignored (they record history). One dated fact is allowed: the
school-locations dataset vintage, which is a property of that dataset, not
of this project's window.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MONTH_YEAR = re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? (\d{1,2},? )?20\d\d\b")
ALLOWED = ("NYC DOE 2019",)


def code_lines(path: Path):
    """(line number, text) outside HTML comments and // or /* */ JS comments."""
    text = path.read_text()
    text = re.sub(r"<!--.*?-->", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
    text = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
    for n, line in enumerate(text.splitlines(), 1):
        line = re.sub(r"(^|\s)//.*$", "", line)
        yield n, line


def test_no_typed_window_dates_in_the_pages():
    found = []
    for page in ("dashboard.html", "video.html"):
        for n, line in code_lines(ROOT / page):
            if MONTH_YEAR.search(line) and not any(a in line for a in ALLOWED):
                found.append(f"{page}:{n}: {line.strip()[:120]}")
    assert not found, "dates typed into the page (build them from the data instead):\n" + "\n".join(found)


def test_the_check_catches_a_typed_window():
    assert MONTH_YEAR.search("fit across the full real Sep 2025–Aug 2026 year")
    assert MONTH_YEAR.search("availability Jul 13, 2026")
    assert not MONTH_YEAR.search("fit across the full real ${tripWindowLabel()} year")
