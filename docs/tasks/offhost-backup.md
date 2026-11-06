# Verified encrypted off-host recovery

## Supported transport decision

The supported transport is the S3 object API over certificate-verified HTTPS,
using Boto3 1.43.125 and AWS Signature Version 4. The isolated acceptance store
is MinIO on a separate Docker network/storage lifecycle. This selects one adapter;
it does not add alternative cloud or filesystem transports.

Payload encryption uses the upstream **age 1.3.2** executable and an X25519 age
recipient. The uploader needs only the public recipient. Recovery requires the
corresponding private identity supplied separately on the recovery host. The
application snapshot implementation and its strict JSONL manifests remain intact.

This choice uses the project's available Docker/Linux CI, existing local backup
and recovery tools, immutable release manifests and backup monitoring contract.
No production endpoint, bucket, credentials, recovery identity or production
RPO/RTO target is implied by the lab. Operators must provide those inputs before
running a real job. Keep an independently accessible copy of recovery tooling,
store credentials, CA trust and the recovery identity outside the source host's
failure domain. Never commit or upload those secrets as CI artifacts.

## Transfer and recovery contract

- Accept only a complete, checksum-verified local artifact, with its expected
  source identity and a compatible immutable application release.
- Escrow the exact application images, migrations included in the backend image,
  release manifest and reviewed nonsecret configuration with the recovery point.
- Encrypt the complete private package before uploading. Check the downloaded
  remote ciphertext and package before conditionally publishing completion.
- Use bounded operations and backoff. Retry interrupted transfers from the start;
  an incomplete attempt is never a recovery point. Duplicate publication cannot
  overwrite a completed point.
- Retrieve into private staging, verify the package and original artifact, then
  atomically publish it to a separate recovery store. Restore and verify with the
  existing workflow; do not seed or migrate the recovered database.
- Scope remote retention to the configured environment/source. Preserve the last
  verified point, unrelated objects and all good local artifacts. Keep each
  retained point's application/configuration escrow alongside its backup.
- Report local integrity, remote-copy completion and application rehearsal as
  separate facts. Produce the remote-copy input consumed by backup monitoring.

The automated lab must make source services and source backup storage unavailable
to recovery, retrieve only from the HTTPS store, restore schema/rows and exercise
real gateway login/history/availability/book/replay/cancel/logout. Public evidence
contains identities, checks and observed timing only. A same-machine lab is an
automated failure simulation, not an actual off-host production measurement.

References: [age](https://github.com/FiloSottile/age),
[Boto3 S3](https://docs.aws.amazon.com/boto3/latest/reference/services/s3.html),
[S3 conditional writes](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html).

## Environment inputs and installation

Install the reviewed checkout on an operations/recovery host, independently of the
application host. Use Python 3.11/3.12, Docker with Linux/amd64 images and enough
private disk space for image escrow and transfer staging. Install the operational
dependencies and the checksum-pinned upstream age executables:

```sh
python -m venv .venv
.venv/bin/python -m pip install -r requirements-offhost.txt
.venv/bin/python -m pip install -e .
.venv/bin/python -m tools.backup.install_age --directory /opt/prl/age
```

The installer supports Linux/amd64 and Windows/amd64. Service recovery acceptance
runs on Linux. Boto3 dependencies are pinned in `requirements-offhost.txt`; they
are separate from the API/worker runtime requirements.

Create the private identity on the independent recovery host/vault, and copy only
its public recipient to the uploader configuration:

```sh
umask 077
/opt/prl/age/age-keygen --output /etc/prl-recovery/age-identity.txt
/opt/prl/age/age-keygen -y /etc/prl-recovery/age-identity.txt
```

Store the private identity, recovery credentials, CA trust, the reviewed tooling
and configuration in independently accessible recovery storage. The uploader
does not need the private identity. A private key stored only on the source host
cannot satisfy host-loss recovery.

Copy [the configuration template](../../tools/backup/offhost.config.example.json)
outside the checkout, replace every placeholder and set file mode 0600. The
example is intentionally unusable until real inputs are supplied. Configuration
contains the HTTPS endpoint, region, bucket, managed prefix, finite monitoring
labels, source hash and age recipient. `ca_file: null` uses system CA validation;
a private CA requires its absolute regular-file path. There is no insecure TLS
switch or implicit credential/provider fallback.

Credentials are a separate 0600 JSON file:

```json
{
  "access_key": "REPLACE_WITH_SCOPED_ACCESS_KEY",
  "secret_key": "REPLACE_WITH_SCOPED_SECRET_KEY",
  "session_token": null
}
```

The combined scheduled-job policy template is
[scoped to one environment/source](../../tools/backup/offhost-policy.example.json).
Apply equivalent provider permissions. A recovery-only account needs read/list,
and can omit put/delete. No operation needs bucket deletion or access to unrelated
prefixes. Credential rotation does not alter the recovery-point identity.

Obtain the source hash from an already verified local artifact without printing
database identifiers or payloads:

```python
from pathlib import Path
from app.backup.config import BackupConfig
from app.backup.storage import load_artifact
from tools.backup.offhost_contract import source_identity

config = BackupConfig("mysql+asyncmy://offline@source-unavailable/prl", Path("/srv/prl-backups"))
manifest = load_artifact(config, "REPLACE_WITH_COMPLETE_BACKUP_ID")
print(source_identity(manifest))
```

The hash binds the manifest's server UUID and database. Upload must also match the
exact source SHA/image ID and the release's schema/migration contract. A source
replacement needs a new explicit source identity; it cannot take over another
source's managed prefix.

Use an encrypted local filesystem for the original snapshot, image escrow and
decrypted staging, with directories 0700 and files 0600 owned by the job user.
Local snapshot bytes remain in the existing format; age encrypts the entire
remote package, including session/account data and application archives. This
tool does not provision or attest host disk encryption. Never place real backup
stores or key directories inside the repository, build contexts or public CI
artifact paths.

## Prepare and publish a recovery point

Set the nonsecret path variables from
[`offhost.env.example`](../../tools/backup/offhost.env.example). The uploader needs
config, scoped credentials, age executable, local store and a prepared escrow;
the private identity is needed only for retrieval.

Fetch the reviewed immutable release and ensure its exact images are available:

```sh
python -m tools.deploy.release fetch "$RELEASE_REFERENCE" --output /var/lib/prl/release.json
python -m tools.deploy.release verify-images /var/lib/prl/release.json --pull
python -m tools.backup.offhost prepare --release /var/lib/prl/release.json
python -m tools.backup.offhost upload --backup-id "$BACKUP_ID"
python -m tools.backup.offhost status
```

`PRL_OFFHOST_ESCROW` must name a fresh destination when preparing a release. Reuse
that immutable directory for subsequent compatible backups. Preparation verifies
the source labels, immutable image identity, actual migrations inside the backend
image and reviewed configuration bytes. It exports all application/vendor images
by ID. Neither upload nor restore builds replacement images. A retained point
contains its own full encrypted escrow; registry cleanup or loss of the source
image cache cannot remove that point's application bytes.

The remote layout is deliberately narrow:

```text
<prefix>/<environment>/<source-sha256>/points/<backup-id>/
  blobs/<ciphertext-sha256>.age
  complete.json
```

Only checksum-verified complete local artifacts enter packaging. File names,
sizes, hashes and source/release identity are verified before encryption. The
remote ciphertext is read back fully before `complete.json` is conditionally
created. Metadata is payload-free. Conditional creation prevents replacement;
duplicate/lost-ack retries verify and reuse the already committed point. Uploads
are single-object writes below S3's size limit, safely restarted from byte zero;
there is no partially visible multipart recovery point.

The configured maximum is at most 4 GiB of package input, plus bounded framing
overhead. Requests/retries/backoff share an overall operation deadline. The CLI
supervises a separate worker and terminates its owned process tree at that
deadline, including a hung HTTPS request. A failed transfer never deletes the
good local artifact and never becomes application-rehearsal success.

## Retrieve and recover independently

On a separate target, supply independent store credentials, the private age
identity, CA trust and reviewed tooling. Use a new private local store. Source
database credentials or a mounted source backup volume are not required.

```sh
python -m tools.backup.offhost retrieve --backup-id "$BACKUP_ID"
python -m tools.backup.offhost import-images --backup-id "$BACKUP_ID" \
  --output-overlay /etc/prl-recovery/recovered-images.yaml \
  --output-configuration /etc/prl-recovery/runtime-configuration
```

Retrieval always reads completion and ciphertext from S3 first. A good local copy
does not mask a missing/bad remote object or key. Decrypt into private staging,
reject unexpected/duplicate/link/traversal/oversized tar members, verify every
file, then run the original manifest/row checksum verifier. Escrow is prepared
with the immutable receipt before the original-format backup directory is
atomically published. A crash between these steps is retryable without replacing
a previous valid artifact.

The target store contains the original artifact at `<store>/<backup-id>` and
its verified dependencies under `<store>/.offhost-escrow/<backup-id>`. The latter
contains the release, exact image archives, reviewed `configuration/` tree and
`POINT.json`. `import-images` rechecks the receipt, source, release and archive
identities, loads those archives and can generate a Compose overlay selecting
only imported IDs with `pull_policy: never` and builds disabled. It never pulls
from a registry as a fallback.
`--output-configuration` publishes a separate working tree containing only the
verified nonsecret configuration, with 0755 directories/0644 files for read-only
mounts by service UIDs. MySQL bootstrap and Nginx must be able to read those files.
Keep the canonical backup/escrow private; do not widen its permissions. Use the
generated working tree for the recovered Compose files, and supply private target
environment files separately. The output must be a fresh path outside the backup
store; existing configuration is never overwritten.
The generated overlay uses the verified imported identity: a classic Docker store
may expose the archive's OCI config digest where a containerd-backed source used
a manifest/index ID. Only those recorded identities are accepted; an unrelated
same-source rebuild is not a substitute for the retained archive.

Use the retrieved Compose/configuration tree with the existing
[recovery runbook](mysql-recovery-runbook.md). Supply new target environment files
and a separate empty MySQL 8.4 database. Apply the generated image overlay last.
`DATABASE_URL` may identify the expected original database on an unavailable host;
the restore workflow connects only through `RESTORE_DATABASE_URL`. For example,
with the private environment already loaded:

```sh
export BACKUP_STORE="$PRL_OFFHOST_LOCAL_STORE"
python -m app.backup restore --backup-id "$BACKUP_ID" --target-database "$TARGET_DATABASE"
python -m app.backup verify --backup-id "$BACKUP_ID" --target-database "$TARGET_DATABASE"
```

Run schema/row verification before application smoke changes target data. Apply
the reviewed runtime grants, start the recovered gateway/API/worker/frontend and
perform login/history/availability/book/replay/cancel/replay/logout. Cancellation
requires the correct receipt plus independent detail and DB `CANCELLED` state.
Do not seed, migrate, overwrite or cut over a live database. Failed imports can
leave target DDL; quarantine that target under the existing restore contract.

## Scheduling, monitoring and retention

The optional `tools.backup.scheduled` wrapper runs the existing local scheduled
job, then copies its completed point and runs remote retention. Local backup,
local retention and remote outcomes remain separate. A local retention failure
after a completed snapshot does not prevent copying that good snapshot.

For this host invoker, the external Docker backup volume must explicitly bind
`PRL_OFFHOST_LOCAL_STORE`; the wrapper verifies the volume label, bind option and
exact resolved device path. Use a separate private filesystem directory owned
by UID10001. Keep it separate from the MySQL volume. Configure the existing local
backup job file and the extra nonsecret path file, then install
[`prl-offhost-backup.service`](../../tools/backup/systemd/prl-offhost-backup.service)
and its timer. Select this timer instead of running two overlapping local jobs.
Install the operational virtualenv at the unit's stated checkout path. Adjust
the unit account before enabling it: `prl-backup` must have UID10001 (or select an
existing UID10001 account in the unit), matching the backend backup image and bind
store owner. Give it Docker-group access and ownership/read access to its private
role/configuration/credential files, escrow and store directories. Install age as
that account, or grant that account execute/traverse access to the trusted binary
directory; the private identity remains on the independent recovery host. The
strict file-owner checks apply to the host invoker as well as container jobs.
Adjust
its schedule and total timeout for the chosen local/remote limits; the sample
daily schedule and retention values are examples, not selected production RPOs.

Monitoring reuses #46's state file and nonblocking state lock. Set exactly the
same finite `environment`/`store` labels in remote configuration and the exporter.
Mismatched/unbound input is `unknown`. Failed/pending transfers retain the last
verified point's timestamps without changing local success or rehearsal flags.

| Signal                                                   | Meaning                                             |
| -------------------------------------------------------- | --------------------------------------------------- |
| `prl_backup_remote_copy_state`                           | Configured copy is unknown/pending/success/failure. |
| `prl_backup_remote_copy_binding_valid`                   | Input belongs to the configured environment/store.  |
| `prl_backup_remote_copy_last_verified_timestamp_seconds` | Last verified remote completion.                    |
| `prl_backup_remote_recovery_point_timestamp_seconds`     | Conservative snapshot time of the verified point.   |
| `prl_backup_remote_copy_state_timestamp_seconds`         | Freshness of the remote status producer.            |

A fresh upload of old data is not a fresh recovery point. Use the snapshot metric
for data freshness, and the state/producer metrics for failed or missing jobs.
Configure thresholds from the environment's chosen RPO/schedule. Telemetry failure
does not participate in the backup lifecycle lock or prevent backup/recovery.

Remote retention verifies all recognized points before mutations, preserves the
newest verified point even if expired, then applies count/age rules oldest-first.
It conditionally changes a selected completion into a permanent typed `retired`
record before deleting only that record's exact immutable ciphertext blob. This
uses portable conditional PUT, not conditional DELETE. Retries resume cleanup of
retired points; duplicate uploads cannot resurrect their IDs. Unknown/corrupt
records stop deletion, and unrelated keys are untouched. Tombstones and incomplete
unreferenced upload attempts remain conservative metadata/orphans; they are not
eligible for broad prefix deletion. Inventory/deadline limits fail closed. Bucket
versioning/Object Lock may retain older provider versions; the tool never deletes
versions or buckets and does not claim physical erasure/capacity reclamation.

To rotate an age recipient, retain independent access to the old key/configuration
and use a new explicit managed prefix. Never remove the old recovery key while
its points remain retained. Keep the last verified old point until a new scope
has a verified recovery point. The tool does not silently reinterpret old points
under a different store/source/key identity.

## Required acceptance and evidence

The `Encrypted off-host recovery` workflow installs pinned age, builds its MinIO
fixture from upstream commit `9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a`, and requires:

```sh
RUN_OFFHOST_TESTS=1 python -m pytest tests/offhost -q --tb=short \
  --junitxml=build/offhost-tests.xml
```

Set `PRL_OFFHOST_AGE` and `PRL_OFFHOST_MINIO_BINARY` to the installed executables.
The real application case additionally requires Linux, Docker/Compose, Git,
Skopeo and ORAS from the existing release tooling. Missing prerequisites fail;
required integration cases never skip. Transport tests exercise real HTTPS/age,
interrupted/duplicate writes, failed completion, missing/wrong keys/artifacts,
checksum/identity failures, retention and unrelated-prefix protection. A real hung
HTTPS server checks process-level deadlines and owned staging cleanup.

The application case stops source services/database and the fixture registry,
quarantines the local export, retrieves into an empty target store and force-loads
the recovered image archives. It restores/verifies the separate database and
performs real recovered gateway business smoke. Only the source DB is restarted
after smoke for a read-only unchanged-data comparison. The source backup volume
is never mounted into the recovery project.

Only `build/offhost-review/rehearsal.json`, validated as `OffhostEvidence` version
2, is uploaded. It records checked-out source SHA and PR head separately, exact
build/image/configuration identities, selected point, isolation checks, incident/
recovery/smoke timestamps and observed RPO interval/RTO through full service smoke.
`timing.rto_service_seconds` uses the monotonic recovery-start-to-smoke clock;
`incident_to_smoke_wall_seconds` separately reports the recorded incident-to-smoke
UTC interval, including source isolation. These lab observations are not a chosen
production target or a measurement of provisioning replacement physical hosts.
Local recovery evidence version 1 remains readable and cannot be relabeled as
off-host proof. Credentials, keys, plaintext/ciphertext payloads, raw manifests,
application responses and private tool logs are excluded from CI artifacts.

This is an isolated automated Linux lab with simulated release-publication/review
metadata. It does not attest independently provisioned production hosts/storage,
fresh hardware or an operational recovery target. End-to-end incident/host-loss
timing is exercised by the incident-drill issue. PITR, replication, HA and live
database cutover remain outside this transport's scope.
