"""OCI archives and bounded tool calls. Tool diagnostics never enter public evidence."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tarfile
import tempfile
from pathlib import Path

from tools.deploy.release_manifest import (
    MAX_JSON,
    Image,
    Manifest,
    RegistryPath,
    RegistryReference,
    ReleaseError,
    Spec,
    canonical,
    file_sha256,
    load_json,
    parse,
    sha256,
)

ARTIFACT_TYPE = "application/vnd.prl.release.v1"
PAYLOAD_TYPE = "application/vnd.prl.release.v1+json"
OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"


def command(args: list[str], *, cwd: Path | None = None, timeout: int = 600) -> bytes:
    try:
        result = subprocess.run(args, cwd=cwd, capture_output=True, timeout=timeout, check=False)
        if result.returncode:
            raise ReleaseError("tool-failed")
        return result.stdout
    except (OSError, subprocess.TimeoutExpired):
        raise ReleaseError("tool-unavailable") from None


def archive_image(path: Path, repository: str, source_sha: str) -> Image:
    """Never extract a tar; verify all referenced blobs, including compressed layers."""
    try:
        with tarfile.open(path, "r:*") as archive:
            members = archive.getmembers()
            if len(members) > 10000 or len({item.name for item in members}) != len(members):
                raise ValueError()

            def contents(name: str, *, limit: int = MAX_JSON) -> bytes:
                item = archive.getmember(name)
                if not item.isfile() or not 0 <= item.size <= limit:
                    raise ValueError()
                handle = archive.extractfile(item)
                if handle is None:
                    raise ValueError()
                with handle:
                    return handle.read(limit + 1)

            def blob(descriptor: dict, *, metadata: bool = True) -> bytes:
                digest = descriptor["digest"]
                if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
                    raise ValueError()
                name = "blobs/sha256/" + digest[7:]
                item = archive.getmember(name)
                if (
                    not item.isfile()
                    or type(descriptor["size"]) is not int
                    or descriptor["size"] != item.size
                    or not 0 <= item.size <= (MAX_JSON if metadata else 4 * 1024**3)
                ):
                    raise ValueError()
                handle = archive.extractfile(item)
                if handle is None:
                    raise ValueError()
                hasher = hashlib.sha256()
                with handle:
                    while chunk := handle.read(1024 * 1024):
                        hasher.update(chunk)
                actual = "sha256:" + hasher.hexdigest()
                if actual != digest:
                    raise ValueError()
                return contents(name) if metadata else b""

            if load_json(contents("oci-layout")) != {"imageLayoutVersion": "1.0.0"}:
                raise ValueError()
            index = load_json(contents("index.json"))
            descriptors = index["manifests"]
            if len(descriptors) != 1 or descriptors[0]["mediaType"] != OCI_MANIFEST:
                raise ValueError()
            descriptor = descriptors[0]
            manifest = load_json(blob(descriptor))
            if manifest["schemaVersion"] != 2 or manifest["mediaType"] != OCI_MANIFEST:
                raise ValueError()
            config = load_json(blob(manifest["config"]))
            if (
                config["os"] != "linux"
                or config["architecture"] != "amd64"
                or config["config"]["Labels"]["org.opencontainers.image.revision"] != source_sha
            ):
                raise ValueError()
            for layer in manifest["layers"]:
                blob(layer, metadata=False)
            return Image(
                reference=repository + "@" + descriptor["digest"],
                image_id=manifest["config"]["digest"],
                archive_sha256=file_sha256(path),
                platform="linux/amd64",
            )
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError):
        raise ReleaseError("invalid-image-archive") from None


class Registry:
    def __init__(self, *, plain_http: bool = False):
        self.plain_http = plain_http

    def _allow(self, reference: str) -> None:
        # Plain HTTP is explicitly lab-only. Never disable TLS for remote credentials.
        if self.plain_http and not reference.startswith(("127.0.0.1:", "localhost:")):
            raise ReleaseError("unsafe-registry")

    def skopeo(self, operation: str, *args: str) -> bytes:
        auth = os.environ.get("REGISTRY_AUTH_FILE")
        options = ["--authfile", auth] if auth else []
        return command(["skopeo", operation, *options, *args])

    def oras(self, operation: list[str], reference: str, *args: str, cwd=None) -> bytes:
        self._allow(reference)
        auth = os.environ.get("REGISTRY_AUTH_FILE")
        options = ["--registry-config", auth] if auth else []
        if self.plain_http:
            options += ["--plain-http"]
        return command(["oras", *operation, *options, reference, *args], cwd=cwd)

    def copy_archive(self, path: Path, image: Image, source_sha: str, tag: str) -> None:
        repository = image.reference.split("@", 1)[0]
        self._allow(repository)
        if archive_image(path, repository, source_sha) != image:
            raise ReleaseError("candidate-mismatch")
        options = ["--dest-tls-verify=false"] if self.plain_http else []
        self.skopeo(
            "copy",
            "--preserve-digests",
            *options,
            "oci-archive:" + str(path.resolve()),
            "docker://" + repository + ":" + tag,
        )
        self.verify_image(image, source_sha)

    def verify_image(
        self, image: Image, source_sha: str, *, pull: bool = False, complete: bool = False
    ) -> None:
        self._allow(image.reference)
        options = ["--tls-verify=false"] if self.plain_http else []
        raw = self.skopeo("inspect", *options, "--raw", "docker://" + image.reference)
        if sha256(raw) != image.digest:
            raise ReleaseError("registry-digest-mismatch")
        manifest = load_json(raw)
        descriptor = manifest.get("config")
        if not isinstance(descriptor, dict) or descriptor.get("digest") != image.image_id:
            raise ReleaseError("image-id-mismatch")
        config_raw = self.skopeo(
            "inspect", *options, "--config", "--raw", "docker://" + image.reference
        )
        if sha256(config_raw) != image.image_id:
            raise ReleaseError("image-id-mismatch")
        config = load_json(config_raw)
        runtime = config.get("config")
        labels = runtime.get("Labels") if isinstance(runtime, dict) else None
        if (
            config.get("os") != "linux"
            or config.get("architecture") != "amd64"
            or not isinstance(labels, dict)
            or labels.get("org.opencontainers.image.revision") != source_sha
        ):
            raise ReleaseError("image-source-mismatch")
        if complete:
            # A fresh OCI archive forces a registry download of every referenced
            # blob, even when Docker or an earlier verification cached the image.
            with tempfile.TemporaryDirectory(prefix="prl-retained-image-") as directory:
                archive = Path(directory) / "image.tar"
                source_options = ["--src-tls-verify=false"] if self.plain_http else []
                self.skopeo(
                    "copy",
                    "--preserve-digests",
                    *source_options,
                    "docker://" + image.reference,
                    "oci-archive:" + str(archive),
                )
                downloaded = archive_image(archive, image.reference.split("@", 1)[0], source_sha)
                if (
                    downloaded.digest != image.digest
                    or downloaded.image_id != image.image_id
                    or downloaded.platform != image.platform
                ):
                    raise ReleaseError("image-id-mismatch")
                # Archive serialization/checksum can differ; the manifest,
                # config, and all layer bytes above must retain their identities.
        if pull:
            command(["docker", "pull", "--platform", image.platform, image.reference])
            local = load_json(
                command(
                    [
                        "docker",
                        "image",
                        "inspect",
                        "--format",
                        '{"id":{{json .Id}}}',
                        image.reference,
                    ]
                )
            )
            if local != {"id": image.image_id}:
                raise ReleaseError("image-id-mismatch")

    def verify_images(self, spec: Spec, *, pull: bool = False, complete: bool = False) -> None:
        for image in spec.images.values():
            self.verify_image(image, spec.provenance.source_sha, pull=pull, complete=complete)

    def fetch(self, reference: str) -> Manifest:
        # Validate the reference before passing it to tools (no tags/options/URLs).
        try:
            RegistryReference(reference=reference)
        except ValueError:
            raise ReleaseError("invalid-reference") from None
        raw = self.oras(["manifest", "fetch"], reference)
        if sha256(raw) != reference.split("@", 1)[1]:
            raise ReleaseError("registry-digest-mismatch")
        artifact = load_json(raw)
        layers = artifact.get("layers")
        if (
            artifact.get("artifactType") != ARTIFACT_TYPE
            or not isinstance(layers, list)
            or len(layers) != 1
            or not isinstance(layers[0], dict)
            or layers[0].get("mediaType") != PAYLOAD_TYPE
            or type(layers[0].get("size")) is not int
            or not 0 < layers[0]["size"] <= MAX_JSON
        ):
            raise ReleaseError("invalid-release-artifact")
        digest = layers[0]["digest"]
        RegistryReference(reference=reference.split("@", 1)[0] + "@" + digest)
        # Fixed stdout target; never use untrusted artifact filenames as paths.
        payload = self.oras(
            ["blob", "fetch"], reference.split("@", 1)[0] + "@" + digest, "--output", "-"
        )
        if len(payload) != layers[0]["size"] or sha256(payload) != digest:
            raise ReleaseError("registry-digest-mismatch")
        return parse(payload, Manifest)

    def existing(self, target: str) -> bytes | None:
        """Only a registry's explicit not-found response permits creation."""
        self._allow(target)
        options = ["--plain-http"] if self.plain_http else []
        auth = os.environ.get("REGISTRY_AUTH_FILE")
        if auth:
            options += ["--registry-config", auth]
        try:
            result = subprocess.run(
                ["oras", "manifest", "fetch", *options, target],
                capture_output=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise ReleaseError("tool-unavailable") from None
        if result.returncode == 0:
            return result.stdout
        if re.search(
            rb"MANIFEST_UNKNOWN|NAME_UNKNOWN|manifest unknown|name unknown|"
            rb"404 Not Found|: not found",
            result.stderr,
        ):
            return None
        raise ReleaseError("registry-unavailable")

    def publish(self, repository: str, manifest: Manifest) -> str:
        """Manifest is the commit marker. No stable pointer exists in this publisher."""
        RegistryPath(repository=repository)
        target = repository + ":" + manifest.release_id
        self._allow(target)
        with tempfile.TemporaryDirectory(prefix="prl-release-") as directory:
            cwd = Path(directory)
            (cwd / "release.json").write_bytes(canonical(manifest))
            # Materialize locally first, so an existing release ID is compared
            # BEFORE any registry write. A fixed annotation makes retries stable.
            parameters = [
                "--artifact-type",
                ARTIFACT_TYPE,
                "--annotation",
                "org.opencontainers.image.created=" + manifest.created_at,
                "--export-manifest",
                "artifact.json",
                "release.json:" + PAYLOAD_TYPE,
            ]
            command(
                ["oras", "push", "--oci-layout", "layout:" + manifest.release_id, *parameters],
                cwd=cwd,
            )
            expected = (cwd / "artifact.json").read_bytes()
            existing = self.existing(target)
            if existing is not None and existing != expected:
                raise ReleaseError("release-id-conflict")
            reference = repository + "@" + sha256(expected)
            if existing is not None:
                if self.fetch(reference) != manifest:
                    raise ReleaseError("published-manifest-mismatch")
                return reference
            self.oras(
                ["push"],
                target,
                *parameters,
                cwd=cwd,
            )
            if (cwd / "artifact.json").read_bytes() != expected:
                raise ReleaseError("registry-digest-mismatch")
        if self.fetch(reference) != manifest:
            raise ReleaseError("published-manifest-mismatch")
        return reference
