# CI integration follow-up - 09/11/2026

The report remains a snapshot of the original BT3 code/evidence at6184652 and the
student's measured source15526cb. All original commit permalinks and experiment
branches are retained. This follow-up is Codex-executed integration work, not a
new student load experiment or a change to the historical PASS/FAIL evidence.

## Failure reproduced

PR55 head6184652 was tested as synthetic merge808bc790 with develop92d9157.
Two jobs failed at the same guard: the reviewed predecessor could not be derived
because the generated gateway map now includes the export route. The unit test
failed at `previous is not None`; rollback rehearsal failed at
`upgraded.transition is not None`.

The gateway diff from develop consists of seven generated additions for
`GET /api/v1/appointments/export`; no existing mapping is removed. The MySQL grants
hash remains `8a7277032db2552dc45e9031dd0947d1b0aa363b0d1ebd445366225af3af038a`.
The route-map hash moves from the admin-only
`47b24ed0346691dbc949d958fe351f29af893727bbecdde172793358a83fd90c` to
`a21773dd60ab774b23b854523f51186637bc9a94e85d4e5488e4aec15280d7ee`.

## Change and validation

The isolated integration branch starts from the exact failed merge snapshot,
preserving both parent histories. The patch changes only the reviewed current
route-map hash and explains the additive export route. Predecessor hashes,
explicit opt-in, the exact-file allowlist, all other drift checks and runtime
deadlines remain enforced.

The existing opt-in/drift unit test now covers both reviewed files under the three
supported profiles: local, HTTPS, HTTPS with observability. Each case still
rejects unrelated configuration drift and an unreviewed third file version.
Local+observability is excluded because the release contract requires HTTPS.

Codex validation on Windows:

- Before the fix, the original failing unit case reproduced:1failed/0.52s.
- Release-manifest and rollout-CLI modules after the fix:66passed/6.50s.
- Related release/rollout manifest, CLI, preflight, identity, policy, controller,
  rollback and trust modules:98passed/13.92s.
- Generator check:41business operations; Black and Ruff pass for both changed files.

The required full Linux rollback rehearsal and backend jobs are verified by the
new PR55 CI run after publishing this patch; local unit results do not claim a
completed Docker rehearsal. No test is removed/skipped and no advisory/release
gate is disabled. This integration update does not merge PR55 into develop and
does not rebase or force-push any existing evidence commit.
