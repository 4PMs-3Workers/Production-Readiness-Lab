from uuid import UUID

import pytest
from tools.scripts.vt070_mysql_http_load import notice_oracle

APPOINTMENT, STUDENT, OTHER = (UUID(int=value) for value in (1, 2, 3))
NOTICE = (APPOINTMENT, STUDENT)
SUCCESS = (*NOTICE, True)
FAILURE = (*NOTICE, False)
WRONG_RECIPIENT = (APPOINTMENT, OTHER)


@pytest.mark.parametrize(
    ("attempts", "notices", "expected"),
    [
        pytest.param([SUCCESS], [NOTICE], True, id="delivered"),
        pytest.param([FAILURE], [], True, id="recorded-failure"),
        pytest.param([FAILURE], [NOTICE], True, id="uncertain-commit"),
        pytest.param([(*NOTICE, None)], [], False, id="incomplete-attempt"),
        pytest.param([], [], False, id="missing-attempt"),
        pytest.param([], [NOTICE], False, id="untracked-write"),
        pytest.param([SUCCESS], [], False, id="missing-successful-write"),
        pytest.param([SUCCESS], [WRONG_RECIPIENT], False, id="wrong-recipient"),
        pytest.param([FAILURE], [(OTHER, STUDENT)], False, id="wrong-appointment"),
        pytest.param([(*WRONG_RECIPIENT, False)], [], False, id="wrong-recipient-attempt"),
        pytest.param([SUCCESS], [NOTICE, NOTICE], False, id="duplicate-write"),
        pytest.param([SUCCESS, SUCCESS], [NOTICE], False, id="duplicate-attempt"),
    ],
)
def test_candidate_notice_oracle(attempts, notices, expected):
    assert notice_oracle({APPOINTMENT: STUDENT}, attempts, notices) is expected


def test_notice_oracle_when_no_writes_are_expected():
    assert notice_oracle({}, [], [])
    assert not notice_oracle({}, [FAILURE], [])
    assert not notice_oracle({}, [], [NOTICE])
