# Reproducible booking load lab (Issue #53)

This tool provisions fictional booking data in a dedicated MySQL schema, drives
the declared candidate through its real HTTP gateway, verifies database effects,
and writes a bounded sanitized JSON report. It is an opt-in lab run; it does not
prove the seven-day availability SLO.

## Safety and prerequisites

- Use a disposable lab MySQL instance and a candidate gateway that is isolated
  from production. The database account must be able to create, migrate, seed,
  inspect, and remove only the run schema in that lab. The tool also refuses
  schemas outside `prl_load_<32 lowercase UUID hex>` and requires a persisted
  run ownership marker before cleanup.
- Use an HTTPS candidate origin. Plain HTTP is accepted only for a loopback lab
  target. The requested release SHA must equal both a clean local checkout `HEAD`
  and the gateway health event release.
- Provide Loki and Prometheus URLs that expose the candidate's gateway and
  existing worker/database signals. Keep credentials out of command arguments,
  reports, and terminal transcripts.
- Python 3.11 or newer with `requirements-dev.txt` installed. Commands below
  run from the repository root; set `PYTHONPATH=src/backend` so migrations and
  application models resolve.
- Choose a fresh UUID hex for each run. Never point this tool at a production
  endpoint or a schema containing non-lab data.

All write commands require the exact opt-in value
`I_UNDERSTAND_THIS_WRITES_TO_AN_ISOLATED_LAB_SCHEMA`. The tool checks arguments
and clean source identity before opening network or database connections. The
candidate API must be configured to use the newly created schema before the run can proceed; a
run-specific canary login and `/auth/me` identity check refuses a mismatched
database before workload writes.

## Configure and prepare the fixture

Set these values in a private shell environment or an access-controlled env
file. `PRL_LOAD_DATABASE_URL` must name the exact schema for this run. It is
used for schema lifecycle and post-run checks. The gateway's application runtime
must have its own least-privilege database configuration for that same schema.

```sh
export PYTHONPATH=src/backend
export PRL_LOAD_TEST_OPT_IN=I_UNDERSTAND_THIS_WRITES_TO_AN_ISOLATED_LAB_SCHEMA
export PRL_LOAD_DATABASE_URL='mysql+asyncmy://lab_operator:<url-encoded-password>@mysql-lab.example/prl_load_<run-id>'
export PRL_LOAD_LOKI_URL='https://loki-lab.example'
export PRL_LOAD_PROMETHEUS_URL='https://prometheus-lab.example'
export PRL_LOAD_FIXTURE_PASSWORD="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"

RUN_ID='<32 lowercase UUID hex characters>'
RELEASE_SHA="$(git rev-parse HEAD)"
TARGET_URL='https://booking-lab.example'
```

Provision the isolated schema, run current Alembic migrations, and create ten
fictional staff, forty fictional students, and 1,000 appointments (including
valid pending proposal and cancellation fixtures):

```sh
python -m tools.load.cli prepare \
  --target-url "$TARGET_URL" --release-sha "$RELEASE_SHA" --run-id "$RUN_ID"
```

Grant the candidate runtime account access only to this schema, then restart or
reconfigure the candidate API to use it. Preserve the exact same target URL,
release SHA, run ID, seed, and schema name for the run. Do not seed or migrate a
shared demo schema. Preserve the generated fixture password in a private secret
store for the run; it is random and cannot be derived from the public run ID.
Never put it in command arguments, reports, or Git.

## Run the integration acceptance

The short run below exercises real MySQL and gateway requests for one minute.
It still uses twenty authenticated sessions and the same read, booking,
cancellation, and proposal mix as the standard run:

```sh
python -m tools.load.cli run \
  --target-url "$TARGET_URL" --release-sha "$RELEASE_SHA" --run-id "$RUN_ID" \
  --duration-seconds 60 --report "artifacts/load-report-$RUN_ID.json"
```

For the standard observed window, omit `--duration-seconds` (default 600):

```sh
python -m tools.load.cli run \
  --target-url "$TARGET_URL" --release-sha "$RELEASE_SHA" --run-id "$RUN_ID" \
  --report "artifacts/load-report-$RUN_ID.json"
```

The runner uses real returned session cookies and refreshed CSRF tokens. Every
booking and proposal write has a unique idempotency key and is replayed with the
same request body. Booking and proposal receipts, cancellations, audit events,
notifications, overlap rules, the three-upcoming-booking limit, proposal state,
and duplicate idempotency keys are checked after the run. The runner counts its
outbound gateway requests; a Loki record-count mismatch makes the SLI window
incomplete rather than treating surviving records as a complete sample.

For provenance, create the containerized demo's passing evidence manifest at
`build/deploy/<release-sha>/manifest.json`; its `evidence` command records local
image IDs/digests and configuration identity. The report includes this manifest
only when it matches the clean checkout and passes strict schema validation.
The current gateway exposes the release source SHA but not its running image
digests, so the report explicitly marks target artifact identity as unverified.
An SLI run cannot claim complete acceptance until the target exposes a matching
runtime artifact attestation.

The command exits nonzero if an invariant fails, the load generator saturates,
gateway/runtime evidence is incomplete, target artifact identity is unverified,
or either observed-window target is missed.
Business/input exclusions follow the gateway's classification; unknown
classifications, absent telemetry, or too few samples produce insufficient
evidence. Availability uses success divided by success plus service failures.
Fast success is the share of successful requests completed within 500 ms. Both
ratios require at least twenty in-scope observations. Neither a short run nor a
ten-minute run establishes the seven-day SLO.

## Read the report and clean up

The JSON report is limited to 256 KiB and contains source/config digests,
local deployment manifest hash and its API/frontend/worker/gateway image IDs
and digests (when available), locked vendor image digests, dataset and workload counts, timestamps, route latency
percentiles, classified outcomes, gateway/runtime signals, generator-host
samples, invariant counts, and the observed-window verdict. It excludes account
identities, request/response bodies, cookies, tokens, appointment IDs, and
credentials. Target-host CPU/memory are marked unavailable because the current
candidate telemetry does not export those measurements; generator-host CPU and
memory samples are recorded when the host exposes them. Container limits remain
available in the hashed Compose configuration, not as utilization.

After saving the report and any evidence needed for review, explicitly remove
the owned schema:

```sh
python -m tools.load.cli cleanup \
  --target-url "$TARGET_URL" --release-sha "$RELEASE_SHA" --run-id "$RUN_ID"
```

Cleanup checks the exact schema name and persisted run ID before dropping it.
If any check fails, cleanup stops and leaves the database untouched. Retain or
delete the report according to the lab's evidence-retention policy.

## Local checks

Run focused tests and static checks from the checkout:

```sh
python -m pytest tests/load -q
ruff check tools/load tests/load
black --check tools/load tests/load
pyright tools/load tests/load
```

These unit tests do not replace the real MySQL/gateway acceptance command above.
