# Serialized deployment and verified rollback — issue #45

`python -m tools.deploy.rollout` consumes the delivered `prl-release` v1 contract
from [immutable releases](immutable-releases.md). It never builds an application
image or chooses a mutable tag. Frontend, API and worker change together; API and
worker select the same backend digest. This is a single-host procedure with a
service interruption, not a zero-downtime or HA deployment.

## Prepare the operator host

Use Linux, Docker/Compose with `!reset` support, Python with the locked repository
dependencies, Skopeo and ORAS. Use a clean checkout whose public configuration
matches the selected manifest. Keep **one canonical deployment checkout** for
`prl-demo` and run every lifecycle command from it. Preserve its `build/deploy`
directory across source updates; do not copy a release into a second checkout to
operate the same host. The OS lock and authoritative stable history live there.
Protect the checkout and state directory with operator-only filesystem access.

Prepare the existing external inputs described in the
[deployment foundation](containerized-demo.md):

- `PRL_DB_ENV_FILE`, `PRL_RUNTIME_ENV_FILE`, `PRL_MIGRATION_ENV_FILE` and
  `PRL_SEED_ENV_FILE`: absolute paths to private files, even when no seed runs.
- HTTPS: `PRL_TLS_CERT_FILE`, `PRL_TLS_KEY_FILE`, `PRL_HTTPS_ORIGIN` and the existing
  bind/port settings. Runtime origin, Secure cookies and gateway settings must
  agree. `local` uses `http://localhost:${PRL_HTTP_PORT:-8080}` for a local lab.
- `--observability` requires the existing operations TLS, Grafana credential-file,
  origin and CIDR inputs and an independently provisioned observability stack.
  The controller replaces application services and gateway; it does not bootstrap
  or assess the availability of every telemetry service.
- Protected registry pull authentication: `REGISTRY_AUTH_FILE` for Skopeo/ORAS,
  and Docker's normal matching credentials. Never put credentials in references.
- `PRL_SMOKE_CREDENTIALS_FILE`: an absolute path to a private JSON file shaped
  like [the nonsecret example](../../tools/deploy/rollout-smoke.example.json).
  Replace the placeholders outside Git. Supply a dedicated student lab account,
  an assigned purpose with `requires_free_text=false`, and a bookable calendar.
  Optional `staff_id` restricts the search to one staff UUID. The smoke creates
  and cancels a real appointment; reserve capacity for it and keep consultation
  text out of the fixture. Accounts, assignments and calendars must already exist;
  deployment does not run seed.
- Optional `PRL_SMOKE_CA_FILE`: absolute path to the private lab CA used to verify
  HTTPS. Certificate verification is enabled, including without this override.

The manifest's configuration hash covers allowlisted public files/profile. The
host binding additionally records the foundation's public host configuration
identity. Neither hash includes credentials. A changed host binding requires a
reviewed transition. Preserve the state and investigate any unexpected mismatch.

## Deploy, inspect and roll back

Use an actual retained manifest digest from the trusted publishing workflow:

```bash
python -m tools.deploy.rollout deploy \
  ghcr.io/owner/repo/releases@sha256:ACTUAL_64_HEX_DIGEST --overlay https
python -m tools.deploy.rollout status
python -m tools.deploy.rollout rollback --overlay https
```

Add `--observability` consistently with the manifest when applicable. The existing
entrypoint also accepts `python tools/deploy/demo.py deploy ...`, `rollback ...`
and `release-status`. `--plain-http` belongs before the subcommand and is only
for the owned loopback registry rehearsal, not a production registry.

The first manifest must explicitly declare bootstrap. Every later manifest must
name the current verified stable reference as `previous_stable`. The controller
fetches the strict manifest, verifies its immutable identity, image/config/source
identities, profile and schema compatibility, and checks the complete Compose
plan before changing running application services. All application builds are
removed from the generated final overlay; Compose uses `--no-build --pull never`.

### Upgrade a host to administrative booking configuration

Finish any pending operation on the base checkout first. Update the same checkout
and deploy the new manifest with the existing stable digest in `previous_stable`.
Keep the same Compose project, profile, ports, and origins. The reviewed transition
matches the exact old and new digests of `grants.sql` and `route-map.conf`, the
cached base manifest, and the original host binding. The controller updates that
binding together with the deploy operation after the candidate passes preflight.

The deployment sends the verified grant SQL to the running MySQL container and
recreates the gateway with the new routes. The added privileges and routes are
compatible with the base release. A rollback from the updated checkout restores
the base manifest's exact images while retaining the current host binding, grants,
gateway configuration, and MySQL volume, provided the persisted policy is
compatible. Preflight checks the saved service hours and local midnights using
the retained backend's timezone rules. It covers the booking horizon and a full
Gregorian cycle of recurring weekday rules beyond it. A nonexistent or repeated
boundary returns `rollback-incompatible` before replacing the running release.
The controller also rechecks the policy after draining the API and resumes the
same API container if the policy changed during preflight. The saved policy
remains in MySQL. An admin can explicitly save compatible hours and retry the
same rollback. The Linux rehearsal verifies the spring-forward availability
response on both images, the refusal with the incompatible policy in place,
and successful rollback after an admin saves a compatible policy.

The delivered schema contract accepts `20261113_1200_booking_quota`. Only the
initial bootstrap may start with an empty schema. Existing schemas require exactly
that revision, the expected table/column inventory, InnoDB and no unsupported
views, triggers, routines or events. Earlier, unknown, multiple-head and partial
states are refused. Adding a future migration requires a separately reviewed
compatibility contract; accepting a revision stamp alone is insufficient.

Deployment invokes the existing guarded `app.deploy` migration path and reviewed
grants. MySQL migration and backup retain their existing named lifecycle guard.
The MySQL socket health probe also requires networking to be enabled, rejecting
the temporary bootstrap server until initialization and identity setup finish.
Rollback runs no migration, seed or downgrade. Application replacement gracefully
stops the services and retains both the original MySQL container and its owned
data volume. There is no `down --volumes` or database cutover path.

## Lock, state and recovery decisions

`build/deploy/.lifecycle.lock` is one kernel lock shared with legacy demo lifecycle
commands, held through preflight, migration, rollout, readiness, smoke and final
state persistence. A competing deploy/rollback fails with `operation-locked`.
Once managed release state exists, legacy build/migrate/seed/up/stop/down commands
are refused so they cannot bypass stable verification. The migration/backup DB
guard remains a separate existing database-level safety mechanism.

`release-state.json` is the authoritative atomic state. Manifest cache and prior
operation snapshots are retained in the same private directory. Stable promotion
requires complete image, database-volume, schema, advancing worker and business
proofs; writing a pending phase never promotes a release. Legacy deployment
evidence v1/v2 remains readable and is not converted into release proof.

Retirement snapshots use `operations/<operation-id>/<canonical-state-sha256>.json`.
They are immutable observations prepared before replacing authoritative state.
If that replacement fails, the same operation may still receive a refusal or an
interruption marker; its next retirement writes a distinct snapshot. An existing
prepared snapshot never forces equality with a subsequently updated authoritative
record. Older flat `operations/<operation-id>.json` archives are retained and remain
non-authoritative.

| Situation                                                   | Behavior and operator action                                                                                                                                                                                               |
| ----------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Normal deploy                                               | `preflight → migration → rollout → readiness → smoke → succeeded`; only the final verified write promotes stable and retains the old stable.                                                                               |
| Rollout, readiness or smoke fails with a stable baseline    | The same operation and lock enter `rollback_preflight → rollback_rollout → rollback_readiness → rollback_smoke → rolled_back`. The original trigger remains recorded.                                                      |
| Failure before replacement, or bootstrap without a baseline | Record failure without promotion. Resolve the bounded reason and retry; schema safety checks still apply.                                                                                                                  |
| Explicit rollback after a successful deploy                 | Reverify the retained previous stable manifest and recover to it. A missing target is `rollback-unavailable`.                                                                                                              |
| Failed/interrupted deploy needing recovery                  | `rollback` restores the current known stable. An interrupted explicit rollback retries its recorded target.                                                                                                                |
| Process exits or host restarts mid-operation                | The OS releases the lock. The next lifecycle attempt marks pending state `interrupted`; stable remains unchanged. Inspect state before recovery.                                                                           |
| Retry an interrupted deploy before a rollback decision      | `deploy SAME_REFERENCE --overlay https --resume` reruns all checks. A different reference or a prior automatic rollback decision is refused; use `rollback` for recovery.                                                  |
| Rollback fails or exceeds its budget                        | Record `failed`, its original trigger and the final bounded reason; retain the existing stable reference. Correct the cause and explicitly retry rollback. A stable pointer alone does not assert a healthy running stack. |

`status` reads the last persisted state; it performs no live health check. Preserve
the state and manifest cache for investigation. Do not edit or delete pointers to
force success. A completed explicit rollback clears `previous_stable`, preventing
an accidental second rollback from selecting the release just rejected. Missing
images and configuration/schema incompatibility refuse the target before replacing
the applications.

An unresolved recovery intent survives preflight failures. For example, after
verified A → B and an interrupted C, rollback selects B. If B's image pull fails,
the next rollback retries **B**, including on a newly started controller; it does
not fall back again to A or report no target. Failed `deploy --resume` preflight
likewise retains the original selection and resume restrictions. Only complete
verified recovery clears `recovery_pending`.

CLI exit status is zero for a successful deploy or explicit rollback. A failed
deploy whose automatic recovery succeeds still exits **1**, with outcome
`rolled_back`: recovery does not make the attempted release successful. Failed
rollback also exits 1. Errors use allowlisted reason codes, without raw subprocess
output, account data, cookies or response payloads.

## Live verification

Readiness verifies selected frontend/API/worker image references, source labels
and local identities; API, frontend and gateway health; gateway live/ready/login;
and two advancing scan counters from the same worker container. An unchanged
heartbeat counter cannot satisfy worker progress.

Business smoke pages through the active catalog to find its configured purpose.
It searches at most the first 367 allowed booking dates and stops at
the first eligible slot. The school's configured horizon can extend beyond that
search window.

With observability enabled, replacement stops the gateway log rotator before its
namespace owner, recreates the gateway, then recreates the rotator. Readiness also
requires the pinned rotator image to be running in both PID and network namespaces
of the current gateway container. A process attached to a retired gateway cannot
satisfy verification. This applies to deployment and rollback alike.

Business smoke uses the gateway, real session cookies and signed CSRF. It covers
login, history/detail, purpose/staff/availability reads, booking, replay with the
same key and exact receipt, cancellation/replay, logout and subsequent protected
request rejection. Read-only database checks require exactly one appointment,
one audit and two notifications after book/replay, and one appointment, two audits
and four notifications after cancel/replay. No smoke payload is public evidence.
Cancellation additionally requires the strict response's correct identity,
`CANCELLED` status and timestamp, a matching persisted detail read, and an independent
read-only database status check. Equal responses and row counts alone cannot prove
cancellation.

## Private smoke transaction recovery

Host commands keep `build/deploy/private/smoke-transaction.json` separately from
public operation evidence. This bounded, atomic, mode-0600 file contains the
original idempotency key, non-free-text booking input and minimal creation receipt.
Its parent is private; no password, cookie or CSRF token is stored. The journal is
bound to the gateway origin and dedicated account identity. Preserve it across
source updates and process restarts along with the canonical host state. Do not
upload it, include it in CI artifacts, or delete it to bypass recovery.

Intent is durable before sending a booking request. If a response is lost or the
process exits after commit, recovery replays the original key and input to resolve
the receipt. If detail/effects/replay later fails, smoke first attempts to cancel
and verify its own transaction. An unhealthy candidate may not serve cancellation;
in that case the journal remains pending and automatic rollback performs cleanup
through the restored release **before any fresh smoke booking**. Previously
committed application bookings are never used as cleanup targets.

Use the original dedicated account credentials when retrying recovery. Cleanup
must verify cancellation, replay effects and persisted status before clearing the
journal. If cleanup cannot be established, the operation reports
`smoke-cleanup-failed`, keeps its private intent and creates no additional booking.
Correct the gateway/account/database cause and retry the recorded rollback target;
the stable pointer remains unchanged on failure. Bootstrap without a known stable
release may require reviewed operator intervention; retain its private transaction
evidence rather than discarding an unresolved booking.

## Recovery clock and identities

The rollback budget is **300 seconds from the persisted rollback decision** to
complete readiness and business smoke, including registry, Compose and HTTP work.
Normal deployment has a separate 600-second budget. Every rollback stage shares
the same remaining deadline; individual timeouts do not restart the clock. UTC
phase timestamps accompany monotonic elapsed measurements. A crash interrupts
that measurement; a retry records a new decision instead of claiming continuous
timing across a reboot.

Selections record source SHA, manifest reference/payload hash, configuration hash,
image references and OCI config digests. Manifest `image_id` remains the OCI config
digest on every Docker version. Classic Docker local IDs match it directly; when
Docker exposes a selected single-manifest digest as its local ID, verification
also requires the exact descriptor digest/media type already checked against the
remote config. An index ID or unrelated descriptor cannot substitute for it.

## Required isolated Linux rehearsal

Run from a clean Linux checkout with locked dependencies, Git, Docker/Compose,
Buildx, Skopeo and ORAS available. Missing prerequisites fail; there is no skip:

```bash
RUN_ROLLOUT_REHEARSAL=1 NEXT_TELEMETRY_DISABLED=1 \
  python -m pytest tests/rollout -q --tb=short --junitxml=build/rollback-tests.xml
# Equivalent callable entrypoint for a later incident-drill harness:
python -m tools.deploy.rollout_rehearsal
```

Python callers can use `rehearse(Path("build/rollback-review"))` from
`tools.deploy.rollout_rehearsal`. It returns the validated report and also writes
`rehearsal.json`; failure writes sanitized `failure.json` when state is available.

The harness owns a UUID registry, builder, project, network and data volume. It
builds source-labelled OCI images once, publishes a stable fixture plus a known
bad backend whose real readiness/login/booking/replay work while detail and
cancellation return errors, and uses the real controller for automatic recovery.
The candidate therefore commits a real smoke booking which only the restored
release can cancel and verify before its own smoke. One real booking commits before
deployment and one during its rollout phase, before graceful quiescence. The
same actor session reads/replays both after recovery and verifies exact audit/
notification counts. A separate process attempts a competing rollback during
deployment; real missing-image and configuration-drift preflights must also fail
without changing stable or its running containers. Cleanup verifies ownership
and preserves any existing `prl-demo` resources.

The required suite also holds MySQL inside bootstrap to verify its health boundary
and runs a pinned-Nginx component scenario which replaces the gateway twice,
verifies the rotator's new namespaces, and observes actual log rotation/reopening.
Each fixture owns its project/storage and verifies ownership before cleanup; none
uses the host deployment's MySQL volume.

`prl-rollback-lab` v1 requires an outcome, exact checkout/PR-head SHA, artifact and
configuration identities, decision/readiness/smoke times, recovery duration and
all preservation/replay/contention assertions. **Fixture review/publication
metadata is simulated** and explicitly marked. This automated Linux Compose lab
proof is not a trusted registry publication, actual host deployment measurement,
production RTO/SLO, or independent outside-host probe result.

The [rollback workflow](../../.github/workflows/release-rollback.yml) requires this
rehearsal for PRs and produces only the strict allowlisted report. Trusted manual
release publication additionally waits for this reusable check. Private env files,
credentials, registry archives, application responses and raw logs are not uploaded.

Affected local checks:

```bash
python -m black --check tools/deploy/rollout*.py tests/rollout \
  tests/backend/unit/test_rollout*.py tests/backend/unit/rollout_fixtures.py \
  tests/backend/unit/smoke_fixtures.py
python -m ruff check tools/deploy/rollout*.py tests/rollout \
  tests/backend/unit/test_rollout*.py tests/backend/unit/rollout_fixtures.py \
  tests/backend/unit/smoke_fixtures.py
pyright
python -m pytest tests/backend/unit -q
```

Backend MySQL, existing container/recovery, immutable-release candidate, frontend
and security checks remain required independently. Keep the issue open until
review, merge and acceptance are complete.
