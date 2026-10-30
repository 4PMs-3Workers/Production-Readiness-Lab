import pytest

from app.backup.grants import metadata_visible

ROUTINES = "GRANT BACKUP_ADMIN,SHOW_ROUTINE ON *.* TO `backup_probe`@`%`"


def grant(scope, privileges="SELECT, EVENT, TRIGGER"):
    return f"GRANT {privileges} ON {scope} TO `backup_probe`@`%`"


@pytest.mark.parametrize(
    "scope,database,partial_revokes,visible",
    [
        (r"`prl\_app`.*", "prl_app", False, True),
        (r"`prl\_app`.*", "prlXapp", False, False),
        (r"`prl\_restore\_test`.*", "prl_restore_test", False, True),
        (r"`prl\_restore\_test`.*", "prlXrestoreYtest", False, False),
        (r"`prl\_app`.*", "prl_app_extra", False, False),
        (r"`prl\_app`.*", "other_prl_app", False, False),
        ("`prl_app`.*", "prl_app", False, True),
        ("`prl_app`.*", "prlXapp", False, True),
        ("`prl_app`.*", "prl_app", True, True),
        ("`prl_app`.*", "prlXapp", True, False),
        (r"`prl\_app`.*", "prl_app", True, False),
        (r"`prl\_app`.*", r"prl\_app", True, True),
        ("`prl%`.*", "prl_app", False, True),
        ("`prl%`.*", "prl_app", True, False),
        (r"`prl\%app`.*", "prl%app", False, True),
        (r"`prl\%app`.*", "prl_app", False, False),
        (r"`prl\\app`.*", "prl\\app", False, True),
        ("`prl``app`.*", "prl`app", False, True),
        ("`other`.*", "prl_app", False, False),
        ("`PRL_app`.*", "prl_app", False, False),
        ("`prl_app`.`users`", "prl_app", False, False),
        ("prl_app.*", "prl_app", False, False),
    ],
)
def test_database_grant_semantics(scope, database, partial_revokes, visible):
    assert (
        metadata_visible(database, [ROUTINES, grant(scope)], partial_revokes=partial_revokes)
        is visible
    )


@pytest.mark.parametrize("partial_revokes", [False, True])
@pytest.mark.parametrize("privileges", ["SELECT", "SELECT, TRIGGER", "SELECT, EVENT"])
def test_incomplete_metadata_privileges_are_not_enough(privileges, partial_revokes):
    assert not metadata_visible(
        "prl", [ROUTINES, grant("`prl`.*", privileges)], partial_revokes=partial_revokes
    )


@pytest.mark.parametrize("privileges", ["ALL PRIVILEGES", "SELECT, TRIGGER, EVENT"])
def test_global_static_privileges_also_prove_routine_visibility(privileges):
    assert metadata_visible("prl_app", [grant("*.*", privileges)], partial_revokes=False)


def test_schema_privileges_never_substitute_for_global_routine_visibility():
    assert not metadata_visible("prl_app", [grant(r"`prl\_app`.*")], partial_revokes=False)


@pytest.mark.parametrize(
    "revokes",
    ["REVOKE EVENT ON `prl_app`.* FROM `u`@`%`", "REVOKE SELECT ON `other`.* FROM `u`@`%`"],
)
def test_partial_revokes_remain_fail_closed(revokes):
    assert not metadata_visible(
        "prl_app", [grant("*.*", "ALL PRIVILEGES"), revokes], partial_revokes=True
    )


def test_role_table_and_column_grants_do_not_prove_schema_wide_visibility():
    assert not metadata_visible(
        "prl_app",
        [
            ROUTINES,
            "GRANT `operator`@`%` TO `u`@`%`",
            grant("`prl_app`.`users`"),
            grant("`prl_app`.*", "SELECT (id)"),
        ],
        partial_revokes=False,
    )


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("literal_full", [False, True])
def test_literal_scope_wins_without_combining_it_with_wildcard_permissions(reverse, literal_full):
    grants = [
        grant(r"`prl\_app`.*", "TRIGGER, EVENT" if literal_full else "SELECT"),
        grant("`prl_app`.*", "SELECT" if literal_full else "TRIGGER, EVENT"),
    ]
    assert (
        metadata_visible(
            "prl_app", [ROUTINES, *(reversed(grants) if reverse else grants)], partial_revokes=False
        )
        is literal_full
    )


def test_overlapping_patterns_cannot_supply_split_privileges():
    assert not metadata_visible(
        "prl_app",
        [ROUTINES, grant("`prl%`.*", "TRIGGER"), grant("`prl_%`.*", "EVENT")],
        partial_revokes=False,
    )
    assert metadata_visible(
        "prl_app", [ROUTINES, grant("`prl%`.*"), grant("`prl_%`.*")], partial_revokes=False
    )
    assert metadata_visible(
        "prl_app",
        [ROUTINES, grant("`prl%`.*", "ALL PRIVILEGES"), grant("`prl_%`.*")],
        partial_revokes=False,
    )


@pytest.mark.parametrize(
    "scope,database,expected",
    [
        ("`" + "%a" * 30 + "b`.*", "a" * 63 + "c", False),
        ("`" + "%a" * 30 + "b`.*", "a" * 63 + "b", True),
        ("`" + "%" * 64 + "`.*", "a" * 64, True),
        ("`" + "_" * 64 + "`.*", "a" * 64, True),
        ("`" + "_" * 64 + "`.*", "a" * 63, False),
    ],
)
def test_maximum_length_wildcard_scopes_have_bounded_matching(scope, database, expected):
    assert metadata_visible(database, [ROUTINES, grant(scope)], partial_revokes=False) is expected
