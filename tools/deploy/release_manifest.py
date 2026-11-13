"""Versioned release contracts, separate from historical deployment evidence."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal, Self, TypeAlias, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "20261113_1200_booking_quota"
MAX_JSON = 1024 * 1024
SHA: TypeAlias = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
Hex: TypeAlias = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Digest: TypeAlias = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
Repository: TypeAlias = Annotated[
    str, Field(pattern=r"^[a-z0-9][a-z0-9.-]*(?::[0-9]{1,5})?(?:/[a-z0-9][a-z0-9._-]*)+$")
]
Reference: TypeAlias = Annotated[
    str,
    Field(
        pattern=r"^[a-z0-9][a-z0-9.-]*(?::[0-9]{1,5})?"
        r"(?:/[a-z0-9][a-z0-9._-]*)+@sha256:[0-9a-f]{64}$"
    ),
]
Positive: TypeAlias = Annotated[int, Field(strict=True, gt=0)]
IMAGE_NAMES = {"backend", "frontend", "collector"}
CHECKS = {"backend", "frontend", "security", "containers", "recovery"}
BACKEND_SERVICES = {"api", "worker", "schema", "migrate", "seed", "backup"}


class ReleaseError(Exception):
    """Only static codes cross the CLI boundary; never include tool/input text."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def schema_revision(root: Path = ROOT) -> str:
    """Return the exact schema pin declared by the selected source tree."""
    if root.resolve() == ROOT.resolve():
        return SCHEMA
    try:
        module = ast.parse((root / "tools/deploy/release_manifest.py").read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        raise ReleaseError("incompatible-schema") from None
    for node in module.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "SCHEMA" for target in node.targets
        ):
            if isinstance(node.value, ast.Constant) and type(node.value.value) is str:
                return node.value.value
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "SCHEMA"
            and isinstance(node.value, ast.Constant)
            and type(node.value.value) is str
        ):
            return node.value.value
    raise ReleaseError("incompatible-schema")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    @model_validator(mode="before")
    @classmethod
    def strict_versions(cls, data):
        if isinstance(data, dict) and any(
            key in data and type(data[key]) is not int
            for key in ("format_version", "contract_version")
        ):
            raise ValueError()
        return data


def canonical(value: BaseModel | dict) -> bytes:
    data = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    return json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def file_sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError()
        value[key] = item
    return value


def _nonfinite(_):
    raise ValueError()


def load_json(raw: bytes) -> dict:
    try:
        if len(raw) > MAX_JSON:
            raise ValueError()
        value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_nonfinite)
        if not isinstance(value, dict):
            raise ValueError()
        # Also reject non-finite constants nested inside otherwise optional fields.
        canonical(value)
        return value
    except (ValueError, TypeError, UnicodeError):
        raise ReleaseError("invalid-json") from None


T = TypeVar("T", bound=Strict)


def parse(raw: bytes, model: type[T]) -> T:
    load_json(raw)
    try:
        return model.model_validate_json(raw)
    except ValueError:
        raise ReleaseError("invalid-contract") from None


def read(path: Path, model: type[T]) -> T:
    try:
        with path.open("rb") as handle:
            return parse(handle.read(MAX_JSON + 1), model)
    except OSError:
        raise ReleaseError("missing-artifact") from None


def write(path: Path, value: Strict) -> None:
    raw = canonical(value)
    parse(raw, type(value))
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".release-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Provenance(Strict):
    repository: Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")]
    source_sha: SHA
    pr_number: Positive | None
    pr_head_sha: SHA | None
    pr_merge_sha: SHA | None
    run_id: Positive
    run_attempt: Positive
    workflow_ref: Annotated[
        str,
        Field(
            pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/\.github/workflows/release\.yml@refs/[A-Za-z0-9_./-]+$"
        ),
    ]

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if (self.pr_number is None) != (self.pr_head_sha is None):
            raise ValueError()
        if self.pr_merge_sha is not None and self.pr_number is None:
            raise ValueError()
        return self


class RegistryPath(Strict):
    repository: Repository


class RegistryReference(Strict):
    reference: Reference


class Image(Strict):
    reference: Reference
    image_id: Digest
    archive_sha256: Hex
    platform: Literal["linux/amd64"]

    @property
    def digest(self) -> str:
        return self.reference.rsplit("@", 1)[1]


class FileIdentity(Strict):
    path: Annotated[str, Field(pattern=r"^(?:tools|src|Dockerfile)[A-Za-z0-9_./-]*$")]
    sha256: Hex


class Configuration(Strict):
    contract_version: Literal[1]
    overlay: Literal["local", "https"]
    observability: bool
    files: list[FileIdentity] = Field(min_length=1, max_length=128)
    sha256: Hex

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.observability and self.overlay != "https":
            raise ValueError()
        paths = [item.path for item in self.files]
        if paths != sorted(set(paths)):
            raise ValueError()
        if (
            hashlib.sha256(canonical(self.model_dump(exclude={"sha256"}))).hexdigest()
            != self.sha256
        ):
            raise ValueError()
        return self


def configuration(overlay: str, observability: bool, root: Path = ROOT) -> Configuration:
    # Reuse the reviewed file inventory, without changing evidence-v2 hashing.
    from tools.deploy.demo import CONFIG_COMMON, CONFIG_HTTPS, CONFIG_OBSERVABILITY

    paths = list(CONFIG_COMMON) + [
        "tools/deploy/compose.release.yaml",
        "tools/infra/images.lock.json",
        "tools/deploy/compose.backup.yaml",
        "tools/deploy/compose.recovery.yaml",
        # Both gateway profiles mount this shared include/entrypoint inventory.
        # Keep the allowlist explicit so secret or unrelated files are not hashed.
        "tools/deploy/nginx/nginx.conf",
        "tools/deploy/nginx/gateway-entrypoint.sh",
        "tools/deploy/nginx/routes.conf",
        "tools/deploy/nginx/proxy.conf",
        "tools/deploy/nginx/route-map.conf",
        "tools/deploy/nginx/file-sink.conf",
        "tools/deploy/nginx/error-input.conf",
    ]
    paths += (
        ["tools/deploy/compose.local.yaml", "tools/deploy/nginx/local.conf"]
        if overlay == "local"
        else list(CONFIG_HTTPS)
    )
    if observability:
        paths += list(CONFIG_OBSERVABILITY)
    data = {
        "contract_version": 1,
        "overlay": overlay,
        "observability": observability,
        "files": [
            {"path": path, "sha256": file_sha256(root / path)} for path in sorted(set(paths))
        ],
    }
    data["sha256"] = hashlib.sha256(canonical(data)).hexdigest()
    return parse(canonical(data), Configuration)


GRANTS_PATH = "tools/deploy/mysql/grants.sql"

# Issue #50 adds routes and runtime privileges without removing the base release's
# routes or grants. Issue #53 adds a read-only public attestation mount and health
# endpoint without changing the existing host's deployment state. These exact file
# identities let the reviewed upgrade preserve and later restore that predecessor.
ADMIN_POLICY_TRANSITION = {
    GRANTS_PATH: (
        "6350525d0dace2a66459f486606e2f45a1d84e48cc673e0ab1a38f8e3c49a926",
        "bdede7d278ee16b114ed2da85ac9e350cfdacec1aa0b4f125237b2765229f3a3",
    ),
    "tools/deploy/nginx/route-map.conf": (
        "1c5c8eaa269404fff5f30bd8eecd5733768891b5226684bb5f867748b29ce5fd",
        "47b24ed0346691dbc949d958fe351f29af893727bbecdde172793358a83fd90c",
    ),
    "tools/deploy/compose.local.yaml": (
        "c6017399bdf443a3ac5e27d6e64cc6bec3e7fc1f038baf9e71332a9c99d2bf44",
        "5766741d8e44c72fc5487cd689ca658a6780e9f8cf804481df4f3a9a89bbb758",
    ),
    "tools/deploy/compose.https.yaml": (
        "319fdc5b8334164a1577bde4b63cbf2d730dc76dd99e54fc7cb735b2a703af4b",
        "0811f0b010706d59351e7f3a0c4951b9f788731913877c1d88a805be1c0ad305",
    ),
    "tools/deploy/nginx/nginx.conf": (
        "35220da5abdb389321cb47f8688286fc0d4e6a70ea54442db4d087998178248b",
        "dd8331ac797a1f5bfa5c05eb3a33af815fd188346058860d9e9e27e631343597",
    ),
    "tools/deploy/nginx/routes.conf": (
        "5e9f6e34f6911366c0c785f1ab62249e4bb58f9bcaed112c74f8637e1532e925",
        "ebfdcf0a4efcf8f13b8a9423e69ed251ea70a5ebc8193eefcf4d477b4ed5fc22",
    ),
}


def previous_configuration(current: Configuration) -> Configuration | None:
    hashes = {item.path: item.sha256 for item in current.files}
    transition = {
        path: versions for path, versions in ADMIN_POLICY_TRANSITION.items() if path in hashes
    }
    if not transition or any(hashes[path] != after for path, (_, after) in transition.items()):
        return None
    data = current.model_dump(exclude={"sha256"})
    for item in data["files"]:
        if item["path"] in transition:
            item["sha256"] = transition[item["path"]][0]
    data["sha256"] = hashlib.sha256(canonical(data)).hexdigest()
    return parse(canonical(data), Configuration)


def require_configuration(
    config: Configuration, root: Path = ROOT, *, allow_previous: bool = False
) -> None:
    current = configuration(config.overlay, config.observability, root)
    if config != current and (not allow_previous or config != previous_configuration(current)):
        raise ReleaseError("incompatible-configuration")


class Spec(Strict):
    format: Literal["prl-candidate"]
    format_version: Literal[1]
    release_id: Annotated[str, Field(pattern=r"^release-[1-9][0-9]*-[1-9][0-9]*$")]
    created_at: str
    provenance: Provenance
    images: dict[Literal["backend", "frontend", "collector"], Image]
    services: dict[str, Literal["backend", "frontend", "collector"]]
    vendor_images: dict[str, Reference]
    accepted_schema_revisions: list[Annotated[str, Field(pattern=r"^[A-Za-z0-9_]{1,64}$")]] = Field(
        min_length=1, max_length=32
    )
    configuration: Configuration
    previous_stable: Reference | None
    bootstrap: bool

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if type(self.format_version) is not int:
            raise ValueError()
        instant = datetime.fromisoformat(self.created_at.replace("Z", "+00:00"))
        if not self.created_at.endswith("Z") or instant.tzinfo != UTC:
            raise ValueError()
        if self.release_id != f"release-{self.provenance.run_id}-{self.provenance.run_attempt}":
            raise ValueError()
        if self.accepted_schema_revisions != sorted(set(self.accepted_schema_revisions)):
            raise ValueError()
        if self.bootstrap != (self.previous_stable is None):
            raise ValueError()
        expected_images = {"backend", "frontend"}
        expected_services = dict.fromkeys(sorted(BACKEND_SERVICES), "backend")
        expected_services["frontend"] = "frontend"
        expected_vendor = {"mysql", "gateway"}
        if self.configuration.observability:
            expected_images.add("collector")
            expected_services.update(collector="collector", local_alert_receiver="backend")
            expected_vendor |= {"prometheus", "loki", "tempo", "grafana", "alertmanager"}
        if (
            set(self.images) != expected_images
            or self.services != expected_services
            or set(self.vendor_images) != expected_vendor
        ):
            raise ValueError()
        return self


def vendor_inventory(observability: bool, root: Path = ROOT) -> dict[str, str]:
    names = {"mysql", "gateway"}
    if observability:
        names |= {"prometheus", "loki", "tempo", "grafana", "alertmanager"}
    lock = load_json((root / "tools/infra/images.lock.json").read_bytes())["images"]
    result = {}
    for name in sorted(names):
        image, digest = lock[name].rsplit("@", 1)
        # Normalize Docker Hub shorthand and remove descriptive tags; digest is identity.
        last = image.rsplit("/", 1)[-1]
        if ":" in last:
            image = image.rsplit(":", 1)[0]
        first = image.split("/", 1)[0]
        if "/" not in image:
            image = "docker.io/library/" + image
        elif "." not in first and ":" not in first and first != "localhost":
            image = "docker.io/" + image
        result[name] = RegistryReference(reference=image + "@" + digest).reference
    return result


def require_compatible(spec: Spec, root: Path = ROOT, *, allow_previous: bool = False) -> None:
    require_configuration(spec.configuration, root, allow_previous=allow_previous)
    if spec.accepted_schema_revisions != [schema_revision(root)]:
        raise ReleaseError("incompatible-schema")
    if spec.vendor_images != vendor_inventory(spec.configuration.observability, root):
        raise ReleaseError("vendor-inventory-mismatch")


class Proof(Strict):
    stage: Literal["candidate", "published"]
    provenance: Provenance
    configuration_sha256: Hex
    image_digests: dict[str, Digest]
    image_ids: dict[str, Digest]
    archive_sha256: dict[str, Hex]

    def require_spec(self, spec: Spec) -> None:
        if (
            self.provenance != spec.provenance
            or self.configuration_sha256 != spec.configuration.sha256
            or self.image_digests != {key: image.digest for key, image in spec.images.items()}
            or self.image_ids != {key: image.image_id for key, image in spec.images.items()}
            or self.archive_sha256
            != {key: image.archive_sha256 for key, image in spec.images.items()}
        ):
            raise ReleaseError("candidate-mismatch")


class Manifest(Spec):
    format: Literal["prl-release"]  # type: ignore[override]
    candidate_proof: Proof
    published_proof: Proof
    source_checks: list[Literal["backend", "frontend", "security", "containers", "recovery"]]
    candidate_artifact_id: Positive

    @model_validator(mode="after")
    def verified(self) -> Self:
        if (
            self.source_checks != sorted(CHECKS)
            or self.candidate_proof.stage != "candidate"
            or self.published_proof.stage != "published"
            or self.provenance.pr_merge_sha != self.provenance.source_sha
            or self.provenance.workflow_ref
            != self.provenance.repository + "/.github/workflows/release.yml@refs/heads/develop"
        ):
            raise ValueError()
        self.candidate_proof.require_spec(self)
        self.published_proof.require_spec(self)
        return self


def proof(spec: Spec, stage: Literal["candidate", "published"]) -> Proof:
    return Proof(
        stage=stage,
        provenance=spec.provenance,
        configuration_sha256=spec.configuration.sha256,
        image_digests={key: image.digest for key, image in spec.images.items()},
        image_ids={key: image.image_id for key, image in spec.images.items()},
        archive_sha256={key: image.archive_sha256 for key, image in spec.images.items()},
    )
