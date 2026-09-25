"""Build the standalone testing textbook and source bundle."""
import ast
from html import escape
import json
from pathlib import Path
import re
import textwrap
import xml.etree.ElementTree as ET
import zipfile

import markdown
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import BaseDocTemplate, Flowable, Frame, PageTemplate
from reportlab.platypus import Paragraph, Spacer, PageBreak, CondPageBreak, KeepTogether, Table, TableStyle
from reportlab.platypus.tableofcontents import TableOfContents
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
OUT = REPO / "output" / "pdf"
OUT.mkdir(parents=True, exist_ok=True)
TARGET = OUT / "Software_Testing_Beginner_to_SDET.pdf"
WIDTH, HEIGHT = A4
MARGIN = 48
CONTENT = WIDTH - 2 * MARGIN
FONTDIR = Path("C:/Windows/Fonts")
for name, file in [("Body", "georgia.ttf"), ("BodyBold", "georgiab.ttf"),
                   ("BodyItalic", "georgiai.ttf"), ("Head", "arial.ttf"),
                   ("HeadBold", "arialbd.ttf"), ("Mono", "consola.ttf")]:
    pdfmetrics.registerFont(TTFont(name, str(FONTDIR / file)))
pdfmetrics.registerFontFamily("Body", normal="Body", bold="BodyBold", italic="BodyItalic", boldItalic="BodyBold")
pdfmetrics.registerFontFamily("Head", normal="Head", bold="HeadBold", italic="Head", boldItalic="HeadBold")

STYLES = {
    "p": ParagraphStyle("BodyText", fontName="Body", fontSize=10.1, leading=14.8,
                        spaceAfter=8, allowWidows=False, allowOrphans=False, textColor=colors.HexColor("#26342f")),
    "h1": ParagraphStyle("Part", fontName="HeadBold", fontSize=24, leading=29, spaceAfter=18, keepWithNext=True),
    "h2": ParagraphStyle("Chapter", fontName="HeadBold", fontSize=18, leading=23, spaceAfter=14, keepWithNext=True),
    "h3": ParagraphStyle("Section", fontName="HeadBold", fontSize=12, leading=16, spaceBefore=12, spaceAfter=7, keepWithNext=True),
    "small": ParagraphStyle("Small", fontName="Head", fontSize=8.5, leading=12, spaceAfter=7),
    "cell": ParagraphStyle("Cell", fontName="Head", fontSize=8.2, leading=11.5),
    "code": ParagraphStyle("CodeTitle", fontName="HeadBold", fontSize=7.5, leading=10, spaceBefore=6, spaceAfter=3, keepWithNext=True),
}


class CodeLine(Flowable):
    def __init__(self, text):
        super().__init__()
        self.text, self.width, self.height = text, CONTENT, 10.4

    def draw(self):
        self.canv.setFillColor(colors.HexColor("#eef4f1"))
        self.canv.rect(0, 0, self.width, self.height, fill=1, stroke=0)
        self.canv.setFillColor(colors.HexColor("#243d31"))
        self.canv.setFont("Mono", 7.5)
        self.canv.drawString(8, 2.4, self.text)


class LearningPath(Flowable):
    def __init__(self):
        super().__init__()
        self.width, self.height = CONTENT, 180

    def draw(self):
        stages = [("01-05", "Think and explore", "Requirements, risk, manual testing"),
                  ("06-13", "Program with Python", "Values, models, files, concurrency"),
                  ("14-18", "Prove backend behavior", "Pytest, fixtures, fakes, state"),
                  ("19-28", "Automate the browser", "Playwright, controls, traces, mobile"),
                  ("29-34", "Connect the layers", "Frameworks, APIs, identity, security"),
                  ("35-40", "Deliver as an SDET", "CI/CD, resilience, capstone, review")]
        for index, (number, title, detail) in enumerate(stages):
            y = 155 - index * 30
            self.canv.setFillColor(colors.HexColor("#16684d" if index % 2 == 0 else "#85405b"))
            self.canv.rect(0, y, 47, 24, fill=1, stroke=0)
            self.canv.setFillColor(colors.white)
            self.canv.setFont("HeadBold", 9)
            self.canv.drawString(6, y + 8, number)
            self.canv.setFillColor(colors.HexColor("#24372f"))
            self.canv.setFont("HeadBold", 10)
            self.canv.drawString(59, y + 13, title)
            self.canv.setFont("Head", 8)
            self.canv.drawString(59, y + 2, detail)


class Book(BaseDocTemplate):
    def __init__(self):
        super().__init__(str(TARGET), pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN,
                         topMargin=48, bottomMargin=48, title="Software Testing: Beginner to SDET",
                         author="Practical engineering workbook prepared for Amol")
        frame = Frame(MARGIN, 48, CONTENT, HEIGHT - 96, leftPadding=0,
                      rightPadding=0, topPadding=0, bottomPadding=0)
        self.addPageTemplates(PageTemplate(id="book", frames=frame, onPage=self.decorate))

    def decorate(self, canvas, doc):
        if doc.page == 1:
            return
        canvas.saveState()
        canvas.setFillColor(colors.HexColor("#586b61"))
        canvas.setFont("Head", 8)
        canvas.drawString(MARGIN, HEIGHT - 27, "Software Testing | Beginner to SDET")
        canvas.drawString(MARGIN, 27, "Paper-only learning edition")
        canvas.drawRightString(WIDTH - MARGIN, 27, str(doc.page))
        canvas.restoreState()

    def afterFlowable(self, flowable):
        if hasattr(flowable, "book_key"):
            self.canv.bookmarkPage(flowable.book_key)
            title = flowable.getPlainText()
            self.canv.addOutlineEntry(title, flowable.book_key, flowable.book_level, closed=False)
            self.notify("TOCEntry", (flowable.book_level, title, self.page, flowable.book_key))


def inline(element):
    result = escape(element.text or "")
    for child in element:
        content = inline(child)
        if child.tag in {"strong", "b"}:
            content = "<b>" + content + "</b>"
        elif child.tag in {"em", "i"}:
            content = "<i>" + content + "</i>"
        elif child.tag == "code":
            content = '<font name="Mono" size="9">' + content + '</font>'
        elif child.tag == "a":
            href = child.get("href", "")
            if href.startswith(("https://", "http://")):
                content = '<link href="' + escape(href, quote=True) + '" color="#14684d">' + content + '</link>'
        elif child.tag == "br":
            content = "<br/>"
        result += content + escape(child.tail or "")
    return result


INTRO = """# Software Testing: Beginner to SDET

An original practical textbook using an alert-driven trading application.

This edition covers manual testing, Python, pytest, Playwright, HTTP APIs,
authentication, test frameworks, CI/CD and reliability. It is written for a
beginner who wants to understand and build the tests, not merely run generated
scripts. The supplied course outline is a coverage checklist, not source text.

Read chapters in order. Before executing an example, predict its result.
Afterward, change one input and explain the new behavior. Keep a learning
journal with the requirement, oracle, test, failure and conclusion. Each chapter
ends with practice and a checkpoint; Appendix B gives detailed debugging drills.

Safety boundary: all executable exercises use a local paper-only lab with
synthetic symbols. Never use a client VM, real webhook URL, live broker account,
production Redis or real credentials. No test in this book authorizes a trade.
The lab intentionally omits production authentication, persistence and broker
execution. Never expose it publicly.

Examples marked Recipe illustrate a technique and require the stated fixture,
HTML or interface. The companion files are the executable reference. PDF lines
may wrap for printing; use the editable source for exact indentation and long
commands. Windows and Linux commands are labelled where they differ.

Forty chapters and a capstone provide a substantial learning path, not a
guarantee of employment, certification, universal security or bug-free software.
Record the actual scope of every test run. See VERIFICATION.md for this
edition's executable checks and their limitations.
"""


def main():
    parts = sorted((ROOT / "parts").glob("*.md"))
    source = INTRO + "\n\n" + "\n\n".join(p.read_text(encoding="utf-8") for p in parts)
    chapters = re.findall(r"^## Chapter (\d+)\.", source, re.M)
    assert chapters == [str(i) for i in range(1, 41)], chapters
    blocks = re.findall(r"```([^\n]*)\n(.*?)\n```", source, re.S)
    assert source.count("```") == 2 * len(blocks), "Unbalanced code fences"
    python_count = 0
    for language, code in blocks:
        if language == "python":
            ast.parse(code)
            python_count += 1
    source += "\n\n## Appendix F. Executable Lab Source and Walkthrough\n\n"
    source += ("The following files form the companion lab. Begin with domain.py, then follow "
               "create_app in server.py. Each call builds independent state. API tests use "
               "TestClient; browser tests use a short-lived localhost server and fresh contexts. "
               "The DOM-control tests create their own HTML instead of relying on public demo sites. "
               "The memory dictionaries are deliberately not a durable multi-process database.\n\n")
    for name in ["requirements.txt", "pytest.ini", "domain.py", "server.py", "index.html",
                 "tests/conftest.py", "tests/test_domain.py", "tests/test_api.py",
                 "tests/test_browser.py", "tests/test_controls.py", "tests/test_widgets.py"]:
        code = (ROOT / "lab" / name).read_text(encoding="utf-8")
        language = {".py": "python", ".html": "html", ".ini": "ini"}.get(Path(name).suffix, "text")
        if language == "python":
            ast.parse(code)
        source += f"### {name}\n\n```{language}\n{code.rstrip()}\n```\n\n"
    (ROOT / "BOOK.md").write_text(source, encoding="utf-8")
    md = markdown.Markdown(extensions=["fenced_code", "tables", "toc", "sane_lists"])
    html = md.convert(source)
    css = """body{margin:0;background:#f8faf9;color:#25362d;font:18px/1.7 Georgia,serif}
    nav{position:fixed;inset:0 auto 0 0;width:270px;overflow:auto;background:#e9f0ec;padding:22px;box-sizing:border-box;font:14px/1.5 Arial}
    nav ul{padding-left:17px}nav a{color:#18583f}main{max-width:860px;margin-left:300px;padding:35px}
    h1,h2,h3{font-family:Arial;line-height:1.25}h1{border-top:4px solid #16684d;padding-top:25px}h2{margin-top:48px}
    pre{background:#eaf1ed;padding:16px;overflow:auto;font:14px/1.55 Consolas,monospace;border-left:3px solid #16684d}
    code{font-family:Consolas,monospace}table{border-collapse:collapse;width:100%;font:15px/1.5 Arial;margin:20px 0}
    th,td{padding:10px;border:1px solid #bacbbf;vertical-align:top}th{background:#e0ebe4}a{color:#14634a}
    p,li{overflow-wrap:anywhere}table{display:block;max-width:100%;overflow-x:auto}main{min-width:0}
    @media(max-width:900px){nav{position:static;width:auto;max-height:260px}main{margin:0;padding:20px}}
    @media print{nav{display:none}main{margin:0;max-width:none}h2{break-before:page}pre{white-space:pre-wrap}}
    """
    (ROOT / "BOOK.html").write_text('<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"><title>Software Testing: Beginner to SDET</title>'
        '<style>' + css + '</style></head><body><nav><strong>Chapter navigation</strong>' + md.toc +
        '</nav><main>' + html + '</main></body></html>', encoding="utf-8")

    title = ParagraphStyle("CoverTitle", fontName="HeadBold", fontSize=37, leading=42, spaceAfter=19)
    subtitle = ParagraphStyle("CoverSub", fontName="Head", fontSize=18, leading=26, spaceAfter=18)
    story = [Spacer(1, 42), Paragraph("SOFTWARE<br/>TESTING", title),
             Paragraph("From Beginner to SDET", subtitle),
             Paragraph("Manual testing, Python, pytest,<br/>Playwright and API automation", subtitle),
             Spacer(1, 14), LearningPath(), Spacer(1, 25),
             Paragraph("40 chapters | Worked examples | Exercises | Executable lab", STYLES["p"]),
             Paragraph("Built around your trading application, with a separate paper-only practice environment.", STYLES["p"]),
             Spacer(1, 12), Paragraph("Learning edition 1 | 24 September 2026", STYLES["small"]),
             PageBreak(), Paragraph("Contents", STYLES["h1"])]
    toc = TableOfContents()
    toc.levelStyles = [ParagraphStyle("TOCPart", fontName="HeadBold", fontSize=10, leading=14, spaceBefore=7),
                       ParagraphStyle("TOCChapter", fontName="Head", fontSize=9, leading=13, leftIndent=10, spaceAfter=3)]
    story += [toc, PageBreak()]
    tree = ET.fromstring("<document>" + html + "</document>")
    first = True
    wrapped = 0
    for element in tree:
        tag, plain = element.tag, "".join(element.itertext())
        if tag in {"h1", "h2", "h3", "h4"}:
            if first:
                first = False
                paragraph = Paragraph("How to use this book", STYLES["h1"])
                paragraph.book_key, paragraph.book_level = "reader-guide", 0
            else:
                level = "h3" if tag in {"h3", "h4"} else tag
                if tag == "h1":
                    story.append(PageBreak())
                elif tag == "h2" and plain.startswith("Appendix "):
                    if not (isinstance(story[-1], Paragraph) and story[-1].style.name == "Part"):
                        story.append(PageBreak())
                elif tag == "h2":
                    story += [CondPageBreak(190), Spacer(1, 18)]
                elif tag in {"h3", "h4"}:
                    story.append(CondPageBreak(145))
                paragraph = Paragraph(inline(element), STYLES[level])
                if tag in {"h1", "h2"}:
                    paragraph.book_key = element.get("id", "section-" + str(len(story)))
                    paragraph.book_level = 0 if tag == "h1" else 1
            story.append(paragraph)
        elif tag == "p":
            story.append(Paragraph(inline(element), STYLES["p"]))
        elif tag in {"ul", "ol"}:
            for number, item in enumerate(element.findall("li"), 1):
                prefix = str(number) + ". " if tag == "ol" else "- "
                style = ParagraphStyle("List", parent=STYLES["p"], leftIndent=12, firstLineIndent=-10, spaceAfter=5)
                story.append(Paragraph(prefix + inline(item), style))
        elif tag == "pre":
            code = element.find("code")
            language = code.get("class", "code").replace("language-", "") if code is not None else "code"
            items = [Paragraph(language.upper(), STYLES["code"])]
            max_chars = int((CONTENT - 16) / pdfmetrics.stringWidth("M", "Mono", 7.5))
            for line in plain.expandtabs(4).splitlines():
                lines = textwrap.wrap(line, width=max_chars, replace_whitespace=False,
                                      drop_whitespace=False, subsequent_indent="    ") or [""]
                wrapped += len(lines) - 1
                items.extend(CodeLine(part) for part in lines)
            story.append(KeepTogether(items)) if len(items) < 18 else story.extend(items)
            story.append(Spacer(1, 8))
        elif tag == "table":
            rows = element.findall("./thead/tr") + element.findall("./tbody/tr")
            data = [[Paragraph(inline(cell), STYLES["cell"]) for cell in row] for row in rows]
            count = len(data[0])
            ratios = {2: [.33, .67], 3: [.25, .36, .39], 4: [.24, .24, .24, .28]}.get(count, [1/count]*count)
            table = Table(data, colWidths=[CONTENT*r for r in ratios], repeatRows=1, hAlign="LEFT")
            table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dfece4")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f8f6")]),
                ("GRID", (0, 0), (-1, -1), .35, colors.HexColor("#c3d1c7")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]))
            story += [Spacer(1, 6), table, Spacer(1, 9)]
        elif tag == "blockquote":
            for item in element:
                story.append(Paragraph(inline(item), STYLES["p"]))
        elif tag == "hr":
            story.append(Spacer(1, 10))
        else:
            raise ValueError(f"Unhandled Markdown tag: {tag}")
    Book().multiBuild(story)
    prose = re.sub(r"```.*?```", "", source, flags=re.S)
    stats = {"chapters": len(chapters), "prose_words": len(prose.split()),
             "code_blocks": source.count("```") // 2, "python_snippets_syntax_checked": python_count,
             "pages": len(PdfReader(TARGET).pages), "wrapped_code_lines": wrapped, "pdf": str(TARGET)}
    (ROOT / "book-stats.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
    bundle = REPO / "output" / "Software_Testing_SDET_Companion.zip"
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in ROOT.rglob("*"):
            excluded = {"__pycache__", ".pytest_cache", "test-results", ".venv", "artifacts"}
            if path.is_file() and not excluded.intersection(path.parts) and path.suffix != ".pyc":
                archive.write(path, "testing-book/" + path.relative_to(ROOT).as_posix())
        archive.write(TARGET, TARGET.name)
    print(json.dumps(stats, indent=2))
    print("Companion:", bundle)


if __name__ == "__main__":
    main()
