"""Keep current schema revision guidance synchronized with the Alembic head."""

from __future__ import annotations

import re
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[3]
START_MARKER = "<!-- current-schema-revision:start -->"
END_MARKER = "<!-- current-schema-revision:end -->"
REVISION_PATTERN = re.compile(r"`(\d{8}_\d{4}_[a-z0-9]+(?:_[a-z0-9]+)*)`")
PREVIOUS_ACCEPTED_REVISION = "20261016_1200_audit_reqid128"
DOCS = (ROOT / "README.md", ROOT / "docs/tasks/fastapi-bootstrap.md")


def _current_block(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    assert text.count(START_MARKER) == 1, f"{path}: expected one current revision start marker"
    assert text.count(END_MARKER) == 1, f"{path}: expected one current revision end marker"
    start = text.index(START_MARKER) + len(START_MARKER)
    end = text.index(END_MARKER)
    assert start < end, f"{path}: current revision markers are out of order"
    return text[start:end]


def test_current_schema_revision_docs_match_the_single_alembic_head() -> None:
    heads = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_heads()
    assert len(heads) == 1, f"Expected one Alembic head, found: {heads}"

    for path in DOCS:
        revisions = REVISION_PATTERN.findall(_current_block(path))
        assert revisions == heads, f"{path}: current revision must equal Alembic head {heads}"


def test_previous_accepted_revision_is_labeled_historical_outside_current_block() -> None:
    for path in DOCS:
        text = path.read_text(encoding="utf-8")
        block = _current_block(path)
        assert (
            PREVIOUS_ACCEPTED_REVISION not in block
        ), f"{path}: previous accepted revision must not appear in the current block"
        position = text.find(PREVIOUS_ACCEPTED_REVISION)
        assert position >= 0, f"{path}: preserve the previous revision as historical context"
        history_context = text[max(0, position - 160) : position].lower()
        assert (
            "historical" in history_context or "lịch sử" in history_context
        ), f"{path}: previous revision must be explicitly labeled historical"
