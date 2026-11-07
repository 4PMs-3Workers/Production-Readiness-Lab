import os
from pathlib import Path

import pytest
from tools.backup.offhost_lab import StoreLab


@pytest.fixture(scope="session")
def age_executable():
    path = Path(os.environ.get("PRL_OFFHOST_AGE", ""))
    if os.environ.get("RUN_OFFHOST_TESTS") != "1" or not path.is_absolute() or not path.is_file():
        pytest.fail(
            "Required off-host acceptance needs RUN_OFFHOST_TESTS=1 and the pinned age executable"
        )
    return path


@pytest.fixture(scope="session")
def store_lab(tmp_path_factory, age_executable):
    binary = Path(os.environ.get("PRL_OFFHOST_MINIO_BINARY", ""))
    if not binary.is_absolute() or not binary.is_file():
        pytest.fail("Required HTTPS store needs the fixed-source Linux MinIO fixture binary")
    lab = StoreLab(tmp_path_factory.mktemp("offhost-store"), binary)
    try:
        lab.start()
        yield lab
    finally:
        lab.close()
