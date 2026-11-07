"""A disposable containerd recovery daemon for the Linux acceptance lab."""

import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from tools.backup.offhost_contract import Code, RemoteError
from tools.backup.offhost_escrow import capture
from tools.backup.offhost_io import Deadline, run

ENGINE = (
    "docker.io/library/docker:28.5.2-dind@"
    "sha256:9a06753d2401cd049b34cd27dbbc3e0db717d4c1db7bc7f2efad1c187e00bf5a"
)


def containerd_store(deadline: Deadline) -> bool:
    status = json.loads(capture(["docker", "info", "--format", "{{json .DriverStatus}}"], deadline))
    return ["driver-type", "io.containerd.snapshotter.v1"] in (status or [])


@contextmanager
def daemon_socket(endpoint: str):
    keys = ("DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH")
    original = {key: os.environ.pop(key, None) for key in keys}
    os.environ["DOCKER_HOST"] = endpoint
    try:
        yield
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextmanager
def empty_recovery_daemon(inputs: Path):
    if os.name != "posix":
        raise RemoteError(Code.CONFIGURATION)
    owner = uuid4().hex
    name = "prl-recovery-daemon-" + owner
    with tempfile.TemporaryDirectory(prefix="prl-docker-", dir="/tmp") as socket_dir:
        socket = Path(socket_dir) / "docker.sock"
        run(
            [
                "docker",
                "run",
                "--detach",
                "--pull",
                "never",
                "--privileged",
                "--network",
                "host",
                "--name",
                name,
                "--label",
                "prl.offhost.daemon=" + owner,
                # The DinD entrypoint otherwise mounts /tmp itself and hides
                # pytest's nested runtime bind mount from the recovery daemon.
                "--tmpfs",
                "/tmp:rw,exec,dev,mode=1777",
                "--mount",
                f"type=bind,src={socket_dir},dst=/prl-socket",
                "--mount",
                f"type=bind,src={inputs},dst={inputs},readonly",
                ENGINE,
                "dockerd",
                "--host=unix:///prl-socket/docker.sock",
                f"--group={os.getgid()}",
                "--feature=containerd-snapshotter=true",
                "--bridge=none",
                "--iptables=false",
                "--ip6tables=false",
                "--ip-forward=false",
                "--ip-masq=false",
                "--userland-proxy=true",
            ],
            Deadline(45),
        )
        try:
            run(["docker", "exec", name, "test", "-d", str(inputs)], Deadline(15))
            with daemon_socket("unix://" + str(socket)):
                deadline = Deadline(90)
                while True:
                    deadline.remaining()
                    if socket.exists():
                        try:
                            uses_containerd = containerd_store(Deadline(3))
                        except RemoteError:
                            pass
                        else:
                            if not uses_containerd:
                                raise RemoteError(Code.CONFIGURATION)
                            break
                    time.sleep(0.25)
                for args in (
                    ["image", "ls", "--all", "--quiet"],
                    ["volume", "ls", "--quiet"],
                    ["container", "ls", "--all", "--quiet"],
                ):
                    if capture(["docker", *args], deadline).strip():
                        raise RemoteError(Code.IDENTITY)
                yield
        finally:
            # The scoped socket has already been restored to the outer daemon.
            record = json.loads(capture(["docker", "inspect", name], Deadline(20)))[0]
            if record["Config"]["Labels"].get("prl.offhost.daemon") != owner:
                raise RemoteError(Code.IDENTITY)
            run(["docker", "rm", "--force", "--volumes", record["Id"]], Deadline(45))
