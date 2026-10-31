from cybotech.services.policy_gateway.service import PolicyGateway

from .conftest import completed_finding, report_action


def test_sensitive_action_requires_an_approval(db):
    decision = PolicyGateway(db).authorize(report_action(), sentinel_finding=completed_finding())
    assert decision.failed_check == "approval_required"
