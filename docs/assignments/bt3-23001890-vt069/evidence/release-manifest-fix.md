# Release-manifest regression evidence

- Source before fix: implementation/test commit `bb34804de3dbc092233174e9c17639f3823e25c4`.
- Reproduction: `PYTHONUTF8=1`, `PYTHONPATH=src/backend`, Python 3.11.4; from repository root run `python -m pytest tests/backend/unit/test_release_manifest.py::test_reviewed_predecessor_requires_opt_in_and_exact_configuration -q`.
- RED: `1 failed in 0.26s`, exit 1. Failure at `assert previous is not None`; the current `tools/deploy/mysql/grants.sql` hash was `bdede7d278ee16b114ed2da85ac9e350cfdacec1aa0b4f125237b2765229f3a3`, while the transition table expected `8a7277032db2552dc45e9031dd0947d1b0aa363b0d1ebd445366225af3af038a`.
- Change: commit `fb24a3b` updates the current hash in the explicitly reviewed transition. The prior grants hash and exact checks for the other transition files remain unchanged.
- GREEN: same test command, `1 passed in 0.33s`, exit 0. Regression group `test_release_manifest.py test_rollout_schema.py test_rollout_preflight.py -q`: `75 passed in 4.19s`, exit 0.
- Conclusion: the original failure was a mismatch between the live reviewed file and an exact pinned transition, not a schema parser error. The update allows precisely the current reviewed grant version and preserves rejection of third/unreviewed variants.
