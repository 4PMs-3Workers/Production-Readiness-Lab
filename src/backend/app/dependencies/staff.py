"""Authentication and raw-query guards for staff appointment routes."""

from collections import Counter
from typing import Annotated

from fastapi import Depends, Request

from app.api.catalog_errors import catalog_error
from app.dependencies.auth import require_roles
from app.domain.auth import AuthContext

require_staff = require_roles("STAFF")


async def reject_duplicate_staff_query(
    request: Request,
    context: Annotated[AuthContext, Depends(require_staff)],
) -> None:
    """Reject repeated raw query keys before FastAPI collapses values."""
    del context
    keys = [key for key, _ in request.query_params.multi_items()]
    if any(count > 1 for count in Counter(keys).values()):
        raise catalog_error("VALIDATION_ERROR")


async def reject_staff_write_query(
    request: Request,
    context: Annotated[AuthContext, Depends(require_staff)],
) -> None:
    """Staff writes do not define query parameters."""
    del context
    if request.query_params:
        raise catalog_error("VALIDATION_ERROR")
