import re
from pathlib import Path

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.dialects.mysql import dialect
from sqlalchemy.orm import configure_mappers
from sqlalchemy.schema import CreateIndex, CreateTable

from app.models import Base


def test_every_documented_table_is_registered():
    design = Path("docs/architecture/database-design.md").read_text(encoding="utf-8")
    tables = set(re.findall(r"^### 3\.\d+\. `([^`]+)`", design, flags=re.MULTILINE))
    assert len(tables) == 11
    assert set(Base.metadata.tables) == tables
    configure_mappers()


def test_columns_types_and_nullability_match_the_database_dictionary():
    design = Path("docs/architecture/database-design.md").read_text(encoding="utf-8")
    documented: dict[str, dict[str, tuple[str, bool]]] = {}
    table_name = None
    for line in design.splitlines():
        heading = re.match(r"^### 3\.\d+\. `([^`]+)`", line)
        if heading:
            table_name = heading.group(1)
            documented[table_name] = {}
            continue
        if line.startswith("## 4."):
            break
        if table_name is None or not line.startswith("| `"):
            continue
        cells = [cell.strip() for cell in line.split("|")[1:-1]]
        if len(cells) != 4:
            continue
        column_names = re.findall(r"`([a-z_]+)`", cells[0])
        sql_type = re.search(r"`([^`]+)`", cells[1])
        if not column_names or sql_type is None:
            continue
        nullable = re.search(r"\b(NN|PK)\b", cells[2]) is None
        for name in column_names:
            documented[table_name][name] = (sql_type.group(1).lower(), nullable)

    assert len(documented) == 11
    for name, columns in documented.items():
        table = Base.metadata.tables[name]
        assert set(table.columns.keys()) == set(columns), name
        for column_name, (expected_type, nullable) in columns.items():
            column = table.c[column_name]
            actual_type = column.type.compile(dialect=dialect()).lower().split(" collate ", 1)[0]
            if actual_type == "bool":
                actual_type = "boolean"
            elif actual_type == "time":
                actual_type = "time(0)"
            assert actual_type == expected_type, f"{name}.{column_name}"
            assert column.nullable is nullable, f"{name}.{column_name}"


def test_code_and_email_collations_match_the_database_design():
    code_columns = {
        "role",
        "status",
        "code",
        "event_type",
        "action",
        "entity_type",
        "entity_id",
        "request_id",
        "creation_request_key",
    }
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if column.name == "email":
                assert isinstance(column.type, String)
                assert column.type.collation == "utf8mb4_0900_as_ci"
            elif column.name in code_columns:
                assert isinstance(column.type, String)
                assert column.type.collation == "utf8mb4_0900_bin"


def test_all_ddl_compiles_for_mysql_and_all_foreign_keys_resolve():
    for table in Base.metadata.sorted_tables:
        ddl = str(CreateTable(table).compile(dialect=dialect()))
        assert "ENGINE=InnoDB" in ddl
        assert "CHARSET=utf8mb4" in ddl
        assert "COLLATE utf8mb4_0900_ai_ci" in ddl
        for foreign_key in table.foreign_keys:
            assert foreign_key.column.type.compile(dialect=dialect()) == "BINARY(16)"
            assert foreign_key.ondelete == "RESTRICT"
            assert foreign_key.onupdate == "RESTRICT"
        for constraint in table.constraints:
            name = constraint.name
            assert isinstance(name, str) and name
            assert len(name) <= 64
        for index in table.indexes:
            assert str(CreateIndex(index).compile(dialect=dialect()))


def test_appointment_links_the_exact_staff_purpose_pair():
    constraints = Base.metadata.tables["appointments"].constraints
    pair = next(
        constraint
        for constraint in constraints
        if isinstance(constraint, ForeignKeyConstraint)
        and constraint.name == "fk_appointments_staff_purpose"
    )
    assert list(pair.column_keys) == ["staff_id", "purpose_id"]
    assert [fk.target_fullname for fk in pair.elements] == [
        "staff_purposes.staff_id",
        "staff_purposes.purpose_id",
    ]


def test_pending_guard_is_virtual_and_part_of_unique_constraint():
    table = Base.metadata.tables["change_proposals"]
    computed = table.c.pending_guard.computed
    assert computed is not None
    assert computed.persisted is False
    unique = next(
        constraint
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
        and constraint.name == "uq_change_proposals_pending"
    )
    assert list(unique.columns.keys()) == ["appointment_id", "pending_guard"]
    assert "VIRTUAL" in str(CreateTable(table).compile(dialect=dialect()))


def test_sensitive_actor_checks_stay_in_the_service_layer():
    # MySQL disallows CHECK expressions on columns with FK referential actions.
    for table_name, actors in [
        ("appointments", ["cancelled_by", "outcome_by"]),
        ("change_proposals", ["resolved_by"]),
    ]:
        for constraint in Base.metadata.tables[table_name].constraints:
            if isinstance(constraint, CheckConstraint):
                assert not any(actor in str(constraint.sqltext) for actor in actors)
