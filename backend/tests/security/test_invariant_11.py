from datetime import UTC, datetime, timedelta

from cybotech.services.policy_gateway.service import PolicyGateway

from .conftest import approved_report, completed_finding, report_action


def test_approval_is_action_bound_expiring_and_single_use(db):
    action, approval_id = approved_report(db)
    gateway = PolicyGateway(db)
    changed = report_action(
        arguments={"destination": "reports@internal.example", "content": "changed"}
    )
    assert (
        gateway.authorize(
            changed, sentinel_finding=completed_finding(), approval_id=approval_id
        ).failed_check
        == "approval_action_mismatch"
    )
    assert gateway.authorize(
        action, sentinel_finding=completed_finding(), approval_id=approval_id
    ).authorized
    assert (
        gateway.authorize(
            action, sentinel_finding=completed_finding(), approval_id=approval_id
        ).failed_check
        == "approval_already_consumed"
    )
    expired = gateway.create_approval(action, "reviewer", datetime.now(UTC) - timedelta(seconds=1))
    gateway.decide_approval(expired, "reviewer", True)
    assert (
        gateway.authorize(
            action, sentinel_finding=completed_finding(), approval_id=expired
        ).failed_check
        == "approval_missing_or_expired"
    )
