# Rebuilding Your Trading Application

Open [BOOK.html](BOOK.html) for the offline reading edition with chapter navigation, chapter search, responsive layout, and print styling. [BOOK.md](BOOK.md) is the combined editable Markdown edition. Original chapters are in `chapters/`.

The book contains 46 chapters across Python, APIs, authentication, relational design, Redis, trading state, concurrency, frontend development, testing, Google Cloud, data engineering, ML, vector search, certification preparation, and senior-engineer practice.

Start with Part One. Build your own repository and use the [paper lab](lab/README.md) only as a reference. The reference lab intentionally cannot connect to a real broker. It is not suitable for public deployment and does not implement the later security assignments.

The Google Cloud primary path is Associate Cloud Engineer, followed by a chosen professional specialization. The book includes a gap checklist and official sources; it does not guarantee an exam pass or claim complete training for every credential listed in the supplied attachment.

To regenerate the book after editing chapters, install `Markdown` in a documentation-only virtual environment and run `python build_book.py`. Generated files are `BOOK.md`, `BOOK.html`, and `book-stats.json`. The script validates chapter count, code-fence balance, Python example syntax, and table-of-contents anchors.

See [VERIFICATION.md](VERIFICATION.md) for the actual validation results and limits. No production application files were modified to create this book.
