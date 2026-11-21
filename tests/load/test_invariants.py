import sqlite3

from tools.load.invariants import _PENDING_PROPOSAL_VERSION_MISMATCH_SQL


def test_pending_proposal_must_match_current_appointment_version():
    connection = sqlite3.connect(":memory:")
    connection.executescript("""
        CREATE TABLE appointments (id INTEGER PRIMARY KEY, proposal_version INTEGER NOT NULL);
        CREATE TABLE change_proposals (
            appointment_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            created_version INTEGER
        );
        INSERT INTO appointments VALUES (1, 2), (2, 2), (3, 2);
        INSERT INTO change_proposals VALUES
            (1, 'PENDING', 1),
            (2, 'PENDING', 2),
            (3, 'SUPERSEDED', 1);
        """)

    mismatches = connection.execute(_PENDING_PROPOSAL_VERSION_MISMATCH_SQL).fetchone()[0]

    assert mismatches == 1
    connection.close()
