from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

RUNTIME_PINS = {
    "asyncmy": "0.2.16",
    "cryptography": "50.0.2",
    "mako": "1.3.12",
}
DEVELOPMENT_PINS = {
    "black": "26.3.1",
    "pytest": "9.0.3",
    "pytest-asyncio": "1.4.0",
    "pytokens": "0.4.1",
    "markupsafe": "3.0.4",
    "nodeenv": "1.11.0",
}


def _requirements(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"\s*([A-Za-z0-9_.-]+)==([^\s;]+)\s*", line)
        if match:
            pins[match.group(1).lower().replace("_", "-")] = match.group(2)
    return pins


def test_runtime_requirements_and_project_metadata_share_audited_pins() -> None:
    requirements = _requirements(ROOT / "requirements.txt")
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project_pins = {}
    for requirement in project["project"]["dependencies"]:
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s;]+)", requirement)
        assert match, f"Runtime dependency must stay exactly pinned: {requirement}"
        project_pins[match.group(1).lower().replace("_", "-")] = match.group(2)

    for package, version in RUNTIME_PINS.items():
        normalized_name = package.lower().replace("_", "-")
        assert requirements.get(normalized_name) == version
        assert project_pins.get(normalized_name) == version


def test_development_requirements_use_audited_pins() -> None:
    requirements = _requirements(ROOT / "requirements-dev.txt")
    for package, version in DEVELOPMENT_PINS.items():
        normalized_name = package.lower().replace("_", "-")
        assert requirements.get(normalized_name) == version
