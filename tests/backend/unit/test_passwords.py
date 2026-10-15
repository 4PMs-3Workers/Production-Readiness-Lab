import asyncio
import threading


async def test_password_round_trip_preserves_spaces():
    from app.core.passwords import PasswordPolicy, PasswordService

    service = await PasswordService.create(PasswordPolicy(), 2)
    encoded = await service.hash("  AbC!  ")
    assert encoded.startswith("$argon2id$")
    assert await service.verify("  AbC!  ", encoded)
    assert not await service.verify("AbC!", encoded)
    assert not await service.verify("anything", "malformed-hash")
    assert not service.needs_rehash(encoded)
    old = await PasswordService.create(PasswordPolicy(time_cost=2), 1)
    assert service.needs_rehash(await old.hash("a"))
    assert encoded != await service.hash("  AbC!  ")
    await service.verify_dummy("a")


async def test_password_workers_are_offloaded_and_bounded(monkeypatch):
    from app.core.passwords import PasswordPolicy, PasswordService

    service = await PasswordService.create(PasswordPolicy(), 1)
    started, release = threading.Event(), threading.Event()
    active, maximum = 0, 0
    lock = threading.Lock()

    def slow_hash(self, password):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        started.set()
        release.wait(5)
        with lock:
            active -= 1
        return "test-hash"

    monkeypatch.setattr("argon2.PasswordHasher.hash", slow_hash)
    tasks = [asyncio.create_task(service.hash("a")) for _ in range(3)]
    try:
        async with asyncio.timeout(2):
            while not started.is_set():
                await asyncio.sleep(0.001)
        assert active == 1
    finally:
        release.set()
        await asyncio.gather(*tasks)
    assert maximum == 1
