import pytest
from tests.backend.unit.test_rollout_runtime import manifest
from tools.deploy.release_images import OCI_MANIFEST, local_image_identity
from tools.deploy.release_manifest import ReleaseError


def test_classic_config_and_exact_containerd_manifest_both_bind_to_selected_artifact():
    image = manifest().images["backend"]
    # This fixture uses deliberately distinct config/manifest IDs.
    image = image.model_copy(update={"image_id": "sha256:" + "d" * 64})
    assert local_image_identity(image, dict(id=image.image_id)) == image.image_id
    actual = dict(id=image.digest, descriptor=dict(digest=image.digest, mediaType=OCI_MANIFEST))
    assert local_image_identity(image, actual) == image.digest


@pytest.mark.parametrize(
    "descriptor",
    [
        None,
        dict(digest="sha256:" + "f" * 64, mediaType=OCI_MANIFEST),
        dict(digest="sha256:" + "1" * 64, mediaType="application/vnd.oci.image.index.v1+json"),
    ],
)
def test_digest_alias_without_exact_single_manifest_descriptor_is_refused(descriptor):
    image = manifest().images["backend"].model_copy(update={"image_id": "sha256:" + "d" * 64})
    with pytest.raises(ReleaseError, match="image-id-mismatch"):
        local_image_identity(image, dict(id=image.digest, descriptor=descriptor))
