"""Review Git's publishable file set without ever printing candidate secrets."""
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = [
    re.compile(rb"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})"),
    re.compile(rb"sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{30,}"),
    re.compile(rb"AKIA[A-Z0-9]{16}"),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"[CP]:[\\/]Users[\\/][^\\/\s]+", re.I),
]

def main():
    raw = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT)
    paths = sorted(set(p for p in raw.decode("utf-8").split("\0") if p))
    issues = []
    for relative in paths:
        path = ROOT / relative
        parts = path.relative_to(ROOT).parts
        if ((path.name.startswith(".env") and path.name != ".env.example")
                or any(part in {".venv", "node_modules", ".next", ".local", ".cache", "uploads", "outputs", "interior_reports", "logs", "artifacts"} for part in parts)
                or path.suffix.lower() in {".db", ".sqlite", ".sqlite3", ".log", ".pt", ".onnx", ".pdf", ".zip"}):
            issues.append((relative, "private/runtime file"))
            continue
        data = path.read_bytes()
        if len(data) > 2_000_000:
            issues.append((relative, "unexpected large file"))
        if any(pattern.search(data) for pattern in PATTERNS):
            issues.append((relative, "sensitive-looking content; inspect locally"))
    for relative, reason in issues:
        print(f"FAIL {relative}: {reason}")
    print(f"Audited {len(paths)} publishable files; {len(issues)} findings. Values were not printed.")
    return 1 if issues else 0

if __name__ == "__main__":
    sys.exit(main())
