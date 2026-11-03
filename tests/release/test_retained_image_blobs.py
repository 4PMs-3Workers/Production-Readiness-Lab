"""Real OCI-client regression: metadata survives but a retained layer can be lost."""

import gzip
import io
import tarfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import cast
from urllib.parse import unquote, urlsplit

import pytest
from tests.backend.unit.test_release_manifest import spec_data
from tools.deploy.release_images import OCI_MANIFEST, Registry
from tools.deploy.release_manifest import (
    CHECKS,
    Manifest,
    ReleaseError,
    Spec,
    canonical,
    parse,
    proof,
    sha256,
)
from tools.deploy.release_retention import Roots, check_roots


class ImageRegistry(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, failure: str):
        layer_tar = io.BytesIO()
        with tarfile.open(fileobj=layer_tar, mode="w"):
            pass
        layer = gzip.compress(layer_tar.getvalue(), mtime=0)
        config = canonical(
            {
                "os": "linux",
                "architecture": "amd64",
                "config": {"Labels": {"org.opencontainers.image.revision": "a" * 40}},
                "rootfs": {"type": "layers", "diff_ids": [sha256(layer_tar.getvalue())]},
            }
        )
        self.layer_digest = sha256(layer)
        self.config_digest = sha256(config)
        self.manifest = canonical(
            {
                "schemaVersion": 2,
                "mediaType": OCI_MANIFEST,
                "config": {
                    "digest": self.config_digest,
                    "size": len(config),
                    "mediaType": "application/vnd.oci.image.config.v1+json",
                },
                "layers": [
                    {
                        "digest": self.layer_digest,
                        "size": len(layer),
                        "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
                    }
                ],
            }
        )
        self.blobs = {self.config_digest: config, self.layer_digest: layer}
        if failure == "missing":
            self.blobs.pop(self.layer_digest)
        elif failure == "corrupt":
            self.blobs[self.layer_digest] = b"x" * len(layer)
        self.requests: list[tuple[str, str]] = []
        super().__init__(("127.0.0.1", 0), ImageHandler)


class ImageHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.serve(send_body=True)

    def do_HEAD(self):
        self.serve(send_body=False)

    def serve(self, *, send_body: bool):
        fixture = cast(ImageRegistry, self.server)
        path = unquote(urlsplit(self.path).path)
        fixture.requests.append((self.command, path))
        status, media, digest = 200, "application/json", None
        if path.rstrip("/") == "/v2":
            raw = b"{}"
        elif path == "/v2/image/manifests/" + sha256(fixture.manifest):
            raw, media, digest = fixture.manifest, OCI_MANIFEST, sha256(fixture.manifest)
        elif path.startswith("/v2/image/blobs/") and path.rsplit("/", 1)[1] in fixture.blobs:
            digest = path.rsplit("/", 1)[1]
            raw, media = fixture.blobs[digest], "application/octet-stream"
        else:
            status, raw = (
                404,
                b'{"errors":[{"code":"BLOB_UNKNOWN","message":"fixture blob absent"}]}',
            )
        self.send_response(status)
        self.send_header("Docker-Distribution-Api-Version", "registry/2.0")
        self.send_header("Content-Type", media)
        self.send_header("Content-Length", str(len(raw)))
        if digest:
            self.send_header("Docker-Content-Digest", digest)
        self.end_headers()
        if send_body:
            self.wfile.write(raw)

    def log_message(self, format, *args):
        pass


@pytest.mark.parametrize("failure", ["healthy", "missing", "corrupt"])
def test_retention_checks_layers_even_when_metadata_is_available(monkeypatch, failure):
    # All review/proof metadata here is simulated, confined to this loopback fixture.
    with ImageRegistry(failure) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            data = spec_data()
            for image in data["images"].values():
                image.update(
                    reference=f"127.0.0.1:{server.server_port}/image@" + sha256(server.manifest),
                    image_id=server.config_digest,
                )
            spec = parse(canonical(data), Spec)
            retained = parse(
                canonical(
                    dict(
                        data,
                        format="prl-release",
                        candidate_proof=proof(spec, "candidate").model_dump(),
                        published_proof=proof(spec, "published").model_dump(),
                        candidate_artifact_id=456,
                        source_checks=sorted(CHECKS),
                    )
                ),
                Manifest,
            )
            registry = Registry(plain_http=True)
            root = f"127.0.0.1:{server.server_port}/release@sha256:" + "9" * 64
            monkeypatch.setattr(registry, "fetch", lambda reference: retained)
            roots = Roots(format_version=1, releases=[root])

            # Reproduce the review finding: inspecting the surviving metadata passes.
            registry.verify_images(retained)
            layer_request = ("GET", "/v2/image/blobs/" + server.layer_digest)
            assert layer_request not in server.requests
            if failure == "healthy":
                assert check_roots(spec, roots, registry) == {root: retained}
                # Each image gets a new archive, so one check cannot reuse a cached layer.
                assert server.requests.count(layer_request) >= len(spec.images)
            else:
                with pytest.raises(ReleaseError):
                    check_roots(spec, roots, registry)
                assert layer_request in server.requests
        finally:
            server.shutdown()
            thread.join(timeout=5)
