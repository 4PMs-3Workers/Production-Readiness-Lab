"""Export the health API contract without starting lifespan or accessing MySQL."""

import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src/backend"))

from app.core.config import Settings  # noqa: E402
from app.factory import create_app  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Export deterministic FastAPI OpenAPI YAML")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/api/openapi.yaml")
    args = parser.parse_args()
    settings = Settings.model_validate(
        {
            "DATABASE_URL": "mysql+asyncmy://docs:placeholder@127.0.0.1:1/docs",
            "APPLICATION_ORIGIN": "https://example.invalid",
            "COOKIE_SECRET": "documentation-placeholder-cookie-secret",
            "CSRF_SECRET": "documentation-placeholder-csrf-secret",
            "ACCEPTED_SCHEMA_REVISIONS": "a1b2c3d4e5f6",
        }
    )
    document = create_app(settings=settings).openapi()
    output = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        yaml.safe_dump(document, sort_keys=True, allow_unicode=True), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
