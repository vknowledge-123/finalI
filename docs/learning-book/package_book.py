"""Package only teaching sources, never local credentials, caches, or databases."""
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


root = Path(__file__).resolve().parent
target = root.parents[1] / "output/pdf/Trading_Application_Book_Code.zip"
allowed = {".md", ".html", ".py", ".yaml", ".yml", ".txt", ".svg", ".json", ".css"}
excluded = {".git", ".venv", "__pycache__", ".pytest_cache", "test-results", "node_modules"}
special = {"Dockerfile", ".dockerignore", ".gitignore"}
target.parent.mkdir(parents=True, exist_ok=True)
with ZipFile(target, "w", compression=ZIP_DEFLATED) as archive:
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if not path.is_file() or excluded.intersection(relative.parts):
            continue
        if path.suffix in allowed or path.name in special:
            archive.write(path, Path("learning-book") / relative)
with ZipFile(target) as archive:
    assert archive.testzip() is None
    print(f"Packaged {len(archive.namelist())} teaching files: {target}")
