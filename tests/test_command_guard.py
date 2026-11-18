"""M0: the Claude Code PreToolUse guard blocks destructive/out-of-scope actions."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("guard_commands", ROOT / ".claude/hooks/guard_commands.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


@pytest.mark.parametrize("cmd", [
    "docker volume rm spps-canonical_spps_development",
    "docker volume prune -f",
    "docker system prune -a",
    "docker rm -fv spps-m12-physical-20261110095258",
    "docker compose -p spps-canonical down -v",
    "docker compose -f compose.identity-review.yml up -d --force-recreate",
    "docker stop spps-canonical-database-1",
    "docker exec -it spps-identity-review-database-1 psql -U postgres",
    "psql -h 127.0.0.1 -p 55432 -U postgres spps_dev",
    "gh pr merge 6 --squash",
    "git push --force origin integration/spps-full-stack-consolidation-2026-11-10",
    "git push origin HEAD:main",
    "git push origin main",
    "git clean -fdx",
    "cat .env.integration",
    "Get-Content .env.identity-review-access",
    "python -m tools.development_db",
    ".venv/Scripts/python.exe -m tools.identity_review_db install",
    "vercel deploy --prod",
    "supabase db push",
    "Remove-Item .env.integration",
    "adb install app-debug.apk",
])
def test_blocked(cmd):
    assert guard.blocked_command(cmd), cmd


@pytest.mark.parametrize("cmd", [
    "docker compose -f compose.test-ephemeral.yml -p spps-test-ephemeral down --remove-orphans",
    "docker inspect spps-canonical-database-1",
    "docker ps -a",
    "git push origin integration/spps-full-stack-consolidation-2026-11-10",
    "git status --short",
    "cat .env.example",
    "python -m tools.ephemeral_db up",
    "python -m tools.identity_review_db install --target ephemeral",
    "python -m tools.spps_check --tier full",
    "gh pr view 6",
    "npm run android:sync",
])
def test_allowed(cmd):
    assert guard.blocked_command(cmd) is None, cmd


def run_hook(payload):
    return subprocess.run([sys.executable, str(ROOT / ".claude/hooks/guard_commands.py")],
                          input=json.dumps(payload), text=True, capture_output=True)


def test_hook_exit_codes_and_secret_file_reads():
    assert run_hook({"tool_name": "Bash", "tool_input": {"command": "gh pr merge 6"}}).returncode == 2
    assert run_hook({"tool_name": "Bash", "tool_input": {"command": "git status"}}).returncode == 0
    assert run_hook({"tool_name": "Read", "tool_input": {"file_path": str(ROOT / ".env.integration")}}).returncode == 2
    assert run_hook({"tool_name": "Read", "tool_input": {"file_path": str(ROOT / ".env.example")}}).returncode == 0
    assert run_hook({"tool_name": "Read", "tool_input": {"file_path": str(ROOT / "server/main.py")}}).returncode == 0


def test_psql_against_dynamic_m12_port_blocked(monkeypatch):
    monkeypatch.setattr(guard, "live_protected_ports", lambda: ("32768",))
    assert guard.blocked_command("psql -h 127.0.0.1 -p 32768 -U postgres")
    assert guard.blocked_command("pg_dump -p 32768 spps_dev")
    assert guard.blocked_command("psql -p 327680") is None
