import pytest

from app.api.errors import AppError


def test_throttle_limits_expire_and_state_is_bounded(auth_settings):
    from app.core.login_throttle import LoginThrottle

    now = [0.0]
    limiter = LoginThrottle(auth_settings, clock=lambda: now[0])
    for _ in range(30):
        limiter.check_ip("127.0.0.1")
    with pytest.raises(AppError) as caught:
        limiter.check_ip("127.0.0.1")
    assert caught.value.headers is not None
    assert caught.value.headers["Retry-After"] == "60"
    for _ in range(5):
        limiter.check_email("a@example.invalid")
    with pytest.raises(AppError):
        limiter.check_email(" A@Example.invalid ")
    now[0] = 60.0
    limiter.check_ip("127.0.0.1")
    limiter.check_email("a@example.invalid")
    small = LoginThrottle(
        auth_settings.model_copy(update={"login_state_max_entries": 2}), clock=lambda: now[0]
    )
    small.check_ip("1")
    small.check_ip("2")
    with pytest.raises(AppError):
        small.check_ip("3")
    now[0] = 120.0
    small.check_ip("3")
