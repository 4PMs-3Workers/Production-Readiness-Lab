"""Fail-closed review gate for a dispatch on the protected develop branch."""

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from tools.deploy.release_images import command
from tools.deploy.release_manifest import Provenance, ReleaseError, canonical, parse


def github_list(repository: str, resource: str) -> list[dict]:
    results = []
    for page in range(1, 11):
        request = Request(
            f"https://api.github.com/repos/{repository}/{resource}?per_page=100&page={page}",
            headers={
                "Authorization": "Bearer " + os.environ.get("GITHUB_TOKEN", ""),
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urlopen(request, timeout=15) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise ValueError()
            items = json.loads(raw)
            if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
                raise ValueError()
        except (HTTPError, URLError, OSError, ValueError):
            raise ReleaseError("source-review-unavailable") from None
        results.extend(items)
        if len(items) < 100:
            return results
    raise ReleaseError("source-review-unavailable")


def approved_merge(repository: str, source: str) -> tuple[int, str]:
    for pr in github_list(repository, f"commits/{source}/pulls"):
        if (
            not pr.get("merged_at")
            or pr.get("merge_commit_sha") != source
            or pr.get("base", {}).get("ref") != "develop"
            or pr.get("base", {}).get("repo", {}).get("full_name") != repository
        ):
            continue
        head = pr["head"]["sha"]
        reviews = github_list(repository, f"pulls/{pr['number']}/reviews")
        effective = {}
        for review in reviews:
            if review.get("state") in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}:
                effective[review.get("user", {}).get("login")] = review
        approved = [
            review
            for actor, review in effective.items()
            if actor != pr.get("user", {}).get("login")
            and review.get("state") == "APPROVED"
            and review.get("commit_id") == head
            and review.get("author_association") in {"OWNER", "MEMBER", "COLLABORATOR"}
        ]
        if approved and not any(
            review.get("state") == "CHANGES_REQUESTED" for review in effective.values()
        ):
            return pr["number"], head
    raise ReleaseError("source-not-reviewed")


def require_clean_source(expected_sha: str) -> None:
    if command(["git", "rev-parse", "HEAD"]).decode().strip() != expected_sha or command(
        ["git", "status", "--porcelain", "--untracked-files=all"]
    ):
        raise ReleaseError("source-checkout-mismatch")


def provenance(*, trusted: bool) -> Provenance:
    source = command(["git", "rev-parse", "HEAD"]).decode().strip()
    require_clean_source(source)
    data = {
        "repository": os.environ["GITHUB_REPOSITORY"],
        "source_sha": source,
        "run_id": int(os.environ["GITHUB_RUN_ID"]),
        "run_attempt": int(os.environ["GITHUB_RUN_ATTEMPT"]),
        "pr_number": None,
        "pr_head_sha": None,
        "pr_merge_sha": None,
        "workflow_ref": os.environ["GITHUB_WORKFLOW_REF"],
    }
    # Validate repository/SHA before using them in an API URL.
    parse(canonical(data), Provenance)
    if source != os.environ["GITHUB_SHA"]:
        raise ReleaseError("source-checkout-mismatch")
    if trusted:
        if (
            os.environ["GITHUB_EVENT_NAME"] != "workflow_dispatch"
            or os.environ["GITHUB_REF"] != "refs/heads/develop"
        ):
            raise ReleaseError("untrusted-source")
        number, head = approved_merge(data["repository"], source)
        data.update(pr_number=number, pr_head_sha=head, pr_merge_sha=source)
    elif os.environ.get("PR_NUMBER"):
        data.update(pr_number=int(os.environ["PR_NUMBER"]), pr_head_sha=os.environ["PR_HEAD_SHA"])
    return parse(canonical(data), Provenance)
