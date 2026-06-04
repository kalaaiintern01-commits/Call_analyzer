"""One-shot dump of catalog PDFs → text for building PRODUCT_KNOWLEDGE."""
import pdfplumber
from pathlib import Path

CATALOGS = Path(r"c:\Users\Varun Bhilare\Desktop\Kala\Kala\genset-call-analyzer\genset-call-analyzer\catalogs")

# Sample the most-common-range catalogs to keep token spend reasonable.
to_read = [
    "2_CPCB IV+25-58.5 kVA.pdf",
    "3_CPCB IV+82.5-160 kVA.pdf",
    "1_CPCB IV+7.5-20 kVA.pdf",
]
MAX_PAGES = 6

for name in to_read:
    p = CATALOGS / name
    print(f"\n{'='*70}\n{name}\n{'='*70}")
    with pdfplumber.open(p) as pdf:
        n = min(MAX_PAGES, len(pdf.pages))
        for i in range(n):
            txt = pdf.pages[i].extract_text() or ""
            # Collapse heavy whitespace
            txt = "\n".join(line.strip() for line in txt.splitlines() if line.strip())
            print(f"\n--- page {i+1}/{len(pdf.pages)} ---\n{txt}")
