"""Validate the exact Docker and OCI views of an untagged image export."""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import tarfile
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, cast

from tools.backup.offhost_contract import Code, RemoteError
from tools.backup.offhost_io import Deadline
from tools.backup.offhost_package import ArchiveReader, HashingReader, PlainTarInfo

from app.backup.manifest import no_duplicate_keys

OCI = "application/vnd.oci.image."
DOCKER = "application/vnd.docker.distribution."
MANIFESTS = {OCI + "manifest.v1+json", DOCKER + "manifest.v2+json"}
INDEXES = {OCI + "index.v1+json", DOCKER + "manifest.list.v2+json"}
CONFIGS = {OCI + "config.v1+json", "application/vnd.docker.container.image.v1+json"}
PLAIN_LAYERS = {OCI + "layer.v1.tar", "application/vnd.docker.image.rootfs.diff.tar"}
GZIP_LAYERS = {OCI + "layer.v1.tar+gzip", "application/vnd.docker.image.rootfs.diff.tar.gzip"}
IMAGE_NAMES = {"org.opencontainers.image.ref.name", "io.containerd.image.name"}
MAX_METADATA = 1024**2
MAX_LAYER_BYTES = 4 * 1024**3
INDEX_FIELDS = ("schemaVersion", "mediaType", "artifactType", "manifests", "subject", "annotations")
MANIFEST_FIELDS = (
    "schemaVersion",
    "mediaType",
    "artifactType",
    "config",
    "layers",
    "subject",
    "annotations",
)
DESCRIPTOR_FIELDS = (
    "mediaType",
    "digest",
    "size",
    "urls",
    "annotations",
    "data",
    "platform",
    "artifactType",
)
PLATFORM_FIELDS = ("architecture", "os", "os.version", "os.features", "variant", "features")
LEGACY_FIELDS = ("Config", "RepoTags", "Layers", "Parent", "LayerSources")
IMAGE_FIELDS = (
    "created",
    "author",
    "architecture",
    "os",
    "os.version",
    "os.features",
    "variant",
    "config",
    "rootfs",
    "history",
    "parent",
    "container",
    "container_config",
    "docker_version",
)
IMAGE_CONFIG_FIELDS = (
    "User",
    "ExposedPorts",
    "Env",
    "Entrypoint",
    "Cmd",
    "Volumes",
    "WorkingDir",
    "Labels",
    "StopSignal",
    "ArgsEscaped",
    "Healthcheck",
    "Shell",
    "OnBuild",
    "StopTimeout",
)


def sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def blob_path(digest: str) -> str:
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError()
    return "blobs/sha256/" + digest[7:]


def json_value(raw: bytes):
    return json.loads(raw, object_pairs_hook=no_duplicate_keys)


def record(value, fields: tuple[str, ...]) -> dict:
    """Disallow noncanonical aliases that Go would decode into a typed field."""
    aliases = {field.casefold(): field for field in fields}
    if not isinstance(value, dict) or any(aliases.get(key.casefold(), key) != key for key in value):
        raise ValueError()
    return value


@dataclass(frozen=True)
class Descriptor:
    digest: str
    size: int
    media_type: str

    @classmethod
    def read(cls, value: dict) -> Descriptor:
        value = record(value, DESCRIPTOR_FIELDS)
        if IMAGE_NAMES & (value.get("annotations") or {}).keys() or value.get("urls"):
            raise ValueError()
        if value.get("platform") is not None:
            record(value["platform"], PLATFORM_FIELDS)
        digest, size, media_type = value["digest"], value["size"], value["mediaType"]
        blob_path(digest)
        if type(size) is not int or not 0 <= size <= MAX_LAYER_BYTES:
            raise ValueError()
        return cls(digest, size, media_type)


def platform_manifest(index: dict) -> Descriptor:
    index = record(index, INDEX_FIELDS)
    if index.get("schemaVersion") != 2 or IMAGE_NAMES & (index.get("annotations") or {}).keys():
        raise ValueError()
    candidates = []
    for value in index["manifests"]:
        descriptor = Descriptor.read(value)
        platform = value.get("platform") or {}
        if (platform.get("os"), platform.get("architecture")) == ("linux", "amd64"):
            candidates.append(descriptor)
    if len(candidates) != 1 or candidates[0].media_type not in MANIFESTS:
        raise ValueError()
    return candidates[0]


def manifest_content(raw: bytes) -> tuple[Descriptor, list[Descriptor]]:
    value = record(json_value(raw), MANIFEST_FIELDS)
    if (
        value.get("schemaVersion") != 2
        or value.get("mediaType") not in MANIFESTS
        or IMAGE_NAMES & (value.get("annotations") or {}).keys()
        or "subject" in value
    ):
        raise ValueError()
    return Descriptor.read(value["config"]), [Descriptor.read(layer) for layer in value["layers"]]


@dataclass(frozen=True)
class ArchiveImage:
    config_id: str
    config: dict
    target: Descriptor | None
    selected: Descriptor | None
    pinned_records: dict[str, bytes]


class ImageTar:
    def __init__(self, archive: tarfile.TarFile, deadline: Deadline):
        self.archive, self.deadline = archive, deadline
        self.members = {}
        for item in archive:
            deadline.remaining()
            if (
                item.name in self.members
                or len(self.members) >= 20000
                or "\\" in item.name
                or any(part in {"", ".", ".."} for part in item.name.split("/"))
                or item.name == "repositories"
            ):
                raise ValueError()
            self.members[item.name] = item

    def open(self, name: str):
        member = self.members[name]
        if not member.isfile() or member.size < 0:
            raise ValueError()
        handle = self.archive.extractfile(member)
        if handle is None:
            raise ValueError()
        return handle

    def metadata(self, name: str, descriptor: Descriptor | None = None) -> bytes:
        size = self.members[name].size
        if size > MAX_METADATA or (descriptor is not None and size != descriptor.size):
            raise ValueError()
        with self.open(name) as handle:
            raw = handle.read(MAX_METADATA + 1)
        if len(raw) != size or (descriptor is not None and sha(raw) != descriptor.digest):
            raise ValueError()
        return raw

    def oci_view(self) -> tuple[Descriptor | None, Descriptor | None, dict[str, bytes]]:
        if "index.json" not in self.members and "oci-layout" not in self.members:
            return None, None, {}
        if json_value(self.metadata("oci-layout")) != {"imageLayoutVersion": "1.0.0"}:
            raise ValueError()
        index = record(json_value(self.metadata("index.json")), INDEX_FIELDS)
        if (
            index.get("schemaVersion") != 2
            or IMAGE_NAMES & (index.get("annotations") or {}).keys()
            or len(index["manifests"]) != 1
        ):
            raise ValueError()
        target = Descriptor.read(index["manifests"][0])
        raw = self.metadata(blob_path(target.digest), target)
        records = {target.digest: raw}
        if target.media_type in INDEXES:
            selected = platform_manifest(json_value(raw))
            records[selected.digest] = self.metadata(blob_path(selected.digest), selected)
        elif target.media_type in MANIFESTS:
            selected = target
        else:
            raise ValueError()
        return target, selected, records

    def verify_layers(
        self, legacy: list[str], diff_ids: list[str], layers: list[Descriptor]
    ) -> None:
        budget = MAX_LAYER_BYTES
        for index, (name, diff_id) in enumerate(zip(legacy, diff_ids, strict=True)):
            blob_path(diff_id)
            with self.open(name) as handle:
                raw = HashingReader(handle, self.deadline)
                compressed = handle.read(2) == b"\x1f\x8b"
                handle.seek(0)
                digest = hashlib.sha256()
                layer_stream = (
                    gzip.GzipFile(fileobj=cast(BinaryIO, raw)) if compressed else nullcontext(raw)
                )
                with layer_stream as content:
                    reader = ArchiveReader(content, self.deadline, budget)
                    while block := reader.read(1024**2):
                        digest.update(block)
                budget = reader.remaining
                if "sha256:" + digest.hexdigest() != diff_id:
                    raise ValueError()
                if layers:
                    layer = layers[index]
                    if (
                        blob_path(layer.digest) != name
                        or layer.media_type not in (GZIP_LAYERS if compressed else PLAIN_LAYERS)
                        or layer.size != self.members[name].size
                        or layer.digest != "sha256:" + raw.checksum.hexdigest()
                    ):
                        raise ValueError()

    def image(self) -> ArchiveImage:
        legacy = json_value(self.metadata("manifest.json"))
        if not isinstance(legacy, list) or len(legacy) != 1:
            raise ValueError()
        item = record(legacy[0], LEGACY_FIELDS)
        if item.get("RepoTags") not in (None, []) or item.get("Parent"):
            raise ValueError()
        config_raw = self.metadata(item["Config"])
        config_id, config = sha(config_raw), record(json_value(config_raw), IMAGE_FIELDS)
        if (config["os"], config["architecture"]) != ("linux", "amd64"):
            raise ValueError()
        if config.get("config") is not None:
            execution = record(config["config"], IMAGE_CONFIG_FIELDS)
            if execution.get("Healthcheck") is not None:
                record(
                    execution["Healthcheck"],
                    ("Test", "Interval", "Timeout", "StartPeriod", "StartInterval", "Retries"),
                )
        rootfs = record(config["rootfs"], ("type", "diff_ids"))
        diff_ids, names = rootfs["diff_ids"], item["Layers"]
        if (
            rootfs["type"] != "layers"
            or not isinstance(diff_ids, list)
            or not isinstance(names, list)
        ):
            raise ValueError()
        target, selected, records = self.oci_view()
        layers = []
        if selected is not None:
            descriptor, layers = manifest_content(records[selected.digest])
            if (
                descriptor.digest != config_id
                or descriptor.media_type not in CONFIGS
                or self.metadata(blob_path(config_id), descriptor) != config_raw
                or len(layers) != len(names)
            ):
                raise ValueError()
        self.verify_layers(names, diff_ids, layers)
        return ArchiveImage(config_id, config, target, selected, records)


def inspect_archive(path: Path, deadline: Deadline) -> ArchiveImage:
    try:
        with tarfile.open(path, "r:", tarinfo=PlainTarInfo) as archive:
            return ImageTar(archive, deadline).image()
    except (tarfile.TarError, OSError, EOFError, ValueError, KeyError, TypeError, AttributeError):
        raise RemoteError(Code.IMAGE) from None


def verify_vendor(reference: str, image: ArchiveImage, records: dict[str, bytes]) -> None:
    """The pinned manifest authenticates config bytes and their ordered rootfs DiffIDs."""
    try:
        pinned = reference.rsplit("@", 1)[1]
        raw = records[pinned]
        if sha(raw) != pinned:
            raise ValueError()
        if "manifests" in json_value(raw):
            selected = platform_manifest(json_value(raw))
            raw = records[selected.digest]
            if len(raw) != selected.size or sha(raw) != selected.digest:
                raise ValueError()
        config, layers = manifest_content(raw)
        if config.digest != image.config_id or len(layers) != len(
            image.config["rootfs"]["diff_ids"]
        ):
            raise ValueError()
    except (IndexError, ValueError, KeyError, TypeError, AttributeError):
        raise RemoteError(Code.IMAGE) from None
