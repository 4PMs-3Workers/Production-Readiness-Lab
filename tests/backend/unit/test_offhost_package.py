import hashlib
import io
import tarfile

import pytest
from tools.backup.offhost_contract import RemoteError, canonical
from tools.backup.offhost_io import Deadline
from tools.backup.offhost_package import Package, pack, unpack

ID = "prl-backup-20261001T000000Z-" + "a" * 32


def package_files(tmp_path):
    files = {}
    for name in (
        "backup/manifest.json",
        "backup/COMPLETE",
        "backup/users.jsonl",
        "release.json",
        "escrow.json",
        "images/backend.tar",
    ):
        path = tmp_path / name.replace("/", "-")
        path.write_bytes((name + "-private-payload").encode())
        path.chmod(0o600)
        files[name] = path
    return files


def test_private_package_roundtrip_checks_every_file(tmp_path):
    files = package_files(tmp_path)
    source, target = tmp_path / "package.tar.gz", tmp_path / "target"
    metadata = pack(files, ID, source, 65536, Deadline(5))
    assert unpack(source, target, 65536, Deadline(5)) == metadata
    for name, path in files.items():
        assert (target / name).read_bytes() == path.read_bytes()


@pytest.mark.parametrize(
    "fault", ["traversal", "symlink", "duplicate", "hash", "missing", "oversize"]
)
def test_untrusted_members_are_never_extracted(tmp_path, fault):
    files = package_files(tmp_path)
    entries = {
        name: dict(bytes=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        for name, path in files.items()
    }
    metadata = Package.model_validate(
        dict(format="prl-offhost-package", format_version=1, backup_id=ID, files=entries)
    )
    source = tmp_path / "malicious.tar.gz"
    with tarfile.open(source, "w:gz") as archive:
        raw = canonical(metadata)
        item = tarfile.TarInfo("package.json")
        item.size = len(raw)
        archive.addfile(item, io.BytesIO(raw))
        for index, (name, path) in enumerate(files.items()):
            if fault == "missing" and index == 0:
                continue
            item = tarfile.TarInfo("../escaped" if fault == "traversal" and index == 0 else name)
            raw = path.read_bytes()
            if fault == "hash" and index == 0:
                raw = b"x" * len(raw)
            item.size = len(raw) + (1 if fault == "oversize" and index == 0 else 0)
            if fault == "symlink" and index == 0:
                item.type, item.linkname = tarfile.SYMTYPE, "../escaped"
                archive.addfile(item)
            else:
                archive.addfile(item, io.BytesIO(raw + (b"x" if item.size > len(raw) else b"")))
            if fault == "duplicate" and index == 0:
                archive.addfile(item, io.BytesIO(raw))
    with pytest.raises(RemoteError):
        unpack(source, tmp_path / "target", 65536, Deadline(5))
    assert not (tmp_path / "escaped").exists()


def test_expired_pack_and_byte_budget_never_produce_accepted_packages(tmp_path):
    files = package_files(tmp_path)
    with pytest.raises(RemoteError):
        pack(files, ID, tmp_path / "expired", 65536, Deadline(0))
    with pytest.raises(RemoteError):
        pack(files, ID, tmp_path / "oversize", 20, Deadline(5))
