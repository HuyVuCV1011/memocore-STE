from __future__ import annotations

import json
import subprocess
import sys


def main() -> int:
    files = subprocess.check_output(["git", "ls-files"], text=True).splitlines()
    files = [item for item in files if item.strip()]
    if not files:
        print("No tracked files to scan.")
        return 0

    command = [
        sys.executable,
        "-m",
        "detect_secrets",
        "scan",
        "--no-verify",
        "--exclude-lines",
        "example|placeholder|your-token|test-token|dummy|fake|sk-test|gemini-key|groq-key|api_key == \"key\"|MODEL_API_KEY",
        *files,
    ]
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        print(completed.stdout)
        print(completed.stderr, file=sys.stderr)
        return completed.returncode

    report = json.loads(completed.stdout or "{}")
    findings = {
        filename: items
        for filename, items in report.get("results", {}).items()
        if items
    }
    if not findings:
        print(f"Secret scan passed for {len(files)} tracked files.")
        return 0

    print("Secret scan found potential secrets in tracked files:")
    for filename, items in findings.items():
        labels = ", ".join(
            f"{item.get('type', 'Unknown')}@{item.get('line_number', '?')}"
            for item in items
        )
        print(f"- {filename}: {labels}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
