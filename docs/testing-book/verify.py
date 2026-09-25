"""Check and render the entire generated PDF for visual review."""
import json
from pathlib import Path
import re

import pymupdf
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
OUT = REPO / "tmp/pdfs/testing-book"
OUT.mkdir(parents=True, exist_ok=True)
document = pymupdf.open(REPO / "output/pdf/Software_Testing_Beginner_to_SDET.pdf")
chapters = [row for row in document.get_toc() if re.match(r"Chapter \d+\.", row[1])]
assert len(chapters) == 40
issues, page_rows = [], []
for number, page in enumerate(document, 1):
    text = page.get_text()
    if len(text.strip()) < 100:
        issues.append({"page": number, "issue": "near-empty page"})
    if "\ufffd" in text:
        issues.append({"page": number, "issue": "replacement glyph"})
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                x0, y0, x1, y1 = span["bbox"]
                if x0 < 34 or x1 > page.rect.width - 34 or y0 < 10 or y1 > page.rect.height - 10:
                    issues.append({"page": number, "issue": "text outside safe bounds", "text": span["text"]})
    page.get_pixmap(matrix=pymupdf.Matrix(1.3, 1.3), alpha=False).save(OUT / f"page-{number:03d}.png")
    page_rows.append({"page": number, "characters": len(text), "links": len(page.get_links())})
for start in range(0, len(document), 12):
    sheet = Image.new("RGB", (1200, 1840), "#d9e3dc")
    draw = ImageDraw.Draw(sheet)
    for offset in range(min(12, len(document) - start)):
        number = start + offset + 1
        with Image.open(OUT / f"page-{number:03d}.png") as img:
            img.thumbnail((380, 424))
            x, y = offset % 3 * 400 + 10, offset // 3 * 460 + 25
            sheet.paste(img, (x, y))
            draw.text((x, y - 19), f"Page {number}", fill="black")
    sheet.save(OUT / f"contact-{start // 12 + 1:02d}.png")
report = {"pages": len(document), "chapter_bookmarks": len(chapters), "issues": issues,
          "chapter_pages": {row[1]: row[2] for row in chapters}, "page_details": page_rows}
(OUT / "audit.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps({k: report[k] for k in ("pages", "chapter_bookmarks", "issues")}, indent=2))
assert not issues
