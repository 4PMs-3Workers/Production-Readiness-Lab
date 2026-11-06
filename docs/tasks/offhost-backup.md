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
