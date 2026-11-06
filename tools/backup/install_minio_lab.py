"""Build the isolated S3 fixture from a fixed upstream commit (not a production service)."""

import argparse
import os
import shutil
from pathlib import Path

from tools.backup.offhost_contract import Code, RemoteError
from tools.backup.offhost_escrow import capture
from tools.backup.offhost_io import Deadline, directory

SOURCE = "9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a"


def install(go: str, root: Path) -> Path:
    directory(root)
    env = dict(os.environ)
    changes = dict(
        GOPATH=str(root / "gopath"),
        GOCACHE=str(root / "gocache"),
        GOMODCACHE=str(root / "gomodcache"),
        GOOS="linux",
        GOARCH="amd64",
        CGO_ENABLED="0",
        GOTOOLCHAIN="local",
        GOPROXY="https://proxy.golang.org,direct",
        GOSUMDB="sum.golang.org",
        GOENV="off",
    )
    # A dedicated subprocess receives the build environment; do not change user Go settings.
    import subprocess

    env.update(changes)
    env.pop("GOBIN", None)
    result = subprocess.run(
        [go, "install", "-trimpath", "-ldflags=-s -w", "github.com/minio/minio@" + SOURCE],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=900,
        check=False,
    )
    if result.returncode:
        raise RemoteError(Code.CONFIGURATION)
    binary = root / "gopath/bin" / ("linux_amd64/minio" if os.name == "nt" else "minio")
    metadata = capture([go, "version", "-m", str(binary)], Deadline(10)).decode()
    if "github.com/minio/minio" not in metadata or SOURCE[:12] not in metadata:
        raise RemoteError(Code.INTEGRITY)
    target = root / "minio"
    shutil.copyfile(binary, target)
    target.chmod(0o700)
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--go", default="go")
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    install(args.go, args.directory.resolve())
    print('{"operation":"install-minio-lab","success":true,"source":"' + SOURCE + '"}')
