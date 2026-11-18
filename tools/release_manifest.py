"""Release evidence manifest: source revision, migrations, test reports, artifact hashes.

    python -m tools.release_manifest --runs <UTC> [<UTC> ...] [--artifact PATH ...] [--physical FILE] --out FILE

Reads only committed sources, runner reports (output/test-runs/<UTC>/report.json) and the given artifact files.
It contains no secrets: report summaries, file names and SHA-256 hashes only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def run_summary(utc: str) -> dict:
    report = ROOT / "output" / "test-runs" / utc / "report.json"
    data = json.loads(report.read_text(encoding="utf-8"))
    tiers = {}
    for name, res in (data.get("tiers") or data.get("results") or {}).items():
        if isinstance(res, dict):
            tiers[name] = {k: res.get(k) for k in ("passed", "failed", "errors", "skipped")}
    return {"run": utc, "commit": data.get("commit"), "exit_code": data.get("exit_code"), "verdict": data.get("verdict"),
            "protected_fingerprint_unchanged": data.get("protected_fingerprint_unchanged"), "notes": data.get("notes"),
            "tiers": tiers, "report_sha256": sha256(report)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", nargs="*", default=[])
    ap.add_argument("--artifact", nargs="*", default=[])
    ap.add_argument("--physical", help="JSON file with physical acceptance records")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    from tools.development_db import CLINICAL_MIGRATIONS
    from tools.identity_review_db import IDENTITY_MIGRATIONS
    manifest = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": {"commit": git("rev-parse", "HEAD"), "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
                   "tracked_changes": git("status", "--porcelain", "--untracked-files=no") or "none"},
        "migrations": {
            "clinical": [{"file": m, "sha256": sha256(ROOT / "migrations" / m)} for m in CLINICAL_MIGRATIONS],
            "identity": [{"file": m, "sha256": sha256(ROOT / "migrations" / m)} for m in IDENTITY_MIGRATIONS],
        },
        "test_runs": [run_summary(u) for u in args.runs],
        "artifacts": [{"file": Path(a).name, "sha256": sha256(Path(a)), "bytes": Path(a).stat().st_size}
                      for a in args.artifact],
        "physical_acceptance": json.loads(Path(args.physical).read_text(encoding="utf-8")) if args.physical else None,
    }
    Path(args.out).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"manifest written: {args.out} ({len(manifest['test_runs'])} runs, {len(manifest['artifacts'])} artifacts)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
