"""UUID-owned HTTPS S3 acceptance store; only allowlisted evidence leaves the lab."""

from __future__ import annotations

import ipaddress
import json
import os
import secrets
import shutil
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from boto3.session import Session
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from tools.backup.offhost_contract import Code, RemoteConfig, RemoteError
from tools.backup.offhost_escrow import capture
from tools.backup.offhost_io import Deadline, directory, run
from tools.deploy.release_manifest import ROOT

from app.backup.storage import write_private


def certificates(path: Path) -> Path:
    directory(path)
    now = datetime.now(UTC)
    ca_key, server_key = rsa.generate_private_key(
        public_exponent=65537, key_size=2048
    ), rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "PRL isolated store")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=6))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    server = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=6))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(server_key.public_key()), critical=False
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    write_private(
        path / "private.key",
        server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )
    write_private(path / "public.crt", server.public_bytes(serialization.Encoding.PEM))
    write_private(path / "ca.pem", ca.public_bytes(serialization.Encoding.PEM))
    return path / "ca.pem"


class StoreLab:
    def __init__(self, scratch: Path, binary: Path):
        self.scratch, self.binary = directory(scratch), binary
        self.owner = uuid4().hex
        self.name = "prl-offhost-" + self.owner
        self.image = "prl-offhost-store:" + self.owner
        self.bucket = "prl-offhost-" + self.owner
        self.volume = self.name + "-data"
        self.access, self.secret = secrets.token_hex(16), secrets.token_urlsafe(32)
        self.endpoint, self.ca = "", scratch / "certs/ca.pem"
        self.created_image = self.created_network = self.created_container = False
        self.created_volume = False

    def start(self) -> None:
        with self.binary.open("rb") as executable:
            if executable.read(4) != b"\x7fELF":
                raise RemoteError(Code.CONFIGURATION)
        build = directory(self.scratch / "build")
        shutil.copyfile(self.binary, build / "minio")
        deadline = Deadline(600)
        run(
            [
                "docker",
                "build",
                "--label",
                "prl.offhost.owner=" + self.owner,
                "-f",
                str(ROOT / "tools/backup/minio-lab.Dockerfile"),
                "-t",
                self.image,
                str(build),
            ],
            deadline,
        )
        self.created_image = True
        self.ca = certificates(self.scratch / "certs")
        env = self.scratch / "store.env"
        write_private(
            env,
            f"MINIO_ROOT_USER={self.access}\nMINIO_ROOT_PASSWORD={self.secret}\nMINIO_BROWSER=off\nHOME=/tmp\n".encode(),
        )
        run(
            [
                "docker",
                "network",
                "create",
                "--label",
                "prl.offhost.owner=" + self.owner,
                self.name,
            ],
            deadline,
        )
        self.created_network = True
        user = str(os.getuid()) if os.name != "nt" else "0"
        run(
            [
                "docker",
                "volume",
                "create",
                "--label",
                "prl.offhost.owner=" + self.owner,
                self.volume,
            ],
            deadline,
        )
        self.created_volume = True
        run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--user",
                "0",
                "--entrypoint",
                "chown",
                "--mount",
                f"type=volume,src={self.volume},dst=/data",
                self.image,
                user,
                "/data",
            ],
            deadline,
        )
        run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                self.name,
                "--label",
                "prl.offhost.owner=" + self.owner,
                "--network",
                self.name,
                "--publish",
                "127.0.0.1::9000",
                "--user",
                user,
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--tmpfs",
                "/tmp:rw,mode=1777",
                "--env-file",
                str(env),
                "--mount",
                f"type=bind,src={self.scratch / 'certs'},dst=/certs,readonly",
                "--mount",
                f"type=volume,src={self.volume},dst=/data",
                self.image,
                "server",
                "/data",
                "--address",
                ":9000",
                "--certs-dir",
                "/certs",
            ],
            deadline,
        )
        self.created_container = True
        record = json.loads(capture(["docker", "inspect", self.name], deadline))[0]
        port = record["NetworkSettings"]["Ports"]["9000/tcp"][0]["HostPort"]
        if not str(port).isdigit():
            raise RemoteError(Code.CONFIGURATION)
        self.endpoint = "https://127.0.0.1:" + port
        self.admin = Session(
            aws_access_key_id=self.access, aws_secret_access_key=self.secret
        ).client(
            "s3",
            endpoint_url=self.endpoint,
            region_name="us-east-1",
            verify=str(self.ca),
            config=Config(
                signature_version="s3v4",
                retries={"total_max_attempts": 1},
                connect_timeout=1,
                read_timeout=1,
                s3={"addressing_style": "path"},
                proxies={},
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        )
        for _ in range(60):
            try:
                self.admin.create_bucket(Bucket=self.bucket)
                return
            except Exception as error:
                self.start_error = type(error).__name__
                time.sleep(0.5)
        write_private(
            self.scratch / "start-status.json",
            json.dumps(dict(error_type=self.start_error)).encode(),
        )
        raise RemoteError(Code.TRANSPORT)

    def configure(
        self, source_id: str, recipient: str, path: Path, *, request_seconds: int | None = None
    ) -> tuple[RemoteConfig, Path]:
        config = RemoteConfig(
            endpoint=self.endpoint,
            region="us-east-1",
            bucket=self.bucket,
            prefix="prl-managed",
            environment="lab",
            store="s3",
            source_id=source_id,
            recipient=recipient,
            ca_file=str(self.ca),
            max_seconds=900,
        )
        # Image escrow uses the normal bounded operational budget. Small fault
        # fixtures can request a shorter budget without constraining bulk copies.
        if request_seconds is not None:
            config = RemoteConfig.model_validate(
                dict(config.model_dump(), request_seconds=request_seconds)
            )
        policy = (
            (ROOT / "tools/backup/offhost-policy.example.json")
            .read_text(encoding="utf-8")
            .replace("REPLACE_BUCKET", self.bucket)
            .replace("managed-backups/production/REPLACE_SOURCE_ID/", config.scope)
        )
        sts = Session(aws_access_key_id=self.access, aws_secret_access_key=self.secret).client(
            "sts",
            endpoint_url=self.endpoint,
            region_name="us-east-1",
            verify=str(self.ca),
            config=Config(
                signature_version="v4",
                retries={"total_max_attempts": 1},
                connect_timeout=10,
                read_timeout=10,
                proxies={},
            ),
        )
        try:
            credentials = sts.assume_role(
                RoleArn="arn:aws:iam::123456789012:role/prl-offhost-lab",
                RoleSessionName="prl-offhost-lab",
                Policy=policy,
                DurationSeconds=3600,
            )["Credentials"]
        except (BotoCoreError, ClientError):
            raise RemoteError(Code.KEY) from None
        finally:
            sts.close()
        write_private(
            path,
            json.dumps(
                dict(
                    access_key=credentials["AccessKeyId"],
                    secret_key=credentials["SecretAccessKey"],
                    session_token=credentials["SessionToken"],
                )
            ).encode(),
        )
        return config, path

    def close(self) -> None:
        deadline = Deadline(90)
        if self.created_container:
            data = json.loads(capture(["docker", "inspect", self.name], deadline))[0]
            if (
                data.get("Name") != "/" + self.name
                or data["Config"]["Labels"].get("prl.offhost.owner") != self.owner
            ):
                raise RemoteError(Code.IDENTITY)
            run(["docker", "rm", "--force", data["Id"]], deadline)
        if self.created_network:
            data = json.loads(capture(["docker", "network", "inspect", self.name], deadline))[0]
            if data.get("Labels", {}).get("prl.offhost.owner") != self.owner:
                raise RemoteError(Code.IDENTITY)
            run(["docker", "network", "rm", data["Id"]], deadline)
        if self.created_volume:
            data = json.loads(capture(["docker", "volume", "inspect", self.volume], deadline))[0]
            if data.get("Labels", {}).get("prl.offhost.owner") != self.owner:
                raise RemoteError(Code.IDENTITY)
            run(["docker", "volume", "rm", self.volume], deadline)
        if self.created_image:
            data = json.loads(capture(["docker", "image", "inspect", self.image], deadline))[0]
            if data["Config"]["Labels"].get("prl.offhost.owner") != self.owner:
                raise RemoteError(Code.IDENTITY)
            run(["docker", "image", "rm", self.image], deadline)


def age_keys(executable: Path, vault: Path) -> tuple[Path, str]:
    directory(vault)
    identity = vault / "identity.txt"
    keygen = executable.with_name("age-keygen" + executable.suffix)
    run([str(keygen), "--output", str(identity)], Deadline(10))
    identity.chmod(0o600)
    recipient = capture([str(keygen), "-y", str(identity)], Deadline(10), 1024).decode().strip()
    return identity, recipient
