"""PreToolUse guard: blocks destructive or out-of-scope actions before they run.

Exit code 2 blocks the tool call and shows the reason to Claude. This guard is
deliberately conservative; the owner can still run any command manually.
Covered: protected PostgreSQL environments, Docker volume destruction, merges,
force pushes, deploys, secret-file reads and unguarded provisioners.
"""
import json
import re
import sys

PROTECTED_CONTAINERS = (
    "spps-canonical-database-1", "spps-milestone-1-database-1",
    "spps-identity-review-database-1", "spps-m12-physical-20261110095258",
    "supabase_db_SPSP_MVP", "supabase_",
)
PROTECTED_PROJECTS = ("spps-canonical", "spps-milestone-1", "spps-identity-review", "SPSP_MVP")
PROTECTED_PORTS = ("55432", "55433", "55434", "60528", "54322")

RULES = [
    (r"\bdocker(\.exe)?\s+volume\s+(rm|remove|prune)\b", "Docker volume deletion is prohibited"),
    (r"\bdocker(\.exe)?\s+(system|container|image|builder|network)\s+prune\b", "Docker prune is prohibited"),
    (r"\bdocker(\.exe)?\s+(container\s+)?rm\b[^|;&]*\s(-v|--volumes|-[a-z]*v[a-z]*)\b", "docker rm -v is prohibited"),
    (r"\bdocker(\.exe)?\s+compose\b[^|;&]*\bdown\b[^|;&]*(\s-v\b|--volumes|--rmi)", "compose down -v/--volumes is prohibited"),
    (r"\bgh\s+pr\s+merge\b", "Merging pull requests requires the owner"),
    (r"\bgh\s+(repo\s+(delete|archive|edit)|release\s+create|secret\s+set|workflow\s+run)\b", "GitHub admin/release action requires the owner"),
    (r"\bgit\s+push\b[^|;&]*(\s--force\b|\s-f\b|--force-with-lease|\s\+\S)", "Force push is prohibited"),
    (r"\bgit\s+push\b[^|;&]*\s(main|HEAD:main|refs/heads/main)(\s|$)", "Pushing to main is prohibited"),
    (r"\bgit\s+push\b[^|;&]*\s--delete\b|\bgit\s+push\b[^|;&]*\s:\S", "Deleting remote branches is prohibited"),
    (r"\bgit\s+clean\b[^|;&]*-[a-zA-Z]*[xX]", "git clean -x would delete ignored local credentials/.env files"),
    (r"\bgit\s+(filter-branch|filter-repo)\b|\bgit\s+rebase\b[^|;&]*\borigin/", "History rewriting is prohibited"),
    (r"\b(vercel|netlify|flyctl|fly|heroku)\s+(deploy|--prod|promote)\b|\bvercel\b[^|;&]*--prod", "Deployments require the owner"),
    (r"\bsupabase\s+(db\s+(push|reset)|migration\s+(up|repair)|link|projects\s+(create|delete))\b", "Hosted Supabase changes require the owner"),
    (r"\brender\s+(deploy|services)\b", "Render changes require the owner"),
    (r"-m\s+tools\.development_db\b", "Run provisioning only through tools.ephemeral_db (guarded)"),
    (r"-m\s+tools\.identity_review_db\s+install(?![^|;&]*--target\s+ephemeral)", "Identity installer only with --target ephemeral"),
    (r"\b(rm|del|Remove-Item|erase)\b[^|;&]*(\.env(\.|\s|$|\")|\.venv\b|\.git\b|node_modules\b)", "Deleting credentials/venv/git data is prohibited"),
    (r"\b(cat|type|Get-Content|gc|head|tail|less|more|sed|awk|grep|rg|Select-String|findstr|bat|strings|xxd|od|base64)\b[^|;&]*(^|[\s\"'/\\])\.env(\.integration|\.identity-review(-access)?)?(?!\.example)(\s|\"|'|$)",
     "Reading secret .env files is prohibited (use the redacted key list instead)"),
    (r"\b(DROP|TRUNCATE)\s+(DATABASE|SCHEMA|TABLE|ROLE|OWNED)\b", "Destructive SQL from the shell is prohibited; tests use the disposable DB via Python"),
    (r"\bgradlew(\.bat)?\b[^|;&]*\b(publish|bundleRelease|assembleRelease)\b[^|;&]*--?signing", "Signed release builds require the owner"),
    (r"\badb\b[^|;&]*\b(install|uninstall|shell\s+pm\s+(clear|uninstall))\b", "Physical device actions require the owner"),
]


def blocked_command(cmd: str):
    flat = " ".join(cmd.split())
    for pattern, reason in RULES:
        if re.search(pattern, flat, re.IGNORECASE if "SQL" in reason else 0):
            return reason
    lowered = flat
    for name in PROTECTED_CONTAINERS:
        if name in lowered and re.search(
                r"\bdocker(\.exe)?\s+(rm|stop|kill|restart|pause|exec|start|update|rename|cp|commit|container\s+(rm|stop|kill|restart|exec))\b",
                lowered):
            return f"Protected container {name}: only read-only inspect/ps/logs allowed"
    for project in PROTECTED_PROJECTS:
        if re.search(r"\bdocker(\.exe)?\s+compose\b", lowered) and (
                f"-p {project}" in lowered or f"--project-name {project}" in lowered
                or "compose.development.yml" in lowered or "compose.identity-review.yml" in lowered) \
                and re.search(r"\b(down|rm|stop|restart|up|create|kill|exec|run)\b", lowered):
            return "Compose actions on protected projects are prohibited"
    if re.search(r"\b(psql|pg_restore|pg_dump|pg_dumpall|dropdb|createdb|pgcli)\b", lowered):
        for port in (*PROTECTED_PORTS, *live_protected_ports()):
            if re.search(rf"(?<!\d){port}(?!\d)", lowered):
                return f"Direct database clients against protected port {port} are prohibited"
    return None


def live_protected_ports():
    """Host ports currently published by protected containers.

    spps-m12-physical-* binds `127.0.0.1::5432`, so Docker assigns a NEW host
    port on every start (60528 at audit time, 32768 after the power loss).
    """
    import subprocess
    found = set()
    for name in PROTECTED_CONTAINERS:
        if name.endswith("_"):
            continue
        try:
            out = subprocess.run(["docker", "inspect", "--format",
                                  "{{json .HostConfig.PortBindings}}{{json .NetworkSettings.Ports}}", name],
                                 capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.TimeoutExpired):
            continue
        found.update(re.findall(r'"HostPort":"(\d+)"', out))
    return tuple(sorted(found))


SECRET_FILE = re.compile(r"(^|[\\/])\.env(\.integration|\.identity-review(-access)?)?$")


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    tool = payload.get("tool_name", "")
    data = payload.get("tool_input", {}) or {}
    reason = None
    if tool in ("Bash", "PowerShell"):
        reason = blocked_command(data.get("command", ""))
    elif tool in ("Read", "Edit", "Write", "NotebookEdit"):
        path = str(data.get("file_path", ""))
        if SECRET_FILE.search(path):
            reason = "Protected local credential file"
    if reason:
        print(f"BLOCKED by SPPS guard: {reason}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
