"""Private bounded file/process primitives; raw tool output never crosses the CLI."""

import hashlib
import os
import stat
import subprocess
import time
from pathlib import Path

from tools.backup.offhost_contract import Code, RemoteError


class Deadline:
    def __init__(self, seconds: float):
        self.expires = time.monotonic() + seconds

    def remaining(self) -> float:
        left = self.expires - time.monotonic()
        if left <= 0:
            raise RemoteError(Code.TIMEOUT)
        return left


def directory(path: Path) -> Path:
    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        raise RemoteError(Code.CONFIGURATION)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if not path.is_dir() or (
        os.name != "nt" and (info.st_mode & 0o077 or info.st_uid != os.geteuid())
    ):
        raise RemoteError(Code.CONFIGURATION)
    return path


def regular(path: Path, maximum: int) -> int:
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_size > maximum
        or path.is_symlink()
        or (os.name != "nt" and info.st_mode & 0o077)
    ):
        raise RemoteError(Code.INTEGRITY)
    return info.st_size


def digest(path: Path, maximum: int, deadline: Deadline) -> tuple[str, int]:
    size = regular(path, maximum)
    result, count = hashlib.sha256(), 0
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as source:
        while block := source.read(1024 * 1024):
            deadline.remaining()
            count += len(block)
            if count > size:
                raise RemoteError(Code.INTEGRITY)
            result.update(block)
    if count != size or regular(path, maximum) != size:
        raise RemoteError(Code.INTEGRITY)
    return result.hexdigest(), count


def run(args: list[str], deadline: Deadline, *, stdout=None) -> None:
    try:
        result = subprocess.run(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL if stdout is None else stdout,
            stderr=subprocess.DEVNULL,
            timeout=deadline.remaining(),
            check=False,
        )
        if result.returncode:
            raise RemoteError(Code.INTEGRITY)
    except subprocess.TimeoutExpired:
        raise RemoteError(Code.TIMEOUT) from None
    except OSError:
        raise RemoteError(Code.CONFIGURATION) from None


def age(
    executable: Path,
    source: Path,
    target: Path,
    deadline: Deadline,
    *,
    recipient: str | None = None,
    identity: Path | None = None,
) -> None:
    if not executable.is_absolute() or not executable.is_file() or executable.is_symlink():
        raise RemoteError(Code.KEY)
    if (recipient is None) == (identity is None):
        raise RemoteError(Code.CONFIGURATION)
    from tools.backup.offhost_contract import private_file

    if identity is not None:
        # Only native, noninteractive X25519 identities are supported.
        raw = private_file(identity, 4096)
        keys = [
            line for line in raw.decode("ascii").splitlines() if line and not line.startswith("#")
        ]
        if len(keys) != 1 or not keys[0].startswith("AGE-SECRET-KEY-1"):
            raise RemoteError(Code.KEY)
    command = [str(executable)]
    command += (
        ["--encrypt", "--recipient", recipient]
        if recipient
        else ["--decrypt", "--identity", str(identity)]
    )
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as output:
            run([*command, str(source)], deadline, stdout=output)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        target.unlink(missing_ok=True)
        raise
