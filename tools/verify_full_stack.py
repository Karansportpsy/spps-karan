"""Read-only SPPS source completeness gate. Never opens credentials or connects externally.

This checks preservation of the cumulative repository stack; functional evidence
and remaining hosted-enablement work are tracked separately in release documentation.
Run: python -m tools.verify_full_stack
"""
from __future__ import annotations

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = [
    "server/__init__.py",
    "server/main.py", "server/models.py", "server/db.py", "server/security.py",
    "server/identity.py", "server/development_profile.py", "server/beta_routes.py",
    "server/subscription_routes.py", "server/marketplace_routes.py",
    "web/index.html", "web/app.js", "web/styles.css", "web/room.html", "web/room.js",
    "web/privacy.html", "web/terms.html",
    "capacitor.config.json", "android/app/build.gradle",
    "android/app/src/main/AndroidManifest.xml",
    "android/app/src/main/java/com/winmindperform/spps/MainActivity.java",
    "android/gradlew.bat", "android/gradle/wrapper/gradle-wrapper.jar",
    "scripts/stage-web.mjs", "scripts/configure-android.mjs",
    "scripts/apply-native-overrides.mjs", "package.json", "package-lock.json",
    "requirements-hosted.txt", "requirements-development.txt",
    "Dockerfile", "render.yaml",
    "compose.development.yml",
    "migrations/0001_REVIEW_ONLY_postgresql_schema.sql",
    "migrations/0002_isolated_development_rls.sql",
    "migrations/0003_local_clinical_parity.sql",
    ".env.example", ".env.integration.example", 
    ".env.production.example", ".gitignore",
    "server/hosted_profile.py", "tools/hosted_db.py", "tests/test_hosted_profile.py",
    "tests_hosted/conftest.py", "tests_hosted/test_hosted_staging.py",
    "tests/test_clinical_parity_gate.py",
    "tests_integration/test_shared_backend.py",
    "tests_integration/test_clinical_parity.py",
    "tests_android/clinical_parity_contract.test.mjs",
    "tests_android/side_by_side_package.test.mjs",
    # M0 autonomous-testing safety infrastructure
    "CLAUDE.md", "compose.test-ephemeral.yml", "tools/ephemeral_guard.py",
    "tools/ephemeral_db.py", "tools/spps_check.py", "scripts/spps-check.ps1",
    ".claude/settings.json", ".claude/hooks/guard_commands.py",
]
COPIED_ASSETS = [
    ("web/app.js", "www/assets/app.js"),
    ("web/styles.css", "www/assets/styles.css"),
    ("web/room.js", "www/assets/room.js"),
    ("web/index.html", "www/index.html"),
    ("web/room.html", "www/room.html"),
    ("web/privacy.html", "www/privacy.html"),
    ("web/terms.html", "www/terms.html"),
    ("web/portal.html", "www/portal.html"),
    ("web/portal.js", "www/assets/portal.js"),
]
REQUIRED_ROUTES = {
    "server/main.py": (
        "/api/auth/login", "/api/athletes", "/api/cases", "/api/notes",
        "/api/factors", "/api/observations", "/api/assignments",
        "/api/reports", "/api/athletes/{athlete_id}/observations"
    ),
    "server/beta_routes.py": (
        "/api/public/practitioners", "/api/public/bookings",
        "/api/bookings/{bid}/approve", "/api/assessments/offline",
        "/api/injury/episodes", "/ws/room/{room_id}",
        "/api/payment/razorpay/webhook",
    ),
    "server/subscription_routes.py": (
        "/api/public/practitioner-registration",
        "/api/payment/razorpay/subscription-webhook",
    ),
    "server/marketplace_routes.py": (
        "/api/public/programmes", "/api/practitioner/programmes",
        "/api/public/programme-enquiries",
    ),
}


def inspect_stack(root: Path = ROOT) -> tuple[list[str], dict[str, object]]:
    issues = []
    missing = [path for path in REQUIRED if not (root / path).is_file()]
    if missing:
        issues.extend(f"missing required source: {path}" for path in missing)
    matched = 0
    for source, staged in COPIED_ASSETS:
        src, copy = root / source, root / staged
        if not src.is_file() or not copy.is_file():
            issues.append(f"missing web or generated Android asset: {source} -> {staged}")
        elif src.read_bytes() != copy.read_bytes():
            issues.append(f"web/Android asset divergence: {source} -> {staged}")
        else:
            matched += 1
    for module, routes in REQUIRED_ROUTES.items():
        file = root / module
        if not file.is_file():
            continue
        content = file.read_text(encoding="utf-8")
        for route in routes:
            if route not in content:
                issues.append(f"expected original endpoint absent: {module}:{route}")
    model = root / "server/models.py"
    table_count = 0
    if model.is_file():
        model_text = model.read_text(encoding="utf-8")
        table_count = len(re.findall(r"^\s*__tablename__\s*=", model_text, re.MULTILINE))
        if table_count != 32:
            issues.append(f"existing 32-table ORM baseline altered: {table_count} tables")
    config = root / "android/app/build.gradle"
    if config.is_file():
        source = config.read_text(encoding="utf-8")
        for value in ('applicationId "com.winmindperform.spps.beta15"', 'applicationIdSuffix ".m12validation"'):
            if value not in source:
                issues.append(f"Android identity invariant missing: {value}")
    gitignore = root / ".gitignore"
    if gitignore.is_file():
        ignore = gitignore.read_text(encoding="utf-8").splitlines()
        for value in (".env", ".env.*", "! .env.integration.example"):
            if value.startswith("! "):
                value = value.replace("! ", "!", 1)
            if value not in ignore:
                issues.append(f"ignore rule missing: {value}")
    return issues, {"required_files": len(REQUIRED), "missing_files": len(missing),
                    "web_android_asset_pairs_matching": matched,
                    "asset_pair_count": len(COPIED_ASSETS), "orm_tables": table_count}


def main() -> int:
    issues, meta = inspect_stack()
    print("SPPS consolidated source integrity (offline, no credentials or network)")
    for k, v in meta.items():
        print(f"{k}: {v}")
    for issue in issues:
        print(f"FAIL: {issue}")
    if issues:
        print("Source invariant failure. No deployment or production authorization.")
        return 1
    print("PASS: Cumulative existing SPPS source preserved and web/Android staging aligned.")
    print("NOTE: Functional evidence and remaining hosted gates are tracked in release docs.")
    print("This source-integrity result is NOT functional or production acceptance.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
