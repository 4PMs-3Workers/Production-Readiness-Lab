"""Interpret direct SHOW GRANTS scopes without widening metadata privileges."""

import re
from collections.abc import Sequence

_GRANT = re.compile(r"^GRANT ([A-Z_, ]+) ON (\*\.\*|`(?:[^`]|``)+`\.\*) TO ")


def _schema_match(scope: str, database: str, partial_revokes: bool) -> tuple[bool, bool]:
    # SHOW GRANTS quotes identifiers; doubled backticks quote a literal backtick.
    name = scope[1:-3].replace("``", "`")
    if partial_revokes:
        # With partial_revokes enabled, schema names are literal, including '\'.
        return name == database, True
    positions, literal, index = {0}, True, 0
    while index < len(name):
        char = name[index]
        if char == "\\" and index + 1 < len(name):
            index += 1
            char = name[index]
        elif char == "_":
            literal = False
            positions = {position + 1 for position in positions if position < len(database)}
            index += 1
            continue
        elif char == "%":
            literal = False
            positions = set(range(min(positions), len(database) + 1)) if positions else set()
            index += 1
            continue
        positions = {
            position + 1
            for position in positions
            if position < len(database) and database[position] == char
        }
        index += 1
    # At most len(name) * (len(database) + 1) transitions: repeated SQL wildcards
    # must not become a backtracking regex inside the operation's event loop.
    return len(database) in positions, literal


def metadata_visible(database: str, grants: Sequence[str], *, partial_revokes: bool) -> bool:
    """Prove schema-wide TRIGGER/EVENT and global routine visibility.

    Literal schema grants take precedence over wildcard schema grants in MySQL.
    Different matching patterns must never be unioned: MySQL selects one database
    grant, rather than accumulating their privileges. For overlapping patterns we
    accept only privileges present in every candidate, independent of row ordering.
    Actual partial revokes and role-only permissions remain unsupported.
    """
    global_privileges: set[str] = set()
    schemas: dict[str, set[str]] = {}
    for grant in grants:
        if grant.startswith("REVOKE "):
            return False
        match = _GRANT.match(grant)
        if match is None:
            continue
        privileges = {value.strip() for value in match[1].split(",")}
        if "ALL PRIVILEGES" in privileges:
            privileges.update({"TRIGGER", "EVENT"})
        if match[2] == "*.*":
            global_privileges.update(privileges)
        else:
            schemas.setdefault(match[2], set()).update(privileges)
    exact, wildcard = [], []
    for scope, privileges in schemas.items():
        matches, literal = _schema_match(scope, database, partial_revokes)
        if matches:
            (exact if literal else wildcard).append(privileges)
    candidates = exact or wildcard
    schema_privileges = set.intersection(*candidates) if candidates else set()
    visible = global_privileges | schema_privileges
    return ("ALL PRIVILEGES" in visible or {"TRIGGER", "EVENT"} <= visible) and bool(
        {"ALL PRIVILEGES", "SELECT", "SHOW_ROUTINE"} & global_privileges
    )
