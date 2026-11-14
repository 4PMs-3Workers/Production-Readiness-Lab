"""Baseline RED contract for the account soft-delete lifecycle."""

from app.services import admin_accounts


def test_soft_delete_transition_contract_exists():
    """The baseline must fail until the lifecycle operation is implemented."""
    assert hasattr(admin_accounts, "mark_account_deleted")
