"""Typeset the expanded textbook with bookmarks, contents and page numbers."""
from html import escape
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

import markdown
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate, Flowable, Frame, KeepTogether, PageBreak, PageTemplate,
    Paragraph, Spacer, Table, TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
OUT = REPO / "output" / "pdf"
OUT.mkdir(parents=True, exist_ok=True)
TARGET = OUT / "Rebuilding_Your_Trading_Application_Expanded.pdf"
WIDTH, HEIGHT = A4
MARGIN = 49
CONTENT = WIDTH - 2 * MARGIN
FONTDIR = Path("C:/Windows/Fonts")

for name, filename in [
    ("Book", "georgia.ttf"), ("BookBold", "georgiab.ttf"),
    ("BookItalic", "georgiai.ttf"), ("Heading", "arial.ttf"),
    ("HeadingBold", "arialbd.ttf"), ("Code", "consola.ttf"),
]:
    pdfmetrics.registerFont(TTFont(name, str(FONTDIR / filename)))
pdfmetrics.registerFontFamily("Book", normal="Book", bold="BookBold", italic="BookItalic", boldItalic="BookBold")
pdfmetrics.registerFontFamily("Heading", normal="Heading", bold="HeadingBold", italic="Heading", boldItalic="HeadingBold")

styles = {
    "body": ParagraphStyle("Body", fontName="Book", fontSize=10.2, leading=14.7,
                           spaceAfter=7, allowWidows=False, allowOrphans=False,
                           textColor=colors.HexColor("#252b2d"), splitLongWords=True),
    "h1": ParagraphStyle("Part", fontName="HeadingBold", fontSize=24, leading=29,
                         spaceBefore=12, spaceAfter=19, keepWithNext=True),
    "h2": ParagraphStyle("Chapter", fontName="HeadingBold", fontSize=18, leading=23,
                         spaceBefore=8, spaceAfter=15, keepWithNext=True),
    "h3": ParagraphStyle("Section", fontName="HeadingBold", fontSize=12.5, leading=17,
                         spaceBefore=13, spaceAfter=7, keepWithNext=True),
    "small": ParagraphStyle("Small", fontName="Heading", fontSize=8.5, leading=12,
                            spaceAfter=6, textColor=colors.HexColor("#52615e")),
    "cell": ParagraphStyle("Cell", fontName="Heading", fontSize=8.1, leading=11.5,
                           spaceAfter=0, splitLongWords=True),
    "code_label": ParagraphStyle("CodeLabel", fontName="HeadingBold", fontSize=7.4, leading=10,
                                 textColor=colors.HexColor("#28634e"), spaceBefore=8,
                                 spaceAfter=3, keepWithNext=True),
}


class CodeLine(Flowable):
    def __init__(self, text):
        super().__init__()
        self.text = text
        self.width = CONTENT
        self.height = 10.6

    def draw(self):
        self.canv.setFillColor(colors.HexColor("#eff4f2"))
        self.canv.rect(0, 0, self.width, self.height, fill=1, stroke=0)
        self.canv.setFillColor(colors.HexColor("#21352d"))
        self.canv.setFont("Code", 7.8)
        self.canv.drawString(9, 2.5, self.text)


class LearningDiagram(Flowable):
    def __init__(self):
        super().__init__()
        self.width = CONTENT
        self.height = 140

    def draw(self):
        labels = [("Python", "Model and calculate"), ("APIs and identity", "Validate and authorize"),
                  ("Durable state", "Record and reconcile"), ("Testing", "Prove and challenge"),
                  ("Cloud and Kubernetes", "Deploy and recover"), ("Data and AI", "Analyze without trading authority")]
        for index, (title, subtitle) in enumerate(labels):
            col, row = index % 2, index // 2
            x, y = col * (CONTENT / 2 + 5), 97 - row * 45
            self.canv.setFillColor(colors.HexColor("#eef4f0" if index != 5 else "#f4edf2"))
            self.canv.roundRect(x, y, CONTENT / 2 - 6, 37, 3, fill=1, stroke=0)
            self.canv.setFillColor(colors.HexColor("#233c30"))
            self.canv.setFont("HeadingBold", 10)
            self.canv.drawString(x + 10, y + 22, title)
            self.canv.setFont("Heading", 8)
            self.canv.drawString(x + 10, y + 8, subtitle)


class BookDocument(BaseDocTemplate):
    def __init__(self, filename):
        super().__init__(str(filename), pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN,
                         topMargin=48, bottomMargin=48, title="Rebuilding Your Trading Application",
                         author="Prepared for Amol", allowSplitting=True)
        frame = Frame(MARGIN, 48, CONTENT, HEIGHT - 96, leftPadding=0, rightPadding=0,
                      topPadding=0, bottomPadding=0)
        self.addPageTemplates(PageTemplate(id="Book", frames=frame, onPage=self.page_header))
        self.current_part = "Rebuilding Your Trading Application"

    def beforeDocument(self):
        self.current_part = "Rebuilding Your Trading Application"

    def page_header(self, canvas, doc):
        canvas.saveState()
        if doc.page > 1:
            canvas.setFillColor(colors.HexColor("#596963"))
            canvas.setFont("Heading", 8)
            canvas.drawString(MARGIN, HEIGHT - 27, "Trading Application Engineering | Expanded Edition")
            canvas.drawRightString(WIDTH - MARGIN, 27, str(doc.page))
        canvas.restoreState()

    def afterFlowable(self, flowable):
        if hasattr(flowable, "book_key"):
            key = flowable.book_key
            level = flowable.book_level
            text = flowable.getPlainText()
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(text, key, level=level, closed=level == 0)
            self.notify("TOCEntry", (level, text, self.page, key))


def inline(element):
    result = escape(element.text or "")
    for child in element:
        content = inline(child)
        if child.tag in {"strong", "b"}:
            content = "<b>" + content + "</b>"
        elif child.tag in {"em", "i"}:
            content = "<i>" + content + "</i>"
        elif child.tag == "code":
            content = '<font name="Code" size="9">' + content + '</font>'
        elif child.tag == "a":
            href = child.get("href", "")
            if href.startswith(("https://", "http://")):
                content = '<link href="' + escape(href, quote=True) + '" color="#17634b">' + content + '</link>'
        elif child.tag == "br":
            content = "<br/>"
        result += content + escape(child.tail or "")
    return result


wrapped_code_lines = 0


def code_lines(text):
    global wrapped_code_lines
    available = CONTENT - 19
    for raw in text.expandtabs(4).splitlines():
        if not raw:
            yield ""
            continue
        remaining = raw
        while pdfmetrics.stringWidth(remaining, "Code", 7.8) > available:
            limit = len(remaining)
            while pdfmetrics.stringWidth(remaining[:limit], "Code", 7.8) > available:
                limit -= 1
            split = remaining.rfind(" ", 0, limit + 1)
            if split < limit // 2:
                split = limit
            yield remaining[:split]
            remaining = "    " + remaining[split:].lstrip()
            wrapped_code_lines += 1
        yield remaining


source = (ROOT / "BOOK.md").read_text(encoding="utf-8")
html = markdown.markdown(source, extensions=["tables", "fenced_code", "sane_lists"])
tree = ET.fromstring("<document>" + html + "</document>")
story = []
cover_title = ParagraphStyle("CoverTitle", fontName="HeadingBold", fontSize=33, leading=39, spaceAfter=20)
cover_sub = ParagraphStyle("CoverSubtitle", fontName="Heading", fontSize=17, leading=25, spaceAfter=22)
story += [Spacer(1, 52), Paragraph("Rebuilding Your<br/>Trading Application", cover_title),
          Paragraph("Python, APIs, secure identity, testing,<br/>Google Cloud and Kubernetes", cover_sub),
          Paragraph("A practical textbook and chapter-by-chapter workbook for Amol", styles["body"]),
          Spacer(1, 22), LearningDiagram(), Spacer(1, 22),
          Paragraph("Expanded edition 2 | 23 September 2026", styles["small"]),
          Paragraph("60 chapters. Worked examples throughout. A paper-only reference lab and container manifests.", styles["body"]),
          Paragraph("Learn with synthetic data and disposable infrastructure. No example authorizes live trading. Cloud resources can incur charges.", styles["small"]),
          PageBreak(), Paragraph("Contents", styles["h1"])]
toc = TableOfContents()
toc.levelStyles = [
    ParagraphStyle("TOCPart", fontName="HeadingBold", fontSize=10, leading=14, leftIndent=0,
                   firstLineIndent=0, spaceBefore=9, spaceAfter=3),
    ParagraphStyle("TOCChapter", fontName="Heading", fontSize=9, leading=13, leftIndent=12,
                   firstLineIndent=0, spaceBefore=1, spaceAfter=2),
]
story += [toc, PageBreak()]
intro = Paragraph("How to use this book", styles["h1"])
intro.book_key, intro.book_level = "introduction", 0
story.append(intro)
first_h1 = True
chapter_counter = 0

for element in tree:
    tag = element.tag
    plain = "".join(element.itertext())
    if tag == "h1":
        if first_h1:
            first_h1 = False
            continue
        story.append(PageBreak())
        paragraph = Paragraph(inline(element), styles["h1"])
        paragraph.book_key = "part-" + str(len(story))
        paragraph.book_level = 0
        story.append(paragraph)
    elif tag in {"h2", "h3", "h4"}:
        if plain.startswith("A practical book on"):
            continue
        if plain.startswith("Chapter "):
            if not isinstance(story[-1], Paragraph) or story[-1].style.name != "Part":
                story.append(PageBreak())
            paragraph = Paragraph(inline(element), styles["h2"])
            chapter_counter += 1
            paragraph.book_key = f"chapter-{chapter_counter}"
            paragraph.book_level = 1
        elif plain.startswith("Appendix "):
            story.append(PageBreak())
            paragraph = Paragraph(inline(element), styles["h2"])
            paragraph.book_key = "appendix-" + str(len(story))
            paragraph.book_level = 0
        else:
            paragraph = Paragraph(inline(element), styles["h3"])
        story.append(paragraph)
    elif tag == "p":
        story.append(Paragraph(inline(element), styles["body"]))
    elif tag in {"ul", "ol"}:
        for index, item in enumerate(element.findall("li"), 1):
            text = inline(item)
            prefix = f"{index}. " if tag == "ol" else "- "
            style = ParagraphStyle("ListItem", parent=styles["body"], leftIndent=12, firstLineIndent=-10, spaceAfter=5)
            story.append(Paragraph(prefix + text, style))
        story.append(Spacer(1, 4))
    elif tag == "pre":
        code = element.find("code")
        language = (code.get("class", "code").replace("language-", "") if code is not None else "code")
        block = [Paragraph(language.upper(), styles["code_label"])]
        block.extend(CodeLine(line) for line in code_lines(plain))
        if len(block) <= 16:
            story.append(KeepTogether(block))
        else:
            story.extend(block)
        story.append(Spacer(1, 8))
    elif tag == "table":
        rows = element.findall("./thead/tr") + element.findall("./tbody/tr")
        data = [[Paragraph(inline(cell), styles["cell"]) for cell in row] for row in rows]
        columns = len(data[0])
        if columns == 3:
            widths = [CONTENT * 0.24, CONTENT * 0.38, CONTENT * 0.38]
        elif columns == 2:
            widths = [CONTENT * 0.30, CONTENT * 0.70]
        else:
            widths = [CONTENT / columns] * columns
        table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e4eee8")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f8f7")]),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c7d2cc")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ]))
        story += [Spacer(1, 7), table, Spacer(1, 10)]
    elif tag == "blockquote":
        for child in element:
            style = ParagraphStyle("Quote", parent=styles["body"], leftIndent=14, textColor=colors.HexColor("#5b3850"))
            story.append(Paragraph(inline(child), style))
    elif tag == "hr":
        story.append(Spacer(1, 12))
    else:
        raise ValueError(f"Unhandled Markdown block {tag}")

if chapter_counter != 60:
    raise ValueError(f"Expected 60 chapters, found {chapter_counter}")
document = BookDocument(TARGET)
document.multiBuild(story)
print(json.dumps({"pdf": str(TARGET), "chapters": chapter_counter,
                  "wrapped_code_lines": wrapped_code_lines, "bytes": TARGET.stat().st_size}, indent=2))
