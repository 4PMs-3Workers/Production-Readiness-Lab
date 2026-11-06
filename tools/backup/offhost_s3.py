"""One certificate-verified S3 adapter with bounded retries and immutable writes."""

import base64
import hashlib
import os
import re
import time
from pathlib import Path

from boto3.session import Session
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from tools.backup.offhost_contract import (
    MAX_CONTRACT,
    Code,
    Credentials,
    RemoteConfig,
    RemoteError,
    Retirement,
    contract,
    private_file,
)
from tools.backup.offhost_io import Deadline, regular
from tools.backup.offhost_package import BoundedReader


class S3:
    def __init__(
        self,
        config: RemoteConfig,
        deadline: Deadline,
        *,
        client=None,
        credentials: Path | None = None,
    ):
        self.config, self.deadline = config, deadline
        if client is None:
            if credentials is None:
                raise RemoteError(Code.KEY)
            secret = contract(private_file(credentials, 32768), Credentials)
            if config.ca_file is not None:
                private_file(Path(config.ca_file), MAX_CONTRACT, secret=False)
            client = Session(
                aws_access_key_id=secret.access_key,
                aws_secret_access_key=secret.secret_key,
                aws_session_token=secret.session_token,
                region_name=config.region,
            ).client(
                "s3",
                endpoint_url=config.endpoint,
                verify=config.ca_file or True,
                config=Config(
                    signature_version="s3v4",
                    connect_timeout=config.request_seconds,
                    read_timeout=config.request_seconds,
                    retries={"total_max_attempts": 1},
                    s3={"addressing_style": "path"},
                    proxies={},
                    request_checksum_calculation="when_required",
                    response_checksum_validation="when_required",
                ),
            )
        self.client = client

    def key(self, key: str) -> None:
        if not key.startswith(self.config.scope + "points/") or any(
            part in {"", ".", ".."} for part in key.split("/")
        ):
            raise RemoteError(Code.IDENTITY)

    def retry(self, action):
        for attempt in range(self.config.attempts):
            self.deadline.remaining()
            try:
                value = action()
                self.deadline.remaining()
                return value
            except ClientError as error:
                status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
                if status == 412:
                    raise RemoteError(Code.CONFLICT) from None
                if status not in {409, 429, 500, 502, 503, 504}:
                    raise RemoteError(Code.TRANSPORT) from None
            except (BotoCoreError, OSError):
                pass
            if attempt + 1 < self.config.attempts:
                time.sleep(min(0.1 * 2**attempt, self.deadline.remaining()))
        raise RemoteError(Code.TRANSPORT)

    def get(self, key: str) -> tuple[bytes, str] | None:
        self.key(key)

        def action():
            try:
                response = self.client.get_object(Bucket=self.config.bucket, Key=key)
            except ClientError as error:
                if error.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 404:
                    return None
                raise
            with response["Body"] as stream:
                if response["ContentLength"] > MAX_CONTRACT:
                    raise RemoteError(Code.INTEGRITY)
                value = stream.read(MAX_CONTRACT + 1)
            if len(value) != response["ContentLength"] or len(value) > MAX_CONTRACT:
                raise RemoteError(Code.INTEGRITY)
            return value, response["ETag"]

        return self.retry(action)

    def put(self, key: str, value: bytes) -> None:
        self.key(key)
        if len(value) > MAX_CONTRACT:
            raise RemoteError(Code.INTEGRITY)
        self.retry(
            lambda: self.client.put_object(
                Bucket=self.config.bucket,
                Key=key,
                Body=value,
                ContentLength=len(value),
                IfNoneMatch="*",
                ChecksumSHA256=base64.b64encode(hashlib.sha256(value).digest()).decode(),
            )
        )

    def upload(self, key: str, source: Path, sha256: str, size: int) -> None:
        self.key(key)
        if regular(source, self.config.max_bytes + 16 * 1024**2) != size:
            raise RemoteError(Code.INTEGRITY)

        def action():
            with source.open("rb") as handle:
                # The SDK rewinds this bounded reader if signing requires a seek.
                reader = TransferReader(handle, self.deadline)
                return self.client.put_object(
                    Bucket=self.config.bucket,
                    Key=key,
                    Body=reader,
                    ContentLength=size,
                    IfNoneMatch="*",
                    ChecksumSHA256=base64.b64encode(bytes.fromhex(sha256)).decode(),
                )

        self.retry(action)

    def download(self, key: str, target: Path, sha256: str, size: int) -> str:
        self.key(key)
        if (
            not 0 < size <= self.config.max_bytes + 16 * 1024**2
            or target.exists()
            or target.is_symlink()
        ):
            raise RemoteError(Code.INTEGRITY)

        def action():
            response = self.client.get_object(Bucket=self.config.bucket, Key=key)
            try:
                if response["ContentLength"] != size:
                    raise RemoteError(Code.INTEGRITY)
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                checksum, count = hashlib.sha256(), 0
                with os.fdopen(fd, "wb") as output:
                    while block := response["Body"].read(1024 * 1024):
                        self.deadline.remaining()
                        count += len(block)
                        if count > size:
                            raise RemoteError(Code.INTEGRITY)
                        checksum.update(block)
                        output.write(block)
                    output.flush()
                    os.fsync(output.fileno())
                if count != size or checksum.hexdigest() != sha256:
                    raise RemoteError(Code.INTEGRITY)
                return response["ETag"]
            except BaseException:
                target.unlink(missing_ok=True)
                raise
            finally:
                response["Body"].close()

        return self.retry(action)

    def inventory(self) -> list[str]:
        prefix, keys, token = self.config.scope + "points/", [], None
        while True:
            request = dict(Bucket=self.config.bucket, Prefix=prefix, MaxKeys=1000)
            if token is not None:
                request["ContinuationToken"] = token
            response = self.retry(lambda request=request: self.client.list_objects_v2(**request))
            for item in response.get("Contents", []):
                self.key(item["Key"])
                keys.append(item["Key"])
                if len(keys) > 20000:
                    raise RemoteError(Code.TIMEOUT)
            if not response.get("IsTruncated", False):
                return keys
            next_token = response.get("NextContinuationToken")
            if not isinstance(next_token, str) or len(next_token) > 4096 or next_token == token:
                raise RemoteError(Code.INTEGRITY)
            token = next_token

    def replace(self, key: str, value: bytes, etag: str) -> None:
        self.key(key)
        if len(value) > MAX_CONTRACT:
            raise RemoteError(Code.INTEGRITY)
        self.retry(
            lambda: self.client.put_object(
                Bucket=self.config.bucket,
                Key=key,
                Body=value,
                ContentLength=len(value),
                IfMatch=etag,
                ChecksumSHA256=base64.b64encode(hashlib.sha256(value).digest()).decode(),
            )
        )

    def delete(self, key: str) -> None:
        self.key(key)
        relative = key.removeprefix(self.config.scope + "points/")
        match = re.fullmatch(
            r"(prl-backup-\d{8}T\d{6}Z-[0-9a-f]{32})/blobs/([0-9a-f]{64})\.age", relative
        )
        if match is None:
            raise RemoteError(Code.IDENTITY)
        marker = self.get(self.config.scope + "points/" + match[1] + "/complete.json")
        if marker is None:
            raise RemoteError(Code.IDENTITY)
        retired = contract(marker[0], Retirement)
        retired.point.require(self.config, match[1])
        if retired.point.encrypted_sha256 != match[2]:
            raise RemoteError(Code.IDENTITY)
        self.retry(lambda: self.client.delete_object(Bucket=self.config.bucket, Key=key))


class TransferReader(BoundedReader):
    def seek(self, *args):
        self.deadline.remaining()
        return self.source.seek(*args)

    def tell(self):
        return self.source.tell()

    def readable(self):
        return True
