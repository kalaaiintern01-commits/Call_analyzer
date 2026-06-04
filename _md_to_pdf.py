"""One-shot: convert KNOWLEDGE_BASE.md to KNOWLEDGE_BASE.pdf via Edge headless.

Uses Python's `markdown` library to produce HTML, wraps it in a CSS template
with Devanagari-capable font fallback (Nirmala UI is shipped with Windows),
then invokes Edge's headless `--print-to-pdf` to render the HTML to a PDF.

Edge is used (not Chrome) because Chrome wasn't found on PATH but Edge is
guaranteed on Windows 10+. Edge's print engine is identical Chromium under
the hood — Devanagari just works.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import markdown

HERE = Path(__file__).resolve().parent
MD_PATH = HERE / "KNOWLEDGE_BASE.md"
HTML_PATH = HERE / "KNOWLEDGE_BASE.html"
PDF_PATH = HERE / "KNOWLEDGE_BASE.pdf"
EDGE_PATH = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")

CSS = """
@page {
    size: A4;
    margin: 18mm 15mm;
}
body {
    /* Latin font + Devanagari fallback (Nirmala UI ships with Win 10+) */
    font-family: "Segoe UI", "Helvetica Neue", Helvetica, Arial,
                 "Nirmala UI", "Noto Sans Devanagari", "Mangal", sans-serif;
    color: #1a1a1a;
    line-height: 1.55;
    font-size: 10.5pt;
    max-width: 100%;
}
h1 {
    font-size: 22pt;
    color: #1a1a1a;
    border-bottom: 2px solid #1a1a1a;
    padding-bottom: 0.3em;
    margin-top: 0;
    page-break-after: avoid;
}
h2 {
    font-size: 16pt;
    color: #2a2a2a;
    border-bottom: 1px solid #d0d0d0;
    padding-bottom: 0.2em;
    margin-top: 1.4em;
    page-break-after: avoid;
}
h3 {
    font-size: 13pt;
    color: #2a2a2a;
    margin-top: 1.2em;
    page-break-after: avoid;
}
h4 {
    font-size: 11.5pt;
    color: #2a2a2a;
    margin-top: 1em;
    page-break-after: avoid;
}
p, ul, ol {
    margin: 0.6em 0;
}
ul, ol {
    padding-left: 1.6em;
}
li {
    margin: 0.25em 0;
}
strong {
    color: #1a1a1a;
}
code {
    font-family: "Consolas", "SF Mono", Menlo, "Courier New", monospace;
    background: #f3f3f3;
    padding: 0.1em 0.35em;
    border-radius: 3px;
    font-size: 0.92em;
    color: #b91d47;
}
pre {
    background: #f5f5f5;
    border: 1px solid #e0e0e0;
    border-radius: 4px;
    padding: 0.8em 1em;
    overflow-x: auto;
    font-size: 9.5pt;
    page-break-inside: avoid;
}
pre code {
    background: none;
    padding: 0;
    color: #1a1a1a;
}
blockquote {
    border-left: 3px solid #c0c0c0;
    margin: 0.8em 0;
    padding: 0.2em 0 0.2em 1em;
    color: #555;
    background: #fafafa;
}
table {
    border-collapse: collapse;
    width: 100%;
    margin: 0.8em 0;
    page-break-inside: avoid;
    font-size: 9.5pt;
}
th, td {
    border: 1px solid #d0d0d0;
    padding: 0.45em 0.7em;
    text-align: left;
    vertical-align: top;
}
th {
    background: #f0f0f0;
    font-weight: 600;
    color: #1a1a1a;
}
tr:nth-child(even) td {
    background: #fafafa;
}
a {
    color: #0366d6;
    text-decoration: none;
}
hr {
    border: 0;
    border-top: 1px solid #d0d0d0;
    margin: 1.5em 0;
}
"""


def main() -> int:
    if not MD_PATH.exists():
        print(f"ERROR: {MD_PATH} not found", file=sys.stderr)
        return 1
    if not EDGE_PATH.exists():
        print(f"ERROR: Edge not found at {EDGE_PATH}", file=sys.stderr)
        return 1

    print("Reading markdown...")
    md_text = MD_PATH.read_text(encoding="utf-8")

    print("Converting markdown to HTML...")
    html_body = markdown.markdown(
        md_text,
        extensions=["extra", "tables", "fenced_code", "sane_lists", "toc"],
        output_format="html5",
    )

    full_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Shivangi Knowledge Base — Kala Genset</title>
  <style>{CSS}</style>
</head>
<body>
{html_body}
</body>
</html>
"""

    print(f"Writing HTML to {HTML_PATH}")
    HTML_PATH.write_text(full_html, encoding="utf-8")

    print(f"Invoking Edge headless to print to {PDF_PATH}")
    # --headless=new is the current flag (older Chromium used --headless).
    # --no-pdf-header-footer keeps the output clean (no URL/date headers).
    # file:// URI is required to load a local file.
    cmd = [
        str(EDGE_PATH),
        "--headless=new",
        "--disable-gpu",
        "--no-pdf-header-footer",
        f"--print-to-pdf={PDF_PATH}",
        HTML_PATH.as_uri(),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        print("Edge stderr:", result.stderr, file=sys.stderr)
        print("Edge stdout:", result.stdout, file=sys.stderr)
        return result.returncode

    if PDF_PATH.exists():
        size_kb = PDF_PATH.stat().st_size / 1024
        print(f"\n✓ Generated {PDF_PATH} ({size_kb:.1f} KB)")
        return 0
    else:
        print("ERROR: Edge ran but no PDF was produced", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
