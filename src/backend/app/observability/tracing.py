"""Safe, explicitly injected spans for business and transaction boundaries."""

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Protocol, cast

from app.observability.records import SpanName


class SpanLike(Protocol):
    def set_attribute(self, key: str, value: object) -> None: ...


SpanFactory = Callable[[SpanName], AbstractContextManager[object]]
BUSINESS_OUTCOMES = frozenset(
    {"committed", "replayed", "noop", "staged", "failed", "commit_unknown"}
)


@contextmanager
def safe_span(span_factory: SpanFactory | None, name: SpanName) -> Iterator[object | None]:
    """Contain instrumentation failures and always preserve the business exception."""
    manager: AbstractContextManager[object] | None = None
    span: object | None = None
    if span_factory is not None:
        try:
            manager = span_factory(name)
            span = manager.__enter__()
        except BaseException:
            manager = None
            span = None
    try:
        yield span
    except BaseException as business_error:
        if manager is not None:
            try:
                manager.__exit__(type(business_error), business_error, business_error.__traceback__)
            except BaseException:
                pass
        raise
    else:
        if manager is not None:
            try:
                manager.__exit__(None, None, None)
            except BaseException:
                pass


def set_business_outcome(span: object | None, outcome: str) -> None:
    """Set only the finite outcome field, suppressing failures from an owner span."""
    if span is None or outcome not in BUSINESS_OUTCOMES:
        return
    try:
        cast(SpanLike, span).set_attribute("business.outcome", outcome)
    except BaseException:
        pass
