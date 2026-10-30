"""Nonblocking, crash-released local-store lease shared by manual/scheduled operations."""

import errno
import os
import stat
from contextlib import contextmanager

from app.backup.config import BackupConfig, BackupError, Reason
from app.backup.storage import store_root

if os.name == "nt":
    import msvcrt
else:
    import fcntl


@contextmanager
def operation_lock(config: BackupConfig):
    root = store_root(config, create=True)
    try:
        descriptor = os.open(
            root / ".operation.lock", os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600
        )
    except OSError:
        raise BackupError(Reason.STORAGE) from None
    acquired = False
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or (os.name == "posix" and info.st_mode & 0o077):
            raise BackupError(Reason.STORAGE)
        if info.st_size == 0:
            os.write(descriptor, b"0")
        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            if os.name == "nt":
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError as error:
            if error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise BackupError(Reason.BUSY) from None
            raise BackupError(Reason.STORAGE) from None
        yield root
    finally:
        try:
            if acquired:
                os.lseek(descriptor, 0, os.SEEK_SET)
                if os.name == "nt":
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
