import copy

import pytest
from tools.deploy.release_manifest import SCHEMA
from tools.deploy.rollout_schema import EXPECTED, SchemaSnapshot, require_schema, snapshot
from tools.deploy.rollout_state import RolloutError


def current():
    return dict(
        tables=copy.deepcopy(EXPECTED),
        revisions=[SCHEMA],
        unsupported_objects=0,
        nontransactional_tables=0,
    )


def test_full_delivered_schema_is_accepted_without_downgrading():
    assert require_schema(SchemaSnapshot(**current()), [SCHEMA]) == "current"


def test_only_an_empty_bootstrap_can_run_initial_migrations():
    empty = SchemaSnapshot(
        tables={}, revisions=[], unsupported_objects=0, nontransactional_tables=0
    )
    assert require_schema(empty, [SCHEMA], allow_empty=True) == "bootstrap"
    with pytest.raises(RolloutError, match="schema-partial"):
        require_schema(empty, [SCHEMA])


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["tables"].pop("users"),
        lambda d: d["tables"].update(unmanaged=["id"]),
        lambda d: d["tables"]["users"].pop(),
        lambda d: d["tables"]["users"].append("unexpected"),
        lambda d: d.update(revisions=[]),
        lambda d: d.update(revisions=[SCHEMA, "second_head"]),
        lambda d: d.update(revisions=["unknown_future"]),
        lambda d: d.update(unsupported_objects=1),
        lambda d: d.update(nontransactional_tables=1),
    ],
)
def test_revision_stamp_does_not_hide_partial_or_unsupported_schema(change):
    data = current()
    change(data)
    with pytest.raises(RolloutError):
        require_schema(SchemaSnapshot(**data), [SCHEMA], allow_empty=True)


def test_incompatible_rollback_target_is_refused_even_for_a_valid_database():
    with pytest.raises(RolloutError, match="schema-incompatible"):
        require_schema(SchemaSnapshot(**current()), ["older_schema"])


def test_catalog_contains_only_schema_metadata_and_rejects_malformed_response():
    catalog = dict(
        columns=[dict(table="users", column="id")], unsupported_objects=0, nontransactional_tables=0
    )
    assert snapshot(catalog, [SCHEMA]).tables == {"users": ["id"]}
    with pytest.raises(RolloutError, match="schema-partial"):
        snapshot({}, [])
