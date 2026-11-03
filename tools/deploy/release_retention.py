"""Conservative retention: verify roots; deliberately no registry delete operation."""

from pydantic import Field
from tools.deploy.release_images import Registry
from tools.deploy.release_manifest import Manifest, Reference, ReleaseError, Spec, Strict


class Roots(Strict):
    format_version: int = Field(strict=True, ge=1, le=1)
    releases: list[Reference] = Field(max_length=1000)


def check_roots(spec: Spec, roots: Roots, registry: Registry) -> dict[str, Manifest]:
    references = set(roots.releases)
    if len(references) != len(roots.releases):
        raise ReleaseError("duplicate-retention-root")
    if spec.previous_stable is not None:
        references.add(spec.previous_stable)
    retained = {}
    for reference in sorted(references):
        manifest = registry.fetch(reference)
        # Historical releases may have different config/schema; preserve them as-is.
        # Their versioned parser still validates structure and identity coherence.
        registry.verify_images(manifest)
        retained[reference] = manifest
    return retained
