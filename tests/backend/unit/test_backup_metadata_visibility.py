from unittest.mock import AsyncMock, Mock

import pytest

from app.backup.config import BackupError, Reason
from app.backup.schema import require_metadata_visibility


@pytest.mark.parametrize(
    "mode,scope,database,accepted",
    [
        (0, r"`prl\_app`.*", "prl_app", True),
        (1, r"`prl\_app`.*", "prl_app", False),
        (0, "`prl_app`.*", "prlXapp", True),
        (1, "`prl_app`.*", "prlXapp", False),
        (1, "`prl_app`.*", "prl_app", True),
        (None, r"`prl\_app`.*", "prl_app", False),
    ],
)
async def test_preflight_uses_the_connected_servers_grant_mode(mode, scope, database, accepted):
    current = Mock()
    current.one.return_value = (database, mode)
    permissions = Mock()
    permissions.scalars.return_value.all.return_value = [
        "GRANT BACKUP_ADMIN,SHOW_ROUTINE ON *.* TO `u`@`%`",
        f"GRANT SELECT, TRIGGER, EVENT ON {scope} TO `u`@`%`",
    ]
    connection = AsyncMock()
    connection.execute.side_effect = [current, permissions]
    if accepted:
        await require_metadata_visibility(connection)
    else:
        with pytest.raises(BackupError) as error:
            await require_metadata_visibility(connection)
        assert error.value.reason == Reason.UNSUPPORTED
