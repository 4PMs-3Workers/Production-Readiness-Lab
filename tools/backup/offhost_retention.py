"""Conservative scoped retention; verify all candidates before deleting exact objects."""

import re
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tools.backup.offhost_contract import Code, Completion, RemoteError, payload_key, point_key
from tools.backup.offhost_io import directory
from tools.backup.offhost_s3 import S3
from tools.backup.offhost_upload import read_point, verify_remote

from app.backup.manifest import BACKUP_ID, timestamp


def inventory(remote: S3) -> tuple[list[tuple[Completion, str]], int]:
    result, ignored = [], 0
    prefix = remote.config.scope + "points/"
    for key in remote.inventory():
        relative = key.removeprefix(prefix)
        parts = relative.split("/")
        if len(parts) == 2 and parts[1] == "complete.json" and BACKUP_ID.fullmatch(parts[0]):
            point = read_point(remote, parts[0])
            if point is None:
                raise RemoteError(Code.CONFLICT)
            result.append(point)
        elif not re.fullmatch(
            r"prl-backup-\d{8}T\d{6}Z-[0-9a-f]{32}/blobs/[0-9a-f]{64}\.age", relative
        ):
            ignored += 1
    return result, ignored


def retain(remote: S3, scratch: Path, *, now: datetime | None = None) -> dict:
    started = datetime.now(UTC) if now is None else now
    if started.tzinfo != UTC or started > datetime.now(UTC):
        raise RemoteError(Code.CONFIGURATION)
    points, ignored = inventory(remote)
    points.sort(
        key=lambda item: (
            item[0].snapshot.established_after,
            item[0].backup_completed_at,
            item[0].backup_id,
        ),
        reverse=True,
    )
    if not points:
        return dict(removed_count=0, preserved_count=0, ignored_entries=ignored)
    cutoff = started - timedelta(days=remote.config.max_age_days)
    directory(scratch)
    with tempfile.TemporaryDirectory(prefix="retention-", dir=scratch) as temporary:
        work = Path(temporary)
        # A corrupt/unknown completion or payload aborts the entire plan before deletion.
        payload_etags = {point.backup_id: verify_remote(remote, point, work) for point, _ in points}
        guard, guard_etag = points[0]
        candidates = [
            (point, etag)
            for index, (point, etag) in enumerate(points)
            if index > 0
            and timestamp(point.verified_at) <= started
            and (index >= remote.config.keep_count or timestamp(point.backup_completed_at) < cutoff)
        ]
        removed = 0
        for point, etag in candidates:
            remote.deadline.remaining()
            current_guard = read_point(remote, guard.backup_id)
            current = read_point(remote, point.backup_id)
            if current_guard != (guard, guard_etag) or current != (point, etag):
                raise RemoteError(Code.CONFLICT)
            verify_remote(remote, guard, work)
            # Withdraw completion first. A crash can leave an unreferenced encrypted
            # blob, never a complete marker pointing to a deliberately deleted blob.
            remote.delete(point_key(remote.config, point.backup_id), etag)
            remote.delete(payload_key(remote.config, point), payload_etags[point.backup_id])
            removed += 1
        return dict(
            removed_count=removed, preserved_count=len(points) - removed, ignored_entries=ignored
        )
