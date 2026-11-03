"""Source gate cannot confuse PR-head checks with a reviewed merged commit."""

import pytest
from tools.deploy.release_manifest import ReleaseError
from tools.deploy.release_trust import approved_merge, provenance


def reviewed_pr():
    return {
        "number": 44,
        "merged_at": "2026-11-03T00:00:00Z",
        "merge_commit_sha": "a" * 40,
        "base": {"ref": "develop", "repo": {"full_name": "owner/repo"}},
        "head": {"sha": "b" * 40},
        "user": {"login": "author"},
    }


def approval():
    return {
        "state": "APPROVED",
        "commit_id": "b" * 40,
        "user": {"login": "reviewer"},
        "author_association": "MEMBER",
    }


def test_approved_merge_requires_latest_review_of_actual_head(monkeypatch):
    pr, review = reviewed_pr(), approval()
    monkeypatch.setattr(
        "tools.deploy.release_trust.github_list",
        lambda repo, resource: [pr] if resource.startswith("commits/") else [review],
    )
    assert approved_merge("owner/repo", "a" * 40) == (44, "b" * 40)
    for change in (
        {"commit_id": "c" * 40},
        {"state": "DISMISSED"},
        {"state": "CHANGES_REQUESTED"},
        {"author_association": "CONTRIBUTOR"},
        {"user": {"login": "author"}},
    ):
        review.clear()
        review.update(approval() | change)
        with pytest.raises(ReleaseError, match="source-not-reviewed"):
            approved_merge("owner/repo", "a" * 40)


@pytest.mark.parametrize(
    "ref,event",
    [("refs/heads/feature/x", "workflow_dispatch"), ("refs/heads/develop", "pull_request")],
)
def test_pr_or_arbitrary_ref_cannot_enter_trusted_path(monkeypatch, ref, event):
    for name, value in {
        "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_RUN_ID": "1",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_SHA": "a" * 40,
        "GITHUB_EVENT_NAME": event,
        "GITHUB_REF": ref,
        "GITHUB_WORKFLOW_REF": "owner/repo/.github/workflows/release.yml@" + ref,
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr("tools.deploy.release_trust.command", lambda *args: b"a" * 40)
    with pytest.raises(ReleaseError, match="untrusted-source"):
        provenance(trusted=True)
