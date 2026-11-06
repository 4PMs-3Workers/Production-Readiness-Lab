"""Install the pinned upstream age executables; verify the release asset before extraction."""

import argparse
import hashlib
import io
import os
import platform
import tarfile
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


def install(destination: Path) -> Path:
    system = platform.system()
    if system not in ASSETS or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise RemoteError(Code.CONFIGURATION)
    suffix, expected = ASSETS[system]
    url = f"https://github.com/FiloSottile/age/releases/download/v{VERSION}/age-v{VERSION}-{suffix}"
    deadline, data = Deadline(120), bytearray()
    with urllib.request.urlopen(url, timeout=20) as response:
        while block := response.read(65536):
            deadline.remaining()
            data.extend(block)
            if len(data) > 32 * 1024**2:
                raise RemoteError(Code.INTEGRITY)
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
