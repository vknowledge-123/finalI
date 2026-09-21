"""Build the offline book from editable chapters. Requires Python-Markdown."""
import ast
import json
from pathlib import Path
import re

import markdown


ROOT = Path(__file__).resolve().parent
chapters = sorted((ROOT / "chapters").glob("*.md"))
source = "\n\n".join(path.read_text(encoding="utf-8").rstrip() for path in chapters) + "\n"
chapter_names = re.findall(r"^## Chapter \d+ .+$", source, re.MULTILINE)
if len(chapter_names) != 46:
    raise ValueError(f"Expected 46 chapters, found {len(chapter_names)}")
if source.count("```") % 2:
    raise ValueError("Unbalanced Markdown code fences")
python_blocks = re.findall(r"```python\n(.*?)\n```", source, re.DOTALL)
for index, block in enumerate(python_blocks, 1):
    ast.parse(block, filename=f"book-python-example-{index}")
converter = markdown.Markdown(extensions=["tables", "fenced_code", "toc", "sane_lists"],
                              extension_configs={"toc": {"toc_depth": "1-2"}})
body = converter.convert(source)
ids = re.findall(r'\bid="([^"]+)"', body)
if len(ids) != len(set(ids)):
    raise ValueError("Duplicate document anchors")
targets = re.findall(r'href="#([^"]+)"', converter.toc)
if set(targets) - set(ids):
    raise ValueError("Broken table of contents anchors")
prose = re.sub(r"```.*?```", "", source, flags=re.DOTALL)
counts = {
    "chapters": len(chapter_names),
    "source_files": len(chapters),
    "prose_words_approx": len(re.findall(r"\b[\w'-]+\b", prose)),
    "python_examples_syntax_checked": len(python_blocks),
    "code_blocks": len(re.findall(r"^```\w", source, re.MULTILINE)),
}
(ROOT / "BOOK.md").write_text(source, encoding="utf-8")
template = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Rebuilding Your Trading Application</title>
<style>
:root { --ink:#222d31; --muted:#53646b; --line:#d7dfe0; --accent:#126c59; --paper:#fff; }
* { box-sizing:border-box; }
html { scroll-behavior:smooth; scroll-padding-top:30px; }
body { margin:0; color:var(--ink); background:#f2f5f5; font:17px/1.75 Georgia,'Times New Roman',serif; }
a { color:#096552; text-decoration-thickness:1px; text-underline-offset:3px; overflow-wrap:anywhere; }
a:hover { color:#912952; }
.skip { position:absolute; left:16px; top:-100px; padding:8px; background:white; }
.skip:focus { top:10px; z-index:5; }
.layout { max-width:1440px; margin:0 auto; display:grid; grid-template-columns:300px minmax(0,1fr); }
aside { position:sticky; top:0; height:100vh; padding:28px 22px; overflow:auto; font:14px/1.5 system-ui,sans-serif; border-right:1px solid var(--line); }
.brand { font-size:18px; font-weight:700; margin-bottom:4px; }
.edition { color:var(--muted); margin:0 0 20px; }
label { display:block; margin-bottom:6px; font-weight:600; }
input { width:100%; padding:10px; border:1px solid #8e9da2; border-radius:4px; font:inherit; background:white; }
.toc ul { list-style:none; padding:0; margin:14px 0; }
.toc ul ul { margin:8px 0 20px; padding-left:12px; border-left:2px solid var(--line); }
.toc li { margin:8px 0; }
.toc a { color:var(--ink); text-decoration:none; }
.toc a:hover { color:var(--accent); text-decoration:underline; }
.toc > ul > li > a { font-weight:700; }
main { min-width:0; background:var(--paper); padding:44px 56px 80px; }
.meta { font:13px/1.5 system-ui,sans-serif; color:var(--muted); margin:0 0 20px; }
.meta a { margin-right:16px; }
h1,h2,h3,h4 { font-family:system-ui,sans-serif; line-height:1.3; color:#162b25; letter-spacing:0; }
h1 { font-size:32px; margin:60px 0 20px; padding-top:28px; border-top:2px solid #92b3a9; }
main > h1:first-of-type { margin-top:12px; padding:0; border:0; }
h2 { font-size:25px; margin:44px 0 16px; }
h3 { font-size:20px; margin:28px 0 12px; }
p,li { overflow-wrap:break-word; }
p { margin:0 0 17px; } li { margin:7px 0; }
code { font:14px/1.6 Consolas,'Courier New',monospace; background:#edf2f2; padding:2px 4px; border-radius:2px; overflow-wrap:anywhere; }
pre { overflow:auto; padding:18px 20px; background:#f1f5f5; border-left:3px solid #438d78; margin:20px 0; }
pre code { padding:0; background:none; white-space:pre; overflow-wrap:normal; }
.table-wrap { overflow:auto; margin:22px 0; }
table { width:100%; border-collapse:collapse; font:14px/1.55 system-ui,sans-serif; }
th,td { padding:11px 13px; border:1px solid #cbd5d7; text-align:left; vertical-align:top; }
th { background:#e8eeeb; font-weight:650; }
tr:nth-child(even) td { background:#f7f9f9; }
blockquote { margin:20px 0; padding:12px 18px; border-left:3px solid #9a3d61; background:#fcf6f8; }
.map { margin:28px 0 34px; }
.map img { width:100%; height:auto; display:block; }
figcaption { font:13px/1.5 system-ui,sans-serif; color:var(--muted); margin-top:10px; }
.mobile-nav { display:none; }
.endnote { font:14px/1.6 system-ui,sans-serif; border-top:1px solid var(--line); padding-top:22px; margin-top:44px; }
@media(max-width:1000px) { .layout { grid-template-columns:250px minmax(0,1fr); } main { padding:30px; } }
@media(max-width:720px) {
  .layout { display:block; } aside { position:static; height:auto; border:0; padding:18px 20px; }
  aside nav { max-height:270px; overflow:auto; } main { padding:24px 20px 48px; }
  h1 { font-size:28px; } h2 { font-size:23px; } body { font-size:17px; }
  pre { padding:14px; } th,td { min-width:120px; }
}
@media print {
  @page { size:A4; margin:18mm; }
  body { background:white; font-size:11pt; line-height:1.5; }
  .layout { display:block; } aside,.meta { display:none; } main { padding:0; }
  h1 { break-before:page; font-size:24pt; } main > h1:first-of-type { break-before:auto; }
  h2,h3,h4 { break-after:avoid; } p { orphans:3; widows:3; }
  pre { white-space:pre-wrap; overflow-wrap:anywhere; overflow:visible; font-size:9pt; }
  pre code { white-space:pre-wrap; font-size:9pt; }
  table { font-size:9pt; } th,td { padding:6px; } tr { break-inside:avoid; }
  .table-wrap { overflow:visible; } a { color:inherit; }
}
</style></head><body>
<a class="skip" href="#reading">Skip to book</a>
<div class="layout"><aside>
<div class="brand">Trading Application Engineering</div>
<p class="edition">A practical book for Amol<br>Edition 1 / September 2026</p>
<label for="chapter-search">Find a chapter</label><input id="chapter-search" type="search" placeholder="Python, TOTP, testing, cloud...">
<nav aria-label="Book contents">__TOC__</nav>
</aside><main id="reading">
<p class="meta">__COUNT__ chapters / __WORDS__ words approximately<br><a href="BOOK.md">Markdown edition</a><a href="lab/README.md">Paper lab</a><a href="VERIFICATION.md">Verification notes</a></p>
__BODY__
<div class="endnote">End of book. Return to the chapter you are implementing and complete its tests before advancing.</div>
</main></div>
<script>
for (const table of document.querySelectorAll('main table')) {
  const wrapper = document.createElement('div'); wrapper.className = 'table-wrap';
  table.replaceWith(wrapper); wrapper.append(table);
}
document.querySelector('#chapter-search').addEventListener('input', event => {
  const query = event.target.value.trim().toLowerCase();
  for (const item of document.querySelectorAll('.toc > ul > li')) {
    item.hidden = !!query && !item.textContent.toLowerCase().includes(query);
    for (const child of item.querySelectorAll('li')) {
      child.hidden = !!query && !child.textContent.toLowerCase().includes(query);
    }
  }
});
</script></body></html>"""
diagram = '<figure class="map"><img src="assets/system-map.svg" alt="Learning sequence from Python through APIs, durable state, execution, testing and cloud, with analytics and AI separate from orders"><figcaption>Your rebuild sequence. Keep analytics and AI outside the order execution path.</figcaption></figure>'
# Insert the illustration after the opening subtitle, before the first prose paragraph.
closing = body.find("</h2>")
body = body[:closing + 5] + diagram + body[closing + 5:]
output = template.replace("__TOC__", converter.toc).replace("__BODY__", body)
output = output.replace("__COUNT__", str(counts["chapters"]))
output = output.replace("__WORDS__", f'{counts["prose_words_approx"]:,}')
(ROOT / "BOOK.html").write_text(output, encoding="utf-8")
(ROOT / "book-stats.json").write_text(json.dumps(counts, indent=2) + "\n", encoding="utf-8")
print(json.dumps(counts, indent=2))
