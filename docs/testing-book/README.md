# Software Testing Through a Trading Application

An original beginner-to-advanced textbook, not a reproduction of a paid course.
The supplied course outline is used as a topic checklist. All explanations,
examples, cases and exercises are written for this project.

Start with `BOOK.html` or the PDF in `output/pdf/`. Editable source is in
`parts/`. Read `lab/README.md` before running examples. The companion lab has
no broker SDK, account login or real-order endpoint. Do not point exercises
at a client VM, production Redis, real webhook or brokerage account.

This edition has 40 chapters, 6 appendices, 18,591 prose words, 76 code blocks
and a 76-page PDF. It includes a 48-case manual test pack and a staged SDET
capstone. The lab's 40 tests passed serially and with two parallel workers.
See `VERIFICATION.md` for browser/environment details and unexecuted recipes.

Each chapter explains the concept, develops a worked example, identifies
common mistakes, and ends with practice and an answer checkpoint. Code blocks
marked **Lab** correspond to runnable companion files; **Recipe** blocks are
adaptable examples and may need the described fixture or HTML. They are not
claimed to be existing production routes.

The production project is used as a case study, not as proof of universal
safety. Passing examples does not certify broker availability, regulatory
compliance, profitability, or the absence of defects.

Build: install `Markdown`, `reportlab`, `pypdf`, `PyMuPDF` in a documentation
environment, then run `python docs/testing-book/build.py` from the repo root.
The builder produces Markdown, offline HTML, PDF and a companion ZIP. The
verification report records exactly which executable tests were run.

`verify.py` renders all PDF pages and checks structural bounds. It additionally
requires Pillow. `verify_reader.py` checks offline HTML navigation and mobile
layout using Playwright and installed Chrome. The PDF font mapping currently
uses Georgia, Arial and Consolas from `C:/Windows/Fonts`; adapt `FONTDIR` and
the font filenames when rebuilding on another OS.
