import copy
import json
from typing import Any
from uuid import uuid4

import pytest

from app.backup.config import BackupError, Reason
from app.backup.manifest import manifest_bytes, parse_manifest
from app.backup.schema import migration_contract
from app.models import Base


def sample_manifest() -> dict[str, Any]:
    revision, digest = migration_contract()
    tables = []
    for name in sorted(set(Base.metadata.tables) | {"alembic_version"}):
        model = Base.metadata.tables.get(name)
        columns = list(model.columns.keys()) if model is not None else ["version_num"]
        generated = (
            {column.name for column in model.columns if column.computed is not None}
            if model is not None
            else set()
        )
        keys = list(model.primary_key.columns.keys()) if model is not None else columns
        ddl = (
            f"CREATE TABLE `{name}` (\n"
            + ",\n".join(
                f"  `{column}` int"
                + (" GENERATED ALWAYS AS (1) VIRTUAL" if column in generated else "")
                for column in columns
            )
            + ",\n  PRIMARY KEY ("
            + ",".join(f"`{key}`" for key in keys)
            + ")\n) ENGINE=InnoDB"
        )
        tables.append(
            dict(
                name=name,
                ddl=ddl,
                columns=columns,
                writable=[column for column in columns if column not in generated],
                primary_key=keys,
            )
        )
    return dict(
        format="prl-mysql-jsonl",
        format_version=1,
        state="complete",
        backup_id="prl-backup-20261028T120000Z-" + uuid4().hex,
        started_at="2026-10-28T12:00:00.000000Z",
        completed_at="2026-10-28T12:00:01.000000Z",
        snapshot=dict(
            established_before="2026-10-28T12:00:00.100000Z",
            established_after="2026-10-28T12:00:00.110000Z",
        ),
        source=dict(
            revision=revision,
            migration_sha256=digest,
            server_uuid=str(uuid4()),
            server_version="8.4.11",
            charset="utf8mb4",
            collation="utf8mb4_0900_ai_ci",
            database="prl",
            tables=tables,
        ),
        source_sha="a" * 40,
        image_id=None,
        files={table["name"]: dict(rows=1, bytes=1, sha256="0" * 64) for table in tables},
    )


def test_strict_manifest_roundtrip_includes_snapshot_and_release_identity():
    value = sample_manifest()
    parsed = parse_manifest(manifest_bytes(value), expected_sha="a" * 40)
    assert parsed.source.revision == migration_contract()[0] and parsed.state == "complete"


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "extra",
        "version",
        "negative",
        "bool-count",
        "table",
        "ddl",
        "column",
        "incomplete",
    ],
)
def test_inconsistent_or_unsafe_manifest_is_never_accepted(change):
    value = copy.deepcopy(sample_manifest())
    if change == "missing":
        del value["snapshot"]
    elif change == "extra":
        value["password"] = "private"
    elif change == "version":
        value["format_version"] = True
    elif change == "negative":
        value["files"]["users"]["rows"] = -1
    elif change == "bool-count":
        value["files"]["users"]["rows"] = True
    elif change == "table":
        del value["files"]["users"]
    elif change == "ddl":
        value["source"]["tables"][0]["ddl"] += "; DROP DATABASE prl"
    elif change == "column":
        value["source"]["tables"][0]["columns"].append("not_present")
    elif change == "incomplete":
        value["state"] = "partial"
    with pytest.raises(BackupError) as error:
        parse_manifest(json.dumps(value).encode())
    assert error.value.reason == Reason.INTEGRITY


def test_release_and_current_migration_contract_are_required():
    value = sample_manifest()
    with pytest.raises(BackupError) as error:
        parse_manifest(manifest_bytes(value), expected_sha="b" * 40)
    assert error.value.reason == Reason.INCOMPATIBLE
    value["source"]["migration_sha256"] = "f" * 64
    with pytest.raises(BackupError) as error:
        parse_manifest(json.dumps(value).encode())
    assert error.value.reason == Reason.INCOMPATIBLE


def test_duplicate_keys_and_truncated_json_fail_closed():
    for raw in (b'{"format_version":1,"format_version":1}', b'{"format":'):
        with pytest.raises(BackupError):
            parse_manifest(raw)
