"""Measure export coverage and record the exact source bytes used by the run."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from vt071_diff_coverage import FILES


def main():
    output = Path("build/vt071/coverage.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    hashes = {name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in FILES}
    subprocess.run(
        [sys.executable, "-m", "coverage", "run", "--source=app", "-m", "pytest", *sys.argv[1:]],
        check=True,
    )
    subprocess.run([sys.executable, "-m", "coverage", "json", "-o", str(output)], check=True)
    if any(
        hashlib.sha256(Path(name).read_bytes()).hexdigest() != digest
        for name, digest in hashes.items()
    ):
        raise SystemExit("Source changed during coverage measurement")
    metadata = {
        "source_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "source_dirty": bool(subprocess.check_output(["git", "diff", "HEAD", "--", *FILES])),
        "file_sha256": hashes,
        "coverage_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
    }
    Path(str(output) + ".source.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
