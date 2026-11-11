"""Install the pinned upstream age executables; verify the release asset before extraction."""

import argparse
import errno
import hashlib
import http.client
import io
import os
import platform
import socket
import tarfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from tools.backup.offhost_contract import Code, RemoteError
from tools.backup.offhost_io import Deadline, directory

VERSION = "1.3.2"
ASSETS = {
    "Linux": (
        "linux-amd64.tar.gz",
        "cbe24006683f8eb669266162894b9a522a1af52f2665fbc63a4bb032ed26ac10",
    ),
    "Windows": (
        "windows-amd64.zip",
        "f48d8f8f9ebe903ab5027ed067652f2cc1db94bc206976430133b905dcd8e8c7",
    ),
}


def _download(url: str, deadline: Deadline) -> bytearray:
    for attempt in range(3):
        # An interrupted response is untrusted; each attempt starts from zero.
        data = bytearray()
        try:
            with urllib.request.urlopen(url, timeout=min(20, deadline.remaining())) as response:
                headers = getattr(response, "headers", None)
                content_length = headers.get("Content-Length") if headers is not None else None
                try:
                    expected_length = int(content_length) if content_length is not None else None
                except (TypeError, ValueError):
                    raise RemoteError(Code.TRANSPORT) from None
                if expected_length is not None and expected_length < 0:
                    raise RemoteError(Code.TRANSPORT)
                while True:
                    deadline.remaining()
                    block = response.read(65536)
                    deadline.remaining()
                    if not block:
                        if expected_length is not None and len(data) != expected_length:
                            raise http.client.IncompleteRead(
                                bytes(data), max(0, expected_length - len(data))
                            )
                        return data
                    data.extend(block)
                    if len(data) > 32 * 1024**2:
                        raise RemoteError(Code.INTEGRITY)
        except urllib.error.HTTPError as error:
            error.close()
            if error.code not in {408, 429} and not 500 <= error.code <= 599:
                raise RemoteError(Code.TRANSPORT) from None
        except (urllib.error.URLError, OSError, http.client.IncompleteRead) as error:
            reason = error.reason if isinstance(error, urllib.error.URLError) else error
            transient = isinstance(
                reason, (TimeoutError, ConnectionError, http.client.IncompleteRead)
            )
            if isinstance(reason, socket.gaierror):
                transient = reason.errno == socket.EAI_AGAIN
            elif isinstance(reason, OSError):
                transient |= reason.errno in {errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ENETDOWN}
            if not transient:
                raise RemoteError(Code.TRANSPORT) from None
        deadline.remaining()
        if attempt < 2:
            time.sleep(min(2**attempt, deadline.remaining()))
    raise RemoteError(Code.TRANSPORT)


def install(destination: Path) -> Path:
    system = platform.system()
    if system not in ASSETS or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise RemoteError(Code.CONFIGURATION)
    suffix, expected = ASSETS[system]
    url = f"https://github.com/FiloSottile/age/releases/download/v{VERSION}/age-v{VERSION}-{suffix}"
    data = _download(url, Deadline(120))
    if hashlib.sha256(data).hexdigest() != expected:
        raise RemoteError(Code.INTEGRITY)
    directory(destination)
    extension = ".exe" if system == "Windows" else ""
    for name in ("age", "age-keygen"):
        member = f"age/{name}{extension}"
        if system == "Windows":
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                payload = archive.read(member)
        else:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
                entry = archive.getmember(member)
                if not entry.isfile() or entry.size > 32 * 1024**2:
                    raise RemoteError(Code.INTEGRITY)
                source = archive.extractfile(entry)
                if source is None:
                    raise RemoteError(Code.INTEGRITY)
                with source:
                    payload = source.read(32 * 1024**2 + 1)
        target = destination / (name + extension)
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700)
        with os.fdopen(fd, "wb") as output:
            output.write(payload)
    return destination / ("age" + extension)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    install(args.directory.resolve())
    print('{"operation":"install-age","success":true,"version":"1.3.2"}')
