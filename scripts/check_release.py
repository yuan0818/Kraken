"""Check tracked release files without printing credential values."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    problems = []
    key = re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|AKIA[A-Z0-9]{16}|sk-(?:ant-|proj-)?[A-Za-z0-9_-]{25,})"
    )
    for name in filter(None, paths):
        path = Path(name)
        if (
            any(part in {".runtime", ".venv", "node_modules"} for part in path.parts)
            or path.name == ".env"
            or (path.name.startswith(".env.") and not path.name.endswith(".example"))
            or path.suffix in {".pem", ".key", ".sqlite", ".sqlite3", ".db", ".dump"}
        ):
            problems.append(f"{name}: private/runtime file must not be tracked")
        try:
            content = (ROOT / path).read_text()
        except UnicodeError:
            continue
        for number, line in enumerate(content.splitlines(), 1):
            if key.search(line):
                problems.append(
                    f"{name}:{number}: possible credential; review without logging its value"
                )
    script = (ROOT / "scripts/linux-preflight.sh").read_text()
    for unit in re.findall(r"deploy/systemd/[\w.-]+\.service", script):
        if not (ROOT / unit).is_file():
            problems.append(f"{unit}: preflight references a missing service")
    if problems:
        raise SystemExit("\n".join(problems))
    print("Release file checks passed (pattern scan, not a credential audit guarantee).")


if __name__ == "__main__":
    main()
