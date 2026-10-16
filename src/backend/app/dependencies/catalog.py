"""Shared authentication and raw-query guards for student catalog routes."""

from collections import Counter
from typing import Annotated

from fastapi import Depends, Request

from app.api.catalog_errors import catalog_error
from app.dependencies.auth import require_roles
from app.domain.auth import AuthContext

require_student = require_roles("STUDENT")


async def reject_duplicate_catalog_query(
    request: Request,
    context: Annotated[AuthContext, Depends(require_student)],
) -> None:
    """Reject repeated raw query keys before FastAPI collapses their values."""
    del context
    keys = [key for key, _ in request.query_params.multi_items()]
    if any(count > 1 for count in Counter(keys).values()):
        raise catalog_error("VALIDATION_ERROR")
