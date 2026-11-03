"""Immutable release CLI. Host deployment/stable promotion belong to issue #45."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from tools.deploy.release_images import Registry, archive_image
from tools.deploy.release_manifest import (
    CHECKS,
    SCHEMA,
    Manifest,
    Proof,
    Provenance,
    RegistryPath,
    ReleaseError,
    Spec,
    canonical,
    configuration,
    parse,
    read,
    require_compatible,
    vendor_inventory,
    write,
)
from tools.deploy.release_retention import Roots, check_roots
from tools.deploy.release_trust import provenance


def candidate(
    directory: Path,
    repository: str,
    source: Provenance,
    *,
    overlay: str,
    observability: bool,
    previous_stable: str | None,
    bootstrap: bool,
) -> Spec:
    RegistryPath(repository=repository)
    names = ["backend", "frontend"] + (["collector"] if observability else [])
    images = {
        name: archive_image(directory / f"{name}.tar", repository + "/" + name, source.source_sha)
        for name in names
    }
    services = dict.fromkeys(["api", "worker", "schema", "migrate", "seed", "backup"], "backend")
    services["frontend"] = "frontend"
    if observability:
        services.update(collector="collector", local_alert_receiver="backend")
    return parse(
        canonical(
            {
                "format": "prl-candidate",
                "format_version": 1,
                "release_id": f"release-{source.run_id}-{source.run_attempt}",
                "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "provenance": source.model_dump(),
                "images": {key: value.model_dump() for key, value in images.items()},
                "services": services,
                "vendor_images": vendor_inventory(observability),
                "accepted_schema_revisions": [SCHEMA],
                "configuration": configuration(overlay, observability).model_dump(),
                "previous_stable": previous_stable,
                "bootstrap": bootstrap,
            }
        ),
        Spec,
    )


def render(spec: Spec, *, backup: bool = False) -> bytes:
    require_compatible(spec)
    names = {name: spec.images[key].reference for name, key in spec.services.items()}
    if not backup:
        names.pop("backup")
    names.update(spec.vendor_images)
    # All interpolated values have strict reference/service-name schemas.
    lines = ["# Generated from a validated release; apply LAST.", "services:"]
    for name, reference in sorted(names.items()):
        lines += [f"  {name}:", f"    image: {reference}", "    platform: linux/amd64"]
        if name in spec.services:
            lines += ["    build: !reset null"]
        if name in {"api", "worker", "schema"}:
            lines += ["    environment:", f"      ACCEPTED_SCHEMA_REVISIONS: {SCHEMA}"]
    return ("\n".join(lines) + "\n").encode()


def publish(
    spec: Spec,
    directory: Path,
    candidate_proof: Proof,
    published_proof: Proof,
    roots: Roots,
    registry: Registry,
    manifest_repository: str,
    checks: list[str],
    candidate_artifact_id: int,
) -> str:
    require_compatible(spec)
    candidate_proof.require_spec(spec)
    published_proof.require_spec(spec)
    # The caller must have copied/tested images before invoking this final commit.
    # Recheck archives at the boundary rather than trusting a prior tool's success.
    for key, image in spec.images.items():
        if (
            archive_image(
                directory / f"{key}.tar",
                image.reference.split("@", 1)[0],
                spec.provenance.source_sha,
            )
            != image
        ):
            raise ReleaseError("candidate-mismatch")
    registry.verify_images(spec)
    check_roots(spec, roots, registry)
    manifest = parse(
        canonical(
            dict(
                spec.model_dump(),
                format="prl-release",
                candidate_proof=candidate_proof.model_dump(),
                published_proof=published_proof.model_dump(),
                source_checks=checks,
                candidate_artifact_id=candidate_artifact_id,
            )
        ),
        Manifest,
    )
    return registry.publish(manifest_repository, manifest)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ReleaseError("usage")


def parser() -> Parser:
    result = Parser(description=__doc__)
    result.add_argument("--plain-http", action="store_true", help="Loopback registry lab only")
    subs = result.add_subparsers(dest="command", required=True)
    gate = subs.add_parser("source")
    gate.add_argument("--trusted", action="store_true")
    gate.add_argument("--output", type=Path, required=True)
    build = subs.add_parser("candidate")
    build.add_argument("--directory", type=Path, required=True)
    build.add_argument("--repository", required=True)
    build.add_argument("--provenance", type=Path, required=True)
    build.add_argument("--overlay", choices=["local", "https"], default="local")
    build.add_argument("--observability", action="store_true")
    build.add_argument("--previous-stable")
    build.add_argument("--bootstrap", action="store_true")
    build.add_argument("--output", type=Path, required=True)
    for name in ("validate", "verify-images", "render-compose", "retention-check"):
        sub = subs.add_parser(name)
        sub.add_argument("manifest", type=Path)
        if name == "verify-images":
            sub.add_argument("--pull", action="store_true")
        if name == "render-compose":
            sub.add_argument("--output", type=Path, required=True)
            sub.add_argument("--backup", action="store_true")
        if name == "retention-check":
            sub.add_argument("--roots", type=Path, required=True)
    fetch = subs.add_parser("fetch")
    fetch.add_argument("reference")
    fetch.add_argument("--output", type=Path, required=True)
    copy = subs.add_parser("copy")
    copy.add_argument("spec", type=Path)
    copy.add_argument("--directory", type=Path, required=True)
    copy.add_argument("--repository", help="Relocate to a loopback lab registry")
    copy.add_argument("--proof", type=Path, help="Required candidate proof for public publishing")
    final = subs.add_parser("publish")
    for key in ("spec", "directory", "candidate-proof", "published-proof", "roots"):
        final.add_argument("--" + key, type=Path, required=True)
    final.add_argument("--repository", required=True)
    final.add_argument("--output", type=Path, required=True)
    final.add_argument("--checks", nargs="+", required=True)
    final.add_argument("--candidate-artifact-id", type=int, required=True)
    subs.add_parser("schema").add_argument("--output", type=Path, required=True)
    return result


def main(argv=None) -> int:
    try:
        args = parser().parse_args(argv)
        registry = Registry(plain_http=args.plain_http)
        if args.command == "source":
            write(args.output, provenance(trusted=args.trusted))
        elif args.command == "candidate":
            write(
                args.output,
                candidate(
                    args.directory,
                    args.repository,
                    read(args.provenance, Provenance),
                    overlay=args.overlay,
                    observability=args.observability,
                    previous_stable=args.previous_stable,
                    bootstrap=args.bootstrap,
                ),
            )
        elif args.command == "fetch":
            write(args.output, registry.fetch(args.reference))
        elif args.command == "copy":
            spec = read(args.spec, Spec)
            require_compatible(spec)
            if not args.repository:
                if args.proof is None or provenance(trusted=True) != spec.provenance:
                    raise ReleaseError("untrusted-source")
                candidate_proof = read(args.proof, Proof)
                if candidate_proof.stage != "candidate":
                    raise ReleaseError("candidate-mismatch")
                candidate_proof.require_spec(spec)
            for key, image in spec.images.items():
                if args.repository:
                    if not args.plain_http:
                        raise ReleaseError("unsafe-registry")
                    data = dict(
                        image.model_dump(),
                        reference=args.repository + "/" + key + "@" + image.digest,
                    )
                    image = type(image).model_validate(data)
                registry.copy_archive(
                    args.directory / f"{key}.tar",
                    image,
                    spec.provenance.source_sha,
                    spec.release_id,
                )
        elif args.command == "publish":
            # Re-evaluate trust on the publishing runner, not just in the build job.
            spec = read(args.spec, Spec)
            if provenance(trusted=True) != spec.provenance or set(args.checks) != CHECKS:
                raise ReleaseError("untrusted-source")
            RegistryPath(repository=args.repository)
            reference = publish(
                spec,
                args.directory,
                read(args.candidate_proof, Proof),
                read(args.published_proof, Proof),
                read(args.roots, Roots),
                registry,
                args.repository,
                args.checks,
                args.candidate_artifact_id,
            )
            args.output.write_text(reference + "\n", encoding="utf-8")
        elif args.command == "schema":
            args.output.write_bytes(canonical(Manifest.model_json_schema()))
        else:
            manifest = read(args.manifest, Manifest)
            if args.command in {"validate", "render-compose", "verify-images"}:
                require_compatible(manifest)
            if args.command == "verify-images":
                registry.verify_images(manifest, pull=args.pull)
            elif args.command == "render-compose":
                registry.verify_images(manifest, pull=True)
                args.output.write_bytes(render(manifest, backup=args.backup))
            elif args.command == "retention-check":
                check_roots(manifest, read(args.roots, Roots), registry)
        print(json.dumps({"operation": args.command, "success": True}))
        return 0
    except ReleaseError as error:
        print(json.dumps({"success": False, "reason": error.code}))
        return 1
    except (OSError, ValueError, KeyError, TypeError):
        print('{"success":false,"reason":"invalid-input"}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
