"""Render every PDF page and produce visual-review sheets plus structural checks."""
import json
from pathlib import Path
import re

import pymupdf as fitz
from PIL import Image, ImageDraw
from pypdf import PdfReader


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
PDF = REPO / "output/pdf/Rebuilding_Your_Trading_Application_Expanded.pdf"
OUT = REPO / "tmp/pdfs/learning-book"
OUT.mkdir(parents=True, exist_ok=True)
document = fitz.open(PDF)
reader = PdfReader(PDF)
toc = document.get_toc()
chapters = [row for row in toc if re.match(r"Chapter \d+ ", row[1])]
assert len(chapters) == 60
assert sorted(int(re.search(r"Chapter (\d+)", row[1]).group(1)) for row in chapters) == list(range(1, 61))
issues = []
short_pages = []
page_rows = []
for index, page in enumerate(document):
    text = page.get_text()
    if len(text.strip()) < 100:
        short_pages.append(index + 1)
    if "\ufffd" in text or "{{include:" in text:
        issues.append({"page": index + 1, "issue": "replacement glyph or unresolved include"})
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                x0, y0, x1, y1 = span["bbox"]
                if x0 < 35 or x1 > page.rect.width - 35 or y0 < 10 or y1 > page.rect.height - 10:
                    issues.append({"page": index + 1, "issue": "text outside safe page area", "text": span["text"][:80]})
    pix = page.get_pixmap(matrix=fitz.Matrix(1.25, 1.25), alpha=False)
    pix.save(OUT / f"page-{index + 1:03d}.png")
    page_rows.append({"page": index + 1, "characters": len(text), "links": len(page.get_links())})

for start in range(0, len(document), 12):
    sheet = Image.new("RGB", (1200, 4 * 460), "#dde4e1")
    draw = ImageDraw.Draw(sheet)
    for offset in range(min(12, len(document) - start)):
        number = start + offset + 1
        image = Image.open(OUT / f"page-{number:03d}.png")
        image.thumbnail((380, 424))
        x = (offset % 3) * 400 + 10
        y = (offset // 3) * 460 + 24
        sheet.paste(image, (x, y))
        draw.text((x, y - 18), f"Page {number}", fill="black")
    sheet.save(OUT / f"contact-{start // 12 + 1:02d}.png")

report = {"pdf": str(PDF), "pages": len(document), "chapter_bookmarks": len(chapters),
          "issues": issues, "very_short_pages": short_pages, "page_details": page_rows,
          "chapter_pages": {row[1]: row[2] for row in chapters}}
(OUT / "audit.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps({key: report[key] for key in ["pages", "chapter_bookmarks", "issues", "very_short_pages"]}, indent=2))
assert not issues, issues
