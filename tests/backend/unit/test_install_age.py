"""Exercise the installer against interrupted, rejected, and untrusted downloads."""

import errno
import hashlib
import http.client
import io
import socket
import ssl
import tarfile
import time
import urllib.error
import zipfile
from email.message import Message

import pytest
from tools.backup import install_age
from tools.backup.offhost_contract import Code, RemoteError


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    value = Clock()
    monkeypatch.setattr(time, "monotonic", lambda: value.now)
    monkeypatch.setattr(time, "sleep", value.sleep)
    return value


@pytest.fixture
def release(monkeypatch):
    def configure(system="Linux"):
        extension = ".exe" if system == "Windows" else ""
        payloads = {"age" + extension: b"age executable", "age-keygen" + extension: b"keygen"}
        buffer = io.BytesIO()
        if system == "Windows":
            with zipfile.ZipFile(buffer, "w") as archive:
                for name, payload in payloads.items():
                    archive.writestr("age/" + name, payload)
        else:
            with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
                for name, payload in payloads.items():
                    member = tarfile.TarInfo("age/" + name)
                    member.size = len(payload)
                    archive.addfile(member, io.BytesIO(payload))
        data = buffer.getvalue()
        suffix = "windows-amd64.zip" if system == "Windows" else "linux-amd64.tar.gz"
        monkeypatch.setattr(install_age.platform, "system", lambda: system)
        monkeypatch.setattr(install_age.platform, "machine", lambda: "x86_64")
        monkeypatch.setitem(install_age.ASSETS, system, (suffix, hashlib.sha256(data).hexdigest()))
        return data, payloads

    return configure


def downloads(monkeypatch, outcomes):
    requests = []
    pending = iter(outcomes)

    def open_url(url, *, timeout):
        requests.append((url, timeout))
        outcome = next(pending)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(install_age.urllib.request, "urlopen", open_url)
    return requests


@pytest.mark.parametrize("system", ["Linux", "Windows"])
def test_install_retries_redirect_timeout_then_extracts_verified_release(
    tmp_path, monkeypatch, release, clock, system
):
    data, payloads = release(system)
    requests = downloads(monkeypatch, [TimeoutError("redirect read timed out"), io.BytesIO(data)])
    destination = tmp_path / "bin"

    executable = install_age.install(destination)

    assert executable == destination / ("age.exe" if system == "Windows" else "age")
    assert {path.name: path.read_bytes() for path in destination.iterdir()} == payloads
    suffix = "windows-amd64.zip" if system == "Windows" else "linux-amd64.tar.gz"
    assert [url for url, _ in requests] == [
        "https://github.com/FiloSottile/age/releases/download/v1.3.2/age-v1.3.2-" + suffix
    ] * 2
    assert clock.sleeps == [1]


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504, 599])
def test_install_retries_temporary_http_failure(tmp_path, monkeypatch, release, clock, status):
    data, _ = release()
    failure = urllib.error.HTTPError("https://example.test", status, "temporary", Message(), None)
    downloads(monkeypatch, [failure, io.BytesIO(data)])

    assert install_age.install(tmp_path / "bin").read_bytes() == b"age executable"


@pytest.mark.parametrize("status", [400, 401, 403, 404, 410])
def test_install_does_not_retry_permanent_http_failure(
    tmp_path, monkeypatch, release, clock, status
):
    data, _ = release()
    failure = urllib.error.HTTPError("https://example.test", status, "permanent", Message(), None)
    requests = downloads(monkeypatch, [failure, io.BytesIO(data)])
    destination = tmp_path / "bin"

    with pytest.raises(RemoteError) as caught:
        install_age.install(destination)

    assert caught.value.code == Code.TRANSPORT
    assert len(requests) == 1
    assert clock.sleeps == []
    assert not destination.exists()


def test_install_discards_partial_response_before_retry(tmp_path, monkeypatch, release, clock):
    data, payloads = release()

    class Interrupted(io.BytesIO):
        def read(self, size: int | None = -1) -> bytes:
            if self.tell():
                raise ConnectionResetError("connection reset during body read")
            return super().read(size)

    partial = Interrupted(b"untrusted partial body")
    downloads(monkeypatch, [partial, io.BytesIO(data)])
    destination = tmp_path / "bin"

    install_age.install(destination)

    assert partial.closed
    assert {path.name: path.read_bytes() for path in destination.iterdir()} == payloads


@pytest.mark.parametrize(
    "failure",
    [
        urllib.error.URLError(ConnectionResetError("reset")),
        urllib.error.URLError(socket.gaierror(socket.EAI_AGAIN, "temporary DNS failure")),
        OSError(errno.ENETUNREACH, "network unreachable"),
        http.client.IncompleteRead(b"partial", 100),
    ],
)
def test_install_retries_transient_network_failure(tmp_path, monkeypatch, release, clock, failure):
    data, _ = release()
    downloads(monkeypatch, [failure, io.BytesIO(data)])

    assert install_age.install(tmp_path / "bin").read_bytes() == b"age executable"


@pytest.mark.parametrize(
    "failure",
    [
        urllib.error.URLError(ssl.SSLCertVerificationError("untrusted certificate")),
        urllib.error.URLError(socket.gaierror(socket.EAI_NONAME, "unknown host")),
        urllib.error.URLError("invalid URL"),
    ],
)
def test_install_does_not_retry_permanent_network_failure(
    tmp_path, monkeypatch, release, clock, failure
):
    data, _ = release()
    requests = downloads(monkeypatch, [failure, io.BytesIO(data)])
    destination = tmp_path / "bin"

    with pytest.raises(RemoteError) as caught:
        install_age.install(destination)

    assert caught.value.code == Code.TRANSPORT
    assert len(requests) == 1
    assert clock.sleeps == []
    assert not destination.exists()


def test_install_stops_after_three_failed_attempts(tmp_path, monkeypatch, release, clock):
    release()
    requests = downloads(
        monkeypatch,
        [urllib.error.URLError(TimeoutError("timed out")) for _ in range(4)],
    )
    destination = tmp_path / "bin"

    with pytest.raises(RemoteError) as caught:
        install_age.install(destination)

    assert caught.value.code == Code.TRANSPORT
    assert len(requests) == 3
    assert clock.sleeps == [1, 2]
    assert not destination.exists()


def test_install_never_retries_digest_mismatch(tmp_path, monkeypatch, release, clock):
    data, _ = release()
    requests = downloads(monkeypatch, [io.BytesIO(data + b"tampered"), io.BytesIO(data)])
    destination = tmp_path / "bin"

    with pytest.raises(RemoteError) as caught:
        install_age.install(destination)

    assert caught.value.code == Code.INTEGRITY
    assert len(requests) == 1
    assert clock.sleeps == []
    assert not destination.exists()


def test_install_never_retries_oversized_download(tmp_path, monkeypatch, release, clock):
    data, _ = release()
    oversized = io.BytesIO(b"x" * (32 * 1024**2 + 1))
    requests = downloads(monkeypatch, [oversized, io.BytesIO(data)])
    destination = tmp_path / "bin"

    with pytest.raises(RemoteError) as caught:
        install_age.install(destination)

    assert caught.value.code == Code.INTEGRITY
    assert len(requests) == 1
    assert oversized.closed
    assert not destination.exists()


def test_install_shares_deadline_between_attempts_and_bounds_backoff(
    tmp_path, monkeypatch, release, clock
):
    release()
    requests = []

    def open_url(url, *, timeout):
        requests.append(timeout)
        clock.now += 118 if len(requests) == 1 else 0.5
        raise TimeoutError("download timed out")

    monkeypatch.setattr(install_age.urllib.request, "urlopen", open_url)
    destination = tmp_path / "bin"

    with pytest.raises(RemoteError) as caught:
        install_age.install(destination)

    assert caught.value.code == Code.TIMEOUT
    assert requests == [20, 1]
    assert clock.sleeps == [1, 0.5]
    assert clock.now == 120
    assert not destination.exists()


def test_install_checks_deadline_after_empty_final_read(tmp_path, monkeypatch, release, clock):
    data, _ = release()

    class SlowEnd(io.BytesIO):
        def read(self, size: int | None = -1) -> bytes:
            block = super().read(size)
            if not block:
                clock.now = 121
            return block

    response = SlowEnd(data)
    requests = downloads(monkeypatch, [response])
    destination = tmp_path / "bin"

    with pytest.raises(RemoteError) as caught:
        install_age.install(destination)

    assert caught.value.code == Code.TIMEOUT
    assert len(requests) == 1
    assert response.closed
    assert not destination.exists()
