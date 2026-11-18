"""Build spps-v2-export-<label>.zip: source, migrations/, supabase/sql/, docs and *.example env files only.

    python tools/v2_export.py phase1 /path/to/output-dir

Excluded: node_modules, build output (Android build/.gradle, dist), caches, local databases, real .env files.
The build refuses to finish if a file that looks like a secret or a real environment file would be included.
"""
from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_DIRS = {"node_modules", "__pycache__", ".pytest_cache", ".gradle", "build", "dist", ".idea", "data",
                 ".venv", ".venv-tests", "venv", "output", ".mypy_cache", "test-results", "playwright-report"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".db", ".sqlite", ".sqlite3", ".log", ".apk", ".aab", ".keystore", ".jks"}
SECRET_PATTERNS = [re.compile(p) for p in (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", r"sk_live_[A-Za-z0-9]{10,}",
                                            r"rzp_live_[A-Za-z0-9]{8,}", r"sb_secret_[A-Za-z0-9]{10,}")]
REQUIRED = ["MIGRATIONS.md", "DEPLOYMENT.md", "PROGRESS.md", "DECISIONS.md", "migrations", "supabase/sql", "server",
            "web"]


def included(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    if any(part in EXCLUDED_DIRS for part in rel.parts[:-1]):
        return False
    if path.suffix in EXCLUDED_SUFFIXES:
        return False
    name = path.name
    if name.startswith(".env") and not name.endswith(".example"):
        return False
    return path.is_file()


def main(label: str, out_dir: str) -> Path:
    for req in REQUIRED:
        if not (ROOT / req).exists():
            raise SystemExit(f"missing required export content: {req}")
    target = Path(out_dir) / (f"{ROOT.name}.zip" if label == "release" else f"spps-v2-export-{label}.zip")
    target.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in ROOT.rglob("*") if included(p))
    for p in files:
        if p.stat().st_size < 2_000_000 and p.suffix in {".py", ".js", ".mjs", ".json", ".md", ".sql", ".yml",
                                                        ".yaml", ".txt", ".example", ".html", ".ps1", ".bat", ".sh"}:
            text = p.read_text(encoding="utf-8", errors="ignore")
            for pat in SECRET_PATTERNS:
                if pat.search(text):
                    raise SystemExit(f"refusing export: {p.relative_to(ROOT)} matches a secret pattern")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, Path(ROOT.name) / p.relative_to(ROOT))
    print(f"{target} ({len(files)} files, {target.stat().st_size // 1024} KB)")
    return target


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else str(ROOT.parent / "exports"))
