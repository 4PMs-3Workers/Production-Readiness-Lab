# Immutable application releases — issue #44

Release workflow: `.github/workflows/release.yml`. CLI: `python -m tools.deploy.release`.
Requires Python with the repository's locked dependencies, Docker/Compose with `!reset`
support, Skopeo and ORAS. CI pins Ubuntu 24.04 Skopeo
`1.13.3+ds1-2ubuntu0.24.04.3` and ORAS `1.3.6`.

## Artifact contract

`prl-candidate` v1 is a build/test input, not a published release. `prl-release` v1
adds candidate/published proofs, the successful source-check inventory and the
same-run Actions candidate artifact ID. `release-manifest.schema.json` is generated
from the strict runtime model; CI tests that it matches.

Each image records three different identities: registry manifest digest, Docker
image/config ID, and OCI archive SHA-256. Only `linux/amd64` is accepted. Frontend
and backend must carry the exact checkout SHA, while API, worker, schema, migration,
seed and optional backup all select the same backend image. The alert receiver
also uses backend; an observability release includes the source-built collector.
Vendor dependencies use the existing reviewed image lock.

Configuration identity covers the allowlisted public Compose/Dockerfile/config
files and the selected local/HTTPS/observability profile. It excludes secret env
files and host credentials. It is a reviewed template identity, not a hash of the
host's entire runtime environment. Host env still must satisfy backend validation
and the declared accepted schema; release rendering explicitly supplies that revision.
The existing evidence-v2 hash algorithm is unchanged. Old deployment v1/v2 and
backup manifests remain readable under their existing contracts, without being
accepted as release proof.

## Provisioning once, outside the repository

1. Protect `develop`: require reviewed PRs, current-head approval and required CI.
2. Create GitHub environment **`release-publish`**, restrict it to `develop`, require
   a reviewer, and prevent self-review/bypass according to the team's access policy.
3. In that environment set `RELEASE_REGISTRY_USERNAME` and secret
   `RELEASE_REGISTRY_TOKEN`, scoped to GHCR package publishing for this repository.
   No token goes into an example config, manifest, command argument or CI artifact.
   The workflow's default GitHub token has only contents/pull-request read permissions.
4. Grant the registry principal read/write access to
   `ghcr.io/<lowercase-owner>/<lowercase-repo>/{backend,frontend,collector,releases}`.
   Consumer hosts need only pull access. Registry account creation is an environment input.
5. Disable registry cleanup/TTL for published release tags and their images.
   Set environment variable `RELEASE_RETENTION_ROOTS` to JSON shaped like
   `tools/deploy/release-retention.example.json`, listing manifest digest references
   needed by retained backups. Keep this public reference inventory current locally;
   never upload private backup manifests to populate it.

The code checks a merged PR for the exact dispatch SHA, with an effective approval
of its actual head from a collaborator/member/owner other than the PR author, and
no effective changes-requested review. Missing/inaccessible review data fails closed.
Branch protection and environment policy must also be configured by repository admins.

## Run a release

Merge the reviewed implementation into `develop` before dispatch. GitHub must make
the workflow available for manual dispatch (normally requires its presence on the
repository default branch too). Use Actions UI or a configured GitHub CLI:

```bash
# Explicit first-release bootstrap; use previous_stable for every later release.
gh workflow run release.yml --ref develop \
  -f overlay=local -F observability=false -F bootstrap=true

# Subsequent release, referencing the actual previous stable manifest:
gh workflow run release.yml --ref develop \
  -f overlay=https -F observability=true -F bootstrap=false \
  -f previous_stable=ghcr.io/OWNER/REPO/releases@sha256:ACTUAL_DIGEST
```

Replace the uppercase placeholders with lowercase registry paths and a real 64-hex
digest. `release-config.example.json` is a nonsecret example of dispatch inputs.
There is no implicit choice of stable or `latest`; bootstrap must be explicit.

Dispatch reruns the five existing backend/frontend/security/container/recovery
workflows on the exact merged source. Their historical PR triggers remain.
PR-head SHA, checkout SHA and merged SHA are recorded separately. PR candidate jobs
never receive the environment's registry credential.

The candidate job exports each image once as OCI, checks every referenced blob,
copies it to a runner-owned loopback registry preserving digests, and runs real
Compose with `--no-build`. Archive checksums and proofs cross jobs in a same-run
artifact selected by numeric ID; download hash mismatches fail. Before publishing,
the clean runner rechecks source trust, candidate proof and archive identity.
Skopeo copies the same bytes to GHCR; no Docker build occurs in this job.

Published image digests are pulled into the fresh publishing runner's Docker daemon
and consumed by a new UUID-owned Compose project. Acceptance verifies API readiness,
schema, login UI, worker heartbeat and actual container image IDs/references.
Collector releases also check that the published collector starts its version command.
Core candidate smoke uses the local HTTP lab profile; the existing source checks
cover full HTTPS/telemetry behavior. These tests do not measure a host deployment,
SLO or rollback RTO.

Only after image tests and retention preflight pass is the release manifest uploaded
as an OCI artifact. The `published-release-<run>-<attempt>` report contains its exact
reference. `reference.txt` and the registry artifact are the completion outputs;
candidate artifacts alone do not demonstrate publication. A lost response can be
retried against identical content; an existing release ID with different bytes is
rejected. The workflow serializes its publishing runs; administrators must not run
competing writers against the same release tags.

## Retrieve and verify on a consumer

Authenticate using a protected external Docker/ORAS auth file. `REGISTRY_AUTH_FILE`
lets both OCI clients use that file. Docker needs matching pull credentials through
its normal configuration; do not put credentials in image URLs.

```bash
python -m tools.deploy.release fetch \
  ghcr.io/owner/repo/releases@sha256:ACTUAL_DIGEST --output /protected/release.json
python -m tools.deploy.release validate /protected/release.json
python -m tools.deploy.release verify-images /protected/release.json --pull
python -m tools.deploy.release render-compose /protected/release.json \
  --output /protected/release-overlay.yaml
python -m tools.deploy.release retention-check /protected/release.json \
  --roots /protected/release-retention.json
```

Use the configuration files from the manifest's reviewed source. Validation detects
configuration drift; it does not fetch or execute arbitrary source from the manifest.
`render-compose` verifies and pulls images, then emits a nonsecret final overlay:
all application builds are removed, references are digests and schema is explicit.
The existing external runtime/MySQL/migration/seed env-file prerequisites still apply.

Compose selection for #45 or a lab operator is `compose.yaml`, then the manifest's
local/HTTPS overlay, optional observability/backup overlay, and generated release
overlay **last**. Pass `--backup` when rendering for `compose.backup.yaml`; for
observability include its existing external Grafana/telemetry configuration too.
Check the resolved configuration for missing prerequisites before using `--no-build`.
This CLI deliberately does not run host migration, switch stable or orchestrate rollback.

## Retention and failure handling

All published releases and application images are retained. This issue implements
no registry garbage collection or delete command. Retention preflight resolves the
previous stable plus declared backup roots and verifies their exact image identities.
A checksum provides integrity relative to a trusted digest; it is not by itself a
signature or proof that an arbitrary third-party registry is trusted.

Legacy backups may only record local image ID/source SHA. Map them only when the
original image ID matches a known release; otherwise keep/export that original
image outside CI. A rebuild from the same SHA cannot fill a missing original artifact.
This CLI does not scan private backup payloads or change the #43 backup format.

Malformed input, missing artifacts, unsupported versions/platforms, schema/config
drift, wrong image identity and tool/network errors return nonzero with a static
reason code. Raw tool diagnostics, resolved env files and application payloads
are never printed by the release CLI. Interrupted image publishing may leave orphan
images, but cannot commit a complete release manifest or update stable. If the
manifest upload succeeds and the final readback fails, retry the same payload to
reconcile; do not invent a new stable state from the failed job result.

## Verification

```bash
python -m black --check tools/deploy/release*.py tests/backend/unit/test_release*.py tests/release
python -m ruff check tools/deploy/release*.py tests/backend/unit/test_release*.py tests/release
pyright
python -m pytest tests/backend/unit/test_release_manifest.py \
  tests/backend/unit/test_release_images.py tests/backend/unit/test_release_trust.py -q
```

For the real harness, supply `RELEASE_SPEC` (candidate JSON), `RELEASE_STAGE`
(`candidate`/`published`) and `RELEASE_PROOF` (output file). Candidate mode also sets
`RELEASE_LAB_REPOSITORY=127.0.0.1:5000/prl` after the workflow's archive import.
Then run `python -m pytest tests/release -q --junitxml=build/release-tests.xml`.
Missing prerequisites fail, without skips. The loopback test additionally exercises
manifest upload/fetch, identical retry, conflicting release ID and a missing digest
using explicitly simulated metadata; these protocol fixtures are never public
release/review evidence.

Keep #44 open until reviewed merge and the trusted publishing acceptance pass.
If environment credentials or branch policy are absent, report publishing as
unverified instead of treating the lab registry as a real release.
