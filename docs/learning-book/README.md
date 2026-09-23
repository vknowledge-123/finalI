# Rebuilding Your Trading Application

Open [BOOK.html](BOOK.html) for the offline reading edition with chapter navigation, chapter search, responsive layout, and print styling. [BOOK.md](BOOK.md) is the combined editable Markdown edition. Original chapters are in `chapters/`.

The expanded edition contains 60 chapters, approximately 28,400 prose words, 107 code blocks, and worked examples added to all 46 original chapters. It covers Python, APIs, authentication, relational design, Redis, trading state, concurrency, frontend development, testing, Google Cloud, data engineering, ML, vector search, certification preparation, and senior-engineer practice.

The 120-page PDF is generated at `output/pdf/Rebuilding_Your_Trading_Application_Expanded.pdf` relative to the repository root. Chapters 47-60 deepen networking, Docker, Kubernetes, GKE, workload identity, storage, autoscaling, Terraform, and operational recovery. Appendix C contains the paper lab's domain, API, and browser-test source with walkthroughs. The companion ZIP contains these editable chapters and the example files, including Kubernetes manifests.

Start with Part One. Build your own repository and use the [paper lab](lab/README.md) only as a reference. The reference lab intentionally cannot connect to a real broker. It is not suitable for public deployment and does not implement the later security assignments.

The Google Cloud primary path is Associate Cloud Engineer, followed by a chosen professional specialization. The book includes a gap checklist and official sources; it does not guarantee an exam pass or claim complete training for every credential listed in the supplied attachment.

To regenerate the book after editing chapters, install `Markdown` in a documentation-only virtual environment and run `python build_book.py`. Generated files are `BOOK.md`, `BOOK.html`, and `book-stats.json`. The script validates chapter count, code-fence balance, Python example syntax, and table-of-contents anchors.

For PDF generation, also install `reportlab`, `pypdf`, `PyMuPDF`, and `Pillow`, then run `python build_pdf.py` and `python verify_pdf.py`. The PDF builder currently uses Georgia, Arial, and Consolas from `C:/Windows/Fonts`; change `FONTDIR` and the font mapping on another operating system. `verify_pdf.py` renders all pages for visual review. Run `python package_book.py` to produce the companion ZIP without caches or databases.

See [VERIFICATION.md](VERIFICATION.md) for the actual validation results and limits. No production application files were modified to create this book.
