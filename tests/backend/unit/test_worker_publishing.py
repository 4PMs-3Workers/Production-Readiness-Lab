import asyncio
import threading

import pytest

from app.worker.publishing import MAX_RECORD_BYTES, Publisher


async def wait_until(predicate):
    async with asyncio.timeout(1):
        while not predicate():
            await asyncio.sleep(0.001)


async def test_publisher_preserves_order_and_flushes_healthy_output():
    output = []
    publisher = Publisher(output.append)
    assert publisher.submit("first") and publisher.submit("second")
    assert await publisher.close(asyncio.get_running_loop().time() + 1)
    assert output == ["first", "second"] and publisher.failures == 0
    assert not publisher._thread.is_alive()


async def test_blocked_sink_bounds_queue_drops_newest_and_does_not_block_loop_or_cleanup():
    entered, release = threading.Event(), threading.Event()
    output = []

    def blocked(value):
        entered.set()
        release.wait()
        output.append(value)

    publisher = Publisher(blocked, capacity=2, timeout=0.03)
    try:
        assert publisher.submit("active")
        await wait_until(entered.is_set)
        assert publisher.submit("queued-1") and publisher.submit("queued-2")
        assert publisher.submit("drop-newest") is False
        assert publisher.failures == 1
        await asyncio.sleep(0.04)
        assert publisher.failures == 4  # one active timeout, two expired queued records
        started = asyncio.get_running_loop().time()
        assert not await publisher.close(started + 0.03)
        assert asyncio.get_running_loop().time() - started < 0.1
        assert publisher._thread.daemon and publisher._thread.is_alive()
        assert not publisher.submit("after-close")
    finally:
        release.set()
        await wait_until(lambda: not publisher._thread.is_alive())
    assert output == ["active"]  # expired/discarded records are never replayed


async def test_sink_errors_are_counted_without_publishing_exception_payloads(capsys):
    def broken(value):
        raise OSError("private-token-sql-and-consultation")

    publisher = Publisher(broken)
    publisher.submit("safe-json")
    assert await publisher.close(asyncio.get_running_loop().time() + 1)
    assert publisher.failures == 1
    captured = capsys.readouterr()
    assert captured.err == captured.out == ""


async def test_publisher_bounds_record_bytes_and_rejects_after_cleanup():
    publisher = Publisher(lambda value: None)
    assert not publisher.submit("x" * (MAX_RECORD_BYTES + 1))
    assert not publisher.submit("\u00e9" * MAX_RECORD_BYTES)
    assert await publisher.close(asyncio.get_running_loop().time() + 1)
    assert not publisher.submit("closed")
    assert publisher.failures == 3


@pytest.mark.parametrize("capacity,timeout", [(0, 1), (1, 0), (1, float("inf"))])
def test_publisher_rejects_unbounded_configuration(capacity, timeout):
    with pytest.raises(ValueError):
        Publisher(capacity=capacity, timeout=timeout)
