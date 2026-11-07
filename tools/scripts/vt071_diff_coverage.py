"""Report coverage on executable added/changed lines relative to the official baseline."""

import argparse
import json
import re
import subprocess
from pathlib import Path

BASELINE = "aa8c5b0be057754f9b3409c8699537d4aeb887b9"
FILES = [
    "src/backend/app/api/appointments.py",
    "src/backend/app/repositories/appointments.py",
    "src/backend/app/schemas/appointments.py",
    "src/backend/app/services/appointment_export.py",
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--coverage", default="docs/assignments/VT-071/evidence/review-coverage-full.json"
    )
    parser.add_argument(
        "--output", default="docs/assignments/VT-071/evidence/review-diff-coverage.json"
    )
    args = parser.parse_args()
    measured = json.loads(Path(args.coverage).read_text(encoding="utf-8"))["files"]
    measured = {name.replace("\\", "/"): value for name, value in measured.items()}
    result = {
        "baseline": BASELINE,
        "denominator": "added lines intersect executable statements",
        "files": [],
        "covered": 0,
        "executable_changed": 0,
    }
    for name in FILES:
        patch = subprocess.run(
            ["git", "diff", "--no-ext-diff", "--unified=0", BASELINE, "--", name],
            capture_output=True,
            text=True,
            check=True,
            encoding="utf-8",
        ).stdout
        added = set()
        current = None
        for line in patch.splitlines():
            if line.startswith("@@"):
                current = int(re.search(r"\+(\d+)", line).group(1))
            elif current is not None and not line.startswith(("---", "+++", "\\")):
                if line.startswith("+"):
                    added.add(current)
                    current += 1
                elif not line.startswith("-"):
                    current += 1
        data = measured[name]
        executed = set(data["executed_lines"])
        missing = set(data["missing_lines"])
        executable = added & (executed | missing)
        arcs = [
            arc
            for arc in data["executed_branches"] + data["missing_branches"]
            if arc[0] in executable
        ]
        missed_arcs = [arc for arc in data["missing_branches"] if arc[0] in executable]
        row = {
            "file": name,
            "executable_changed": len(executable),
            "covered": len(executable & executed),
            "missing_lines": sorted(executable & missing),
            "changed_lines": sorted(executable),
            "branch_arcs_from_changed_lines": len(arcs),
            "missing_branch_arcs": missed_arcs,
        }
        result["files"].append(row)
        result["covered"] += row["covered"]
        result["executable_changed"] += row["executable_changed"]
    result["line_percent"] = 100 * result["covered"] / result["executable_changed"]
    Path(args.output).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
