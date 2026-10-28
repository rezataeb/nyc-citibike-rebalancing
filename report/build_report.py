"""Build the project report PDF from report/report.html with headless Chrome.

Method: report.html is a plain HTML page styled for print by report.css.
Chrome's own print-to-PDF engine renders it, so the PDF is exactly what the
page looks like in Chrome's print preview -- no LaTeX, no extra Python
dependency. Same approach as the earlier docs V2 doc set.

Usage:
    python3 -m report.build_report
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

REPORT_DIR = Path(__file__).resolve().parent
HTML_PATH = REPORT_DIR / "report.html"
PDF_PATH = REPORT_DIR / "Citi_Bike_Rebalancing_Report.pdf"

# Checked in order; the first one that exists is used.
CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "google-chrome",
    "chromium",
    "chromium-browser",
]


def find_chrome(candidates: list[str] = CHROME_CANDIDATES) -> str | None:
    """Return the first Chrome executable found, or None.

    An absolute path counts if the file exists; a bare name counts if it is
    on PATH.
    """
    for candidate in candidates:
        if os.path.isabs(candidate):
            if Path(candidate).exists():
                return candidate
        elif shutil.which(candidate):
            return shutil.which(candidate)
    return None


def chrome_pdf_command(chrome: str, html_path: Path, pdf_path: Path) -> list[str]:
    """Build the headless-Chrome command that prints html_path to pdf_path.

    --no-pdf-header-footer drops Chrome's default date/URL/page-number
    header and footer, so the page margins come only from report.css's
    @page rule.
    """
    return [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--no-pdf-header-footer",
        f"--print-to-pdf={pdf_path}",
        html_path.resolve().as_uri(),
    ]


def render_pdf(html_path: Path = HTML_PATH, pdf_path: Path = PDF_PATH) -> Path:
    """Render html_path to pdf_path and return pdf_path.

    Raises RuntimeError if Chrome is not installed or if it exits without
    writing the PDF.
    """
    chrome = find_chrome()
    if chrome is None:
        raise RuntimeError("Google Chrome / Chromium not found; it is needed to print the PDF.")
    if pdf_path.exists():
        pdf_path.unlink()
    subprocess.run(
        chrome_pdf_command(chrome, html_path, pdf_path),
        check=True,
        capture_output=True,
    )
    if not pdf_path.exists():
        raise RuntimeError(f"Chrome exited but did not write {pdf_path}.")
    return pdf_path


if __name__ == "__main__":
    out = render_pdf()
    print(f"Wrote {out} ({out.stat().st_size:,} bytes)")
