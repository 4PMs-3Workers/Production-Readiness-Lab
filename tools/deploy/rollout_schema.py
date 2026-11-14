"""Classify the delivered schema before migration, rollout, or rollback."""

from typing import Literal

from pydantic import Field
from tools.deploy.release_manifest import REVIEWED_PREDECESSOR_SCHEMAS, SCHEMA, Strict
from tools.deploy.rollout_state import RolloutError

from app.models import Base

# The registry consumer already restricts manifests to this delivered contract.
# Revision stamps alone cannot prove that a partially applied DDL is usable.
EXPECTED = {name: sorted(table.columns.keys()) for name, table in Base.metadata.tables.items()}
EXPECTED["alembic_version"] = ["version_num"]
PREDECESSOR_EXPECTED = {name: list(columns) for name, columns in EXPECTED.items()}
PREDECESSOR_EXPECTED["service_settings"].remove("max_active_appointments")


class SchemaSnapshot(Strict):
    tables: dict[str, list[str]]
    revisions: list[str] = Field(max_length=32)
    unsupported_objects: int = Field(ge=0)
    nontransactional_tables: int = Field(ge=0)


def require_schema(
    snapshot: SchemaSnapshot, accepted: list[str], *, allow_empty: bool = False
) -> Literal["bootstrap", "current"]:
    # The selected release must declare the schema revision actually delivered.
    # An older release pin cannot prove it can run on a forward-migrated database.
    if len(accepted) != 1 or accepted[0] not in {SCHEMA, *REVIEWED_PREDECESSOR_SCHEMAS}:
        raise RolloutError("schema-incompatible")
    if not snapshot.tables and not snapshot.revisions:
        if allow_empty and snapshot.unsupported_objects == 0:
            return "bootstrap"
        raise RolloutError("schema-partial")
    if len(snapshot.revisions) != 1:
        raise RolloutError("schema-incompatible")
    delivered_revision = snapshot.revisions[0]
    if delivered_revision != accepted[0]:
        raise RolloutError("schema-incompatible")
    observed = {name: sorted(columns) for name, columns in snapshot.tables.items()}
    expected = EXPECTED if delivered_revision == SCHEMA else PREDECESSOR_EXPECTED
    catalog_incompatible = observed != expected
    if (
        catalog_incompatible
        or snapshot.unsupported_objects != 0
        or snapshot.nontransactional_tables != 0
    ):
        raise RolloutError("schema-partial")
    return "current"


# Metadata only, read through the existing MySQL administrator socket. No account,
# session, consultation, or SQL exception text is exported by this probe.
CATALOG_SQL = """
SELECT JSON_OBJECT(
 'columns', (SELECT COALESCE(JSON_ARRAYAGG(
                JSON_OBJECT('table', TABLE_NAME, 'column', COLUMN_NAME)), JSON_ARRAY())
             FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='prl'),
 'unsupported_objects',
                       (SELECT COUNT(*) FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA='prl')
                     + (SELECT COUNT(*) FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA='prl')
                     + (SELECT COUNT(*) FROM information_schema.EVENTS WHERE EVENT_SCHEMA='prl'),
 'nontransactional_tables', (SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA='prl'
                           AND (ENGINE <> 'InnoDB' OR ENGINE IS NULL OR TABLE_TYPE <> 'BASE TABLE'))
);
"""
REVISION_SQL = "SELECT COALESCE(JSON_ARRAYAGG(version_num), JSON_ARRAY()) FROM prl.alembic_version;"


def snapshot(catalog: dict, revisions: list[str]) -> SchemaSnapshot:
    try:
        tables: dict[str, list[str]] = {}
        for row in catalog["columns"]:
            tables.setdefault(row["table"], []).append(row["column"])
        return SchemaSnapshot(
            tables=tables,
            revisions=revisions,
            unsupported_objects=catalog["unsupported_objects"],
            nontransactional_tables=catalog["nontransactional_tables"],
        )
    except (KeyError, TypeError, ValueError):
        raise RolloutError("schema-partial") from None
