# Dependency advisory policy

Pull requests and pushes run npm audit against package-lock.json and pip-audit against the fully pinned Python runtime and development requirements. The policy checker correlates npm advisory nodes with reachable package-lock entries, checks Python report inventory and versions against requirements.txt and requirements-dev.txt, and emits a machine-readable decision at build/security/decision.json.

The Python collector uses `--no-deps --disable-pip` with both requirements.txt and requirements-dev.txt supplied explicitly to audit every exact pin, including the runtime requirements normally reached through the development file's include. These files already contain the resolved runtime and development dependency inventory. This avoids the resolver's temporary environment omitting already-installed packages such as packaging from its dry-run report. The policy checker still rejects missing packages, unexpected packages, and version mismatches.

The checker deduplicates aliases that identify the same GHSA, PYSEC, or CVE for the same package, version, and exposure scope. It returns exit code 0 when the reports are complete and clean or every finding has an approved exact exception, 1 when an advisory remains unhandled, and 2 when a report or policy is incomplete, malformed, expired, or internally inconsistent. A failed audit collector is not treated as a clean report.

An exception must name one ecosystem, advisory ID, package, exact locked version, and exposure scope (runtime or development). It also needs an accountable owner, evidence describing the actual dependency and mitigation state, and a timezone-aware expiry no more than 30 calendar dates away, measured in the timestamp's timezone. Wildcards, package-wide or severity-wide ignores, duplicate entries, and automatic renewals are rejected. The checker refuses an exception if the audit data reports a fixed release. Exceptions that no longer match a finding are reported as unmatched and do not suppress any advisory.

The sole initial exception is for braces==3.0.3 in the development lint chain under GHSA-vfj7-8cjw-p6xm. It belongs to repository-maintainers and expires at 2026-11-18T23:59:59+07:00. Remove it once the locked dependency can be upgraded or the exception is no longer justified; do not extend its date automatically.

The CI job retains the raw npm audit, pip-audit, and decision JSON files for 14 days so that each gate result can be reviewed.
