"""SPPS automated test runner: tiers, safety preflight, reports, regression and flake detection.

    python -m tools.spps_check --tier fast|db|full|security|android|ci|<tier>[,<tier>...]
                               [--rerun-failed] [--repeat N] [--keep-db] [--update-baseline]

Tiers: static, unit, android-src, identity, integration, browser, security, build.
Groups: fast=static,unit,android-src   db=identity,integration,browser
        full=fast+db+security           android=build   ci=static,unit,android-src,db

Exit codes: 0 pass · 1 test failure · 2 safety refusal · 3 protected environment changed
            4 tracked files modified by the run
Reports: output/test-runs/<UTC>/report.json|report.md and output/test-runs/latest.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from tools import ephemeral_guard as guard

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "output" / "test-runs"
BASELINE = RUNS / "baseline.json"
PY = sys.executable
WINDOWS = os.name == "nt"

GROUPS = {
    "fast": ["static", "unit", "android-src"],
    "db": ["identity", "integration", "browser", "hosted"],
    "full": ["static", "unit", "android-src", "identity", "integration", "browser", "hosted", "security"],
    "ci": ["static", "unit", "android-src", "identity", "integration", "browser"],
    "android": ["build"],
    "release": ["release"],
    "validation": ["validation"],
    # Hosted-staging RLS profile on a FRESH, empty, TLS-enabled disposable cluster (own CI job).
    "hosted": ["hosted"],
}
ALL_TIERS = ["static", "unit", "android-src", "identity", "integration", "browser", "hosted", "security", "build",
             "release", "validation"]
DB_TIERS = {"identity", "integration", "browser"}
HOSTED_TIERS = {"hosted"}
SKIP_IS_FAILURE = DB_TIERS | HOSTED_TIERS  # an integration/RLS skip is never acceptance

SECRET_PATTERNS = [
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"), "private key"),
    (re.compile(r"rzp_live_[A-Za-z0-9]{8,}"), "Razorpay live key"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS access key"),
    (re.compile(r"postgres(?:ql)?(?:\+psycopg)?://[^:\s/@]+:(?!REPLACE|<|\$\{|fictional|test@|p@|LOCAL)[^@\s]{12,}@"), "database URL with password"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}"), "JWT"),
]


class Refusal(Exception):
    pass


# ------------------------------------------------------------------ helpers

def run(cmd, *, env=None, timeout=1800, cwd=ROOT):
    start = time.monotonic()
    proc = subprocess.run(cmd, cwd=cwd, env={**os.environ, **(env or {})}, text=True,
                          capture_output=True, timeout=timeout, encoding="utf-8", errors="replace")
    return proc.returncode, (proc.stdout or "") + (proc.stderr or ""), time.monotonic() - start


def git_dirty() -> set[str]:
    code, out, _ = run(["git", "status", "--porcelain=v1", "--untracked-files=no"])
    if code:
        raise Refusal("git status failed")
    return {line[3:] for line in out.splitlines() if line.strip()}


def fingerprint() -> dict:
    """Identity of every protected container + volume (never their contents)."""
    if guard.in_ci_service() or not shutil.which("docker"):
        return {}
    facts = {}
    for name in guard.PROTECTED_CONTAINERS:
        code, out, _ = run(["docker", "inspect", "--format",
                            "{{.Id}}|{{.Created}}|{{.State.Running}}|{{range .Mounts}}{{.Name}},{{end}}", name],
                           timeout=30)
        facts[name] = out.strip() if code == 0 else "ABSENT"
    code, out, _ = run(["docker", "volume", "ls", "--format", "{{.Name}}"], timeout=30)
    facts["volumes"] = sorted(out.split()) if code == 0 else "UNKNOWN"
    return facts


def parse_junit(path: Path, tier: str) -> dict:
    result = {"passed": 0, "failed": 0, "skipped": 0, "errors": 0, "tests": {}, "failures": []}
    if not path.is_file():
        return result
    root = ET.parse(path).getroot()
    for case in root.iter("testcase"):
        name = case.get("name", "?")
        cls = case.get("classname", "")
        file = case.get("file") or ""
        test_id = f"{cls}::{name}" if cls else name
        outcome = "passed"
        detail = ""
        for child in case:
            if child.tag in ("failure", "error"):
                outcome = "failed" if child.tag == "failure" else "error"
                detail = (child.get("message") or "") + "\n" + (child.text or "")
            elif child.tag == "skipped":
                outcome = "skipped"
                detail = child.get("message") or ""
        result["tests"][test_id] = outcome
        key = {"passed": "passed", "failed": "failed", "error": "errors", "skipped": "skipped"}[outcome]
        result[key] += 1
        if outcome in ("failed", "error") or (outcome == "skipped" and tier in SKIP_IS_FAILURE):
            result["failures"].append({"id": test_id, "file": file, "outcome": outcome,
                                       "detail": detail.strip()[-2500:]})
    return result


def pytest_tier(tier, paths, out_dir, *, env=None, extra=(), rerun=None):
    junit = out_dir / f"{tier}.junit.xml"
    targets = rerun if rerun else paths
    cmd = [PY, "-m", "pytest", *targets, "-q", "-ra", "-p", "no:cacheprovider",
           f"--junitxml={junit}", "-o", "junit_family=xunit2",
           f"--basetemp={out_dir / ('tmp-' + tier)}", *extra]
    code, log, secs = run(cmd, env={"PYTHONDONTWRITEBYTECODE": "1", **(env or {})})
    res = parse_junit(junit, tier)
    if code not in (0, 5) and not res["failures"]:
        res["failures"].append({"id": f"{tier}::collection", "file": "", "outcome": "error",
                                "detail": log[-3000:]})
    if code == 5 and tier in SKIP_IS_FAILURE:
        res["failures"].append({"id": f"{tier}::no-tests", "file": "", "outcome": "error",
                                "detail": "No tests collected"})
    return res, log, secs


def check(name, ok, detail=""):
    return {"id": f"static::{name}", "ok": bool(ok), "detail": detail}


# ------------------------------------------------------------------ tiers

def tier_static(out_dir, _ctx):
    results = []
    code, log, _ = run([PY, "-m", "tools.verify_full_stack"])
    results.append(check("verify_full_stack", code == 0, log[-1500:]))
    code, log, _ = run([PY, "-m", "tools.identity_review_preflight"])
    results.append(check("identity_review_preflight", code == 0, log[-800:]))
    code, out, _ = run(["git", "ls-files"])
    tracked = out.splitlines()
    env_tracked = [f for f in tracked if re.search(r"(^|/)\.env(\.|$)", f) and not f.endswith(".example")]
    results.append(check("no_tracked_env_files", not env_tracked, ", ".join(env_tracked)))
    hits = []
    for rel in tracked:
        p = ROOT / rel
        if p.suffix.lower() in {".png", ".jpg", ".webp", ".jar", ".ico", ".apk", ".aab"} or not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for pattern, label in SECRET_PATTERNS:
            if pattern.search(text):
                hits.append(f"{rel}: {label}")
    results.append(check("secret_scan", not hits, "\n".join(hits)))
    for js in ("web/app.js", "web/room.js", *sorted(str(p.relative_to(ROOT)) for p in (ROOT / "web").glob("portal*.js"))):
        code, log, _ = run(["node", "--check", js])
        results.append(check(f"node_syntax:{js}", code == 0, log[-800:]))
    gi = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    results.append(check("output_ignored", "output/" in gi))
    res = {"passed": sum(r["ok"] for r in results), "failed": sum(not r["ok"] for r in results),
           "skipped": 0, "errors": 0, "tests": {r["id"]: "passed" if r["ok"] else "failed" for r in results},
           "failures": [{"id": r["id"], "file": "", "outcome": "failed", "detail": r["detail"]}
                        for r in results if not r["ok"]]}
    return res, "", 0.0


def tier_unit(out_dir, ctx):
    return pytest_tier("unit", ["tests"], out_dir, rerun=ctx.get("rerun", {}).get("unit"))


def tier_android_src(out_dir, _ctx):
    junit = out_dir / "android-src.junit.xml"
    code, log, secs = run(["node", "--test", "--test-reporter=junit",
                           f"--test-reporter-destination={junit}",
                           "--test-reporter=spec", "--test-reporter-destination=stdout",
                           "tests_android/*.test.mjs"])
    res = parse_junit(junit, "android-src")
    if code and not res["failures"]:
        res["failures"].append({"id": "android-src::run", "file": "", "outcome": "error", "detail": log[-3000:]})
    return res, log, secs


def _db_env(ctx):
    env_file = ctx["db"]["integration_env"]
    return {"SPPS_INTEGRATION_ENV": env_file, "SPPS_IDENTITY_TARGET": "ephemeral"}


def tier_identity(out_dir, ctx):
    return pytest_tier("identity", ["tests_identity"], out_dir, env=_db_env(ctx),
                       rerun=ctx.get("rerun", {}).get("identity"))


def tier_integration(out_dir, ctx):
    browser = [f"--ignore={p.relative_to(ROOT).as_posix()}" for p in sorted((ROOT / "tests_integration").glob("test_browser_*.py"))]
    return pytest_tier("integration", ["tests_integration"], out_dir, env=_db_env(ctx), extra=browser,
                       rerun=ctx.get("rerun", {}).get("integration"))


def tier_hosted(out_dir, ctx):
    """Replaces any fixture cluster from earlier db tiers with a fresh, empty, TLS-enabled one."""
    from tools import ephemeral_db
    if guard.in_ci_service() and ctx.get("db"):
        raise Refusal("the hosted tier needs its own fresh CI service container (separate job)")
    state = ephemeral_db.up_empty()
    return pytest_tier("hosted", ["tests_hosted"], out_dir, env={"SPPS_HOSTED_TEST_ADMIN_ENV": state["admin_env"]},
                       rerun=ctx.get("rerun", {}).get("hosted"))


def tier_browser(out_dir, ctx):
    files = [p.relative_to(ROOT).as_posix() for p in sorted((ROOT / "tests_integration").glob("test_browser_*.py"))]
    return pytest_tier("browser", files, out_dir,
                       env=_db_env(ctx), rerun=ctx.get("rerun", {}).get("browser"))


def tier_security(out_dir, _ctx):
    results = []
    if shutil.which("pip-audit") or run([PY, "-m", "pip_audit", "--version"])[0] == 0:
        code, log, _ = run([PY, "-m", "pip_audit", "-r", "requirements-hosted.txt", "--progress-spinner", "off"], timeout=600)
        results.append(check("pip_audit_hosted", code == 0, log[-3000:]))
    else:
        results.append(check("pip_audit_hosted", False, "pip-audit not installed (pip install -r requirements-development.txt)"))
    npm = "npm.cmd" if WINDOWS else "npm"
    code, log, _ = run([npm, "audit", "--audit-level=high", "--omit=dev"], timeout=600)
    results.append(check("npm_audit_high", code == 0, log[-3000:]))
    code, log, _ = run([PY, "-m", "pytest", "tests/test_security_controls.py", "-q", "-p", "no:cacheprovider"]) \
        if (ROOT / "tests/test_security_controls.py").is_file() else (0, "", 0)
    results.append(check("security_control_tests", code == 0, log[-2000:]))
    res = {"passed": sum(r["ok"] for r in results), "failed": sum(not r["ok"] for r in results),
           "skipped": 0, "errors": 0,
           "tests": {r["id"].replace("static::", "security::"): "passed" if r["ok"] else "failed" for r in results},
           "failures": [{"id": r["id"].replace("static::", "security::"), "file": "", "outcome": "failed",
                         "detail": r["detail"]} for r in results if not r["ok"]]}
    return res, "", 0.0


def tier_build(out_dir, _ctx):
    """Debug APK build + package identity check, then restore bundled config."""
    results = []
    sdk = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT") or \
        str(Path(os.environ.get("LOCALAPPDATA", "")) / "Android" / "Sdk")
    env = {"ANDROID_HOME": sdk, "ANDROID_SDK_ROOT": sdk}
    npm = "npm.cmd" if WINDOWS else "npm"
    log_all = ""
    try:
        for step in (["run", "android:configure:debug"], ["run", "android:sync"]):
            code, log, _ = run([npm, *step], env=env)
            log_all += log
            results.append(check("npm " + " ".join(step), code == 0, log[-2000:]))
            if code:
                raise RuntimeError
        gradlew = str(ROOT / "android" / ("gradlew.bat" if WINDOWS else "gradlew"))
        gradle_cmd = [gradlew] if WINDOWS else ["bash", gradlew]  # gradlew is tracked without exec bit
        code, log, _ = run([*gradle_cmd, "-p", "android", "assembleDebug", "--console=plain"], env=env, timeout=3600)
        log_all += log
        results.append(check("gradle_assembleDebug", code == 0, log[-4000:]))
        apk = ROOT / "android/app/build/outputs/apk/debug/app-debug.apk"
        exe = "aapt.exe" if WINDOWS else "aapt"
        candidates = sorted((Path(sdk) / "build-tools").glob(f"*/{exe}"),
                            key=lambda p: [int(x) if x.isdigit() else 0 for x in p.parent.name.split(".")],
                            reverse=True)
        aapt = candidates[0] if candidates else Path(sdk) / "build-tools" / "missing" / exe
        if code == 0 and apk.is_file() and aapt.is_file():
            code, log, _ = run([str(aapt), "dump", "badging", str(apk)])
            results.append(check("apk_package_id", "package: name='com.winmindperform.spps.beta15.m12validation'" in log,
                                 log[:600]))
            results.append(check("apk_label", "application-label:'SPPS Free 1.0'" in log, ""))
            perms = sorted(set(re.findall(r"uses-permission: name='([^']+)'", log)))
            allowed = {"android.permission.INTERNET", "android.permission.CAMERA", "android.permission.RECORD_AUDIO",
                       "android.permission.MODIFY_AUDIO_SETTINGS", "android.permission.ACCESS_NETWORK_STATE",
                       "com.winmindperform.spps.beta15.m12validation.DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION"}
            unexpected = [p for p in perms if p not in allowed]
            results.append(check("apk_permissions_allowlist", not unexpected, ", ".join(unexpected)))
        elif code == 0:
            results.append(check("apk_inspection", False, f"apk={apk.is_file()} aapt={aapt.is_file()}"))
    except RuntimeError:
        pass
    finally:
        for step in (["run", "android:configure:bundle"], ["run", "android:sync"]):
            code, log, _ = run([npm, *step], env=env)
            results.append(check("restore " + " ".join(step), code == 0, log[-1500:]))
    res = {"passed": sum(r["ok"] for r in results), "failed": sum(not r["ok"] for r in results),
           "skipped": 0, "errors": 0,
           "tests": {r["id"].replace("static::", "build::"): "passed" if r["ok"] else "failed" for r in results},
           "failures": [{"id": r["id"].replace("static::", "build::"), "file": "", "outcome": "failed",
                         "detail": r["detail"]} for r in results if not r["ok"]]}
    return res, log_all, 0.0


def tier_release(out_dir, _ctx):
    """Phase G: UNSIGNED release-candidate APK + AAB from bundled assets, then verify the
    release identity and hardening flags. Signing is owner-held and never automated."""
    results = []
    sdk = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT") or \
        str(Path(os.environ.get("LOCALAPPDATA", "")) / "Android" / "Sdk")
    env = {"ANDROID_HOME": sdk, "ANDROID_SDK_ROOT": sdk}
    npm = "npm.cmd" if WINDOWS else "npm"
    log_all = ""
    for step in (["run", "android:configure:bundle"], ["run", "android:sync"]):
        code, log, _ = run([npm, *step], env=env)
        log_all += log
        results.append(check("npm " + " ".join(step), code == 0, log[-1500:]))
    gradlew = str(ROOT / "android" / ("gradlew.bat" if WINDOWS else "gradlew"))
    gradle_cmd = [gradlew] if WINDOWS else ["bash", gradlew]
    code, log, _ = run([*gradle_cmd, "-p", "android", "assembleRelease", "bundleRelease", "--console=plain"],
                       env=env, timeout=3600)
    log_all += log
    results.append(check("gradle_assembleRelease_bundleRelease", code == 0, log[-4000:]))
    apk = ROOT / "android/app/build/outputs/apk/release/app-release-unsigned.apk"
    aab = ROOT / "android/app/build/outputs/bundle/release/app-release.aab"
    results.append(check("release_artifacts_exist", apk.is_file() and aab.is_file(), f"apk={apk.is_file()} aab={aab.is_file()}"))
    exe = "aapt.exe" if WINDOWS else "aapt"
    tools = sorted((Path(sdk) / "build-tools").glob(f"*/{exe}"),
                   key=lambda q: [int(x) if x.isdigit() else 0 for x in q.parent.name.split(".")], reverse=True)
    if apk.is_file() and tools:
        aapt = str(tools[0])
        _, badging, _ = run([aapt, "dump", "badging", str(apk)])
        _, tree, _ = run([aapt, "dump", "xmltree", str(apk), "AndroidManifest.xml"])
        results.append(check("release_package_id", "package: name='com.winmindperform.spps.beta15'" in badging, badging[:300]))
        results.append(check("release_not_debuggable", "application-debuggable" not in badging, ""))
        results.append(check("release_no_cleartext", "usesCleartextTraffic(0x010104ec)=(type 0x12)0x0" in tree, ""))
        results.append(check("release_backup_disabled", "allowBackup(0x01010280)=(type 0x12)0x0" in tree, ""))
        perms = sorted(set(re.findall(r"uses-permission: name='([^']+)'", badging)))
        allowed = {"android.permission.INTERNET", "android.permission.CAMERA", "android.permission.RECORD_AUDIO",
                   "android.permission.MODIFY_AUDIO_SETTINGS",
                   "com.winmindperform.spps.beta15.DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION"}
        results.append(check("release_permissions_allowlist", set(perms) <= allowed, ", ".join(sorted(set(perms) - allowed))))
        sha = hashlib.sha256(apk.read_bytes()).hexdigest()
        (out_dir / "release-artifacts.json").write_text(json.dumps({
            "apk": str(apk), "apk_sha256": sha, "aab": str(aab),
            "aab_sha256": hashlib.sha256(aab.read_bytes()).hexdigest() if aab.is_file() else None,
            "signed": False, "note": "Unsigned release candidate; owner signs with the Play upload key."}, indent=2),
            encoding="utf-8")
    else:
        results.append(check("release_inspection", False, f"apk={apk.is_file()} aapt={bool(tools)}"))
    res = {"passed": sum(r["ok"] for r in results), "failed": sum(not r["ok"] for r in results),
           "skipped": 0, "errors": 0,
           "tests": {r["id"].replace("static::", "release::"): "passed" if r["ok"] else "failed" for r in results},
           "failures": [{"id": r["id"].replace("static::", "release::"), "file": "", "outcome": "failed",
                         "detail": r["detail"]} for r in results if not r["ok"]]}
    return res, log_all, 0.0


def tier_validation(out_dir, _ctx):
    """Physical-validation APK: package ...rcvalidation (side by side with Beta 1.5 and M1.2),
    debug-signed, WebView pointed at http://localhost:8010 via adb reverse (tools.device_backend).
    Never installs anything. Restores the committed bundle configuration afterwards."""
    results = []
    sdk = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT") or \
        str(Path(os.environ.get("LOCALAPPDATA", "")) / "Android" / "Sdk")
    env = {"ANDROID_HOME": sdk, "ANDROID_SDK_ROOT": sdk, "SPPS_ANDROID_DEV_URL": "http://localhost:8010/studio"}
    npm = "npm.cmd" if WINDOWS else "npm"
    log_all = ""
    apk = ROOT / "android/app/build/outputs/apk/rcvalidation/app-rcvalidation.apk"
    try:
        for step in (["run", "android:configure:debug"], ["run", "android:sync"]):
            code, log, _ = run([npm, *step], env=env)
            log_all += log
            results.append(check("npm " + " ".join(step), code == 0, log[-1500:]))
        gradlew = str(ROOT / "android" / ("gradlew.bat" if WINDOWS else "gradlew"))
        gradle_cmd = [gradlew] if WINDOWS else ["bash", gradlew]
        code, log, _ = run([*gradle_cmd, "-p", "android", "assembleRcvalidation", "--console=plain"], env=env, timeout=3600)
        log_all += log
        results.append(check("gradle_assembleRcvalidation", code == 0, log[-4000:]))
        exe = "aapt.exe" if WINDOWS else "aapt"
        tools = sorted((Path(sdk) / "build-tools").glob(f"*/{exe}"),
                       key=lambda q: [int(x) if x.isdigit() else 0 for x in q.parent.name.split(".")], reverse=True)
        if apk.is_file() and tools:
            _, badging, _ = run([str(tools[0]), "dump", "badging", str(apk)])
            results.append(check("rc_package_id", "package: name='com.winmindperform.spps.beta15.rcvalidation'" in badging, badging[:300]))
            results.append(check("rc_label", "application-label:'SPPS RC Validation'" in badging, ""))
            import zipfile
            with zipfile.ZipFile(apk) as z:
                cfg = json.loads(z.read("assets/capacitor.config.json"))
                staged = {n for n in z.namelist() if n.startswith("assets/public/")}
            results.append(check("rc_server_url_localhost_8010", cfg.get("server", {}).get("url") == "http://localhost:8010/studio",
                                 json.dumps(cfg.get("server"))))
            results.append(check("rc_portal_assets_bundled", {"assets/public/portal.html", "assets/public/assets/portal.js"} <= staged, ""))
            (out_dir / "validation-apk.json").write_text(json.dumps({
                "apk": str(apk), "sha256": hashlib.sha256(apk.read_bytes()).hexdigest(),
                "package": "com.winmindperform.spps.beta15.rcvalidation", "server": cfg.get("server")}, indent=2),
                encoding="utf-8")
        else:
            results.append(check("rc_apk_inspection", False, f"apk={apk.is_file()} aapt={bool(tools)}"))
    finally:
        for step in (["run", "android:configure:bundle"], ["run", "android:sync"]):
            code, log, _ = run([npm, *step], env={k: v for k, v in env.items() if k != "SPPS_ANDROID_DEV_URL"})
            results.append(check("restore " + " ".join(step), code == 0, log[-1500:]))
    res = {"passed": sum(r["ok"] for r in results), "failed": sum(not r["ok"] for r in results),
           "skipped": 0, "errors": 0,
           "tests": {r["id"].replace("static::", "validation::"): "passed" if r["ok"] else "failed" for r in results},
           "failures": [{"id": r["id"].replace("static::", "validation::"), "file": "", "outcome": "failed",
                         "detail": r["detail"]} for r in results if not r["ok"]]}
    return res, log_all, 0.0


TIERS = {"static": tier_static, "unit": tier_unit, "android-src": tier_android_src,
         "identity": tier_identity, "integration": tier_integration, "browser": tier_browser,
         "hosted": tier_hosted,
         "security": tier_security, "build": tier_build, "release": tier_release,
         "validation": tier_validation}


# ------------------------------------------------------------------ main

def resolve(spec: str) -> list[str]:
    tiers = []
    for part in spec.split(","):
        for t in GROUPS.get(part.strip(), [part.strip()]):
            if t not in TIERS:
                raise Refusal(f"unknown tier {t}")
            if t not in tiers:
                tiers.append(t)
    return tiers


def load_latest():
    latest = RUNS / "latest.json"
    if latest.is_file():
        return json.loads(latest.read_text(encoding="utf-8"))
    return None


def rerun_targets(report) -> dict:
    targets = {}
    for tier, data in (report or {}).get("tiers", {}).items():
        ids = [f["id"] for f in data.get("failures", []) if "::" in f["id"]]
        nodes = []
        for test_id in ids:
            cls, name = test_id.rsplit("::", 1)
            if cls.startswith(("tests.", "tests_integration.", "tests_identity.", "tests_hosted.")):
                nodes.append(cls.replace(".", "/") + ".py::" + name)
        if nodes:
            targets[tier] = nodes
    return targets


def write_reports(out_dir: Path, report: dict) -> None:
    (out_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    lines = [f"# SPPS test run {report['run_id']}", "",
             f"commit `{report['commit']}` · tiers {', '.join(report['tiers'])} · **{report['verdict']}** (exit {report['exit_code']})", "",
             "| tier | passed | failed | errors | skipped | seconds |", "|---|---|---|---|---|---|"]
    for tier, d in report["tiers"].items():
        lines.append(f"| {tier} | {d['passed']} | {d['failed']} | {d['errors']} | {d['skipped']} | {d.get('seconds', 0):.1f} |")
    reg = report.get("regression", {})
    if reg:
        lines += ["", f"Regression vs baseline: new failures {len(reg['new'])}, still failing {len(reg['still'])}, fixed {len(reg['fixed'])}"]
        lines += [f"- NEW: `{x}`" for x in reg["new"]]
    if report.get("flaky"):
        lines += ["", "## Flaky (outcome varied across repeats)"] + [f"- `{x}`" for x in report["flaky"]]
    for tier, d in report["tiers"].items():
        for f in d["failures"]:
            lines += ["", f"## FAIL {f['id']} ({f['outcome']})", "```", f["detail"][-1500:], "```"]
    for note in report.get("notes", []):
        lines += ["", f"> {note}"]
    (out_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (RUNS / "latest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tier", default="fast")
    ap.add_argument("--rerun-failed", action="store_true")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--keep-db", action="store_true")
    ap.add_argument("--update-baseline", action="store_true")
    args = ap.parse_args(argv)

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = RUNS / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    notes = []
    try:
        tiers = resolve(args.tier)
        ctx: dict = {}
        if args.rerun_failed:
            ctx["rerun"] = rerun_targets(load_latest())
            failed_tiers = set((load_latest() or {}).get("failed_tiers", []))
            tiers = [t for t in tiers if t in failed_tiers] or tiers
        commit = run(["git", "rev-parse", "--short", "HEAD"])[1].strip()
        dirty_before = git_dirty()
        fp_before = fingerprint()
        need_db = any(t in DB_TIERS for t in tiers)
        need_cluster = need_db or any(t in HOSTED_TIERS for t in tiers)
        if need_cluster and not guard.SLOT and (ROOT / "output/device-validation/backend.pid").exists():
            raise Refusal("device validation backend is using the disposable DB; "
                          "run `python -m tools.device_backend --stop` first")
        if need_db:
            from tools import ephemeral_db
            print(f"[preflight] starting disposable {guard.PROJECT} database (127.0.0.1:{guard.PORT}) ...", flush=True)
            ctx["db"] = ephemeral_db.up()
            from dotenv import dotenv_values
            vals = dotenv_values(ctx["db"]["integration_env"])
            for key in ("DATABASE_URL", "SPPS_AUTH_DATABASE_URL"):
                guard.verify_ephemeral_target(vals[key], database="spps_dev")
        all_runs = []
        try:
            for i in range(max(1, args.repeat)):
                results = {}
                for tier in tiers:
                    print(f"[{tier}] running (repeat {i + 1}/{args.repeat}) ...", flush=True)
                    res, log, secs = TIERS[tier](out_dir, ctx)
                    res["seconds"] = secs
                    (out_dir / f"{tier}.log").write_text(log, encoding="utf-8")
                    results[tier] = res
                    print(f"[{tier}] passed={res['passed']} failed={res['failed']} errors={res['errors']} "
                          f"skipped={res['skipped']} ({secs:.0f}s)", flush=True)
                all_runs.append(results)
        finally:
            if need_cluster and not args.keep_db:
                from tools import ephemeral_db
                ephemeral_db.down()
        fp_after = fingerprint()
        dirty_after = git_dirty()
    except (Refusal, guard.EphemeralGuardError) as exc:
        print(f"SAFETY REFUSAL: {exc}", file=sys.stderr)
        return 2

    final = all_runs[-1]
    flaky = []
    if len(all_runs) > 1:
        ids = set().union(*[set(r[t]["tests"]) for r in all_runs for t in r])
        for test_id in sorted(ids):
            seen = {r[t]["tests"].get(test_id) for r in all_runs for t in r if test_id in r[t]["tests"]}
            if len(seen) > 1:
                flaky.append(test_id)
    failing = sorted(f["id"] for d in final.values() for f in d["failures"])
    regression = {}
    if BASELINE.is_file():
        base = set(json.loads(BASELINE.read_text(encoding="utf-8")).get("failing", []))
        regression = {"new": sorted(set(failing) - base), "still": sorted(set(failing) & base),
                      "fixed": sorted(base - set(failing))}
    exit_code = 0
    if failing or flaky:
        exit_code = 1
    if fp_before != fp_after:
        exit_code = 3
        notes.append("PROTECTED ENVIRONMENT FINGERPRINT CHANGED: " + json.dumps(
            {k: (fp_before.get(k), fp_after.get(k)) for k in fp_after if fp_before.get(k) != fp_after.get(k)}))
    newly_dirty = sorted(dirty_after - dirty_before)
    if newly_dirty:
        exit_code = 4 if exit_code in (0, 1) else exit_code
        notes.append("Tracked files modified by the run: " + ", ".join(newly_dirty))
    report = {"run_id": run_id, "commit": commit, "tiers": final, "failed_tiers":
              [t for t, d in final.items() if d["failures"]], "flaky": flaky, "regression": regression,
              "repeat": args.repeat, "protected_fingerprint_unchanged": fp_before == fp_after,
              "exit_code": exit_code,
              "verdict": {0: "PASS", 1: "FAIL", 3: "PROTECTED-ENV-CHANGED", 4: "TREE-MODIFIED"}[exit_code],
              "notes": notes}
    write_reports(out_dir, report)
    if args.update_baseline:
        BASELINE.write_text(json.dumps({"run_id": run_id, "commit": commit, "failing": failing}, indent=2),
                            encoding="utf-8")
    total = {k: sum(d[k] for d in final.values()) for k in ("passed", "failed", "errors", "skipped")}
    print(f"\n{report['verdict']}  passed={total['passed']} failed={total['failed']} errors={total['errors']} "
          f"skipped={total['skipped']}  report: {out_dir / 'report.md'}")
    for f in failing:
        print("  FAIL", f)
    for n in notes:
        print("  NOTE", n)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
