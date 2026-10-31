from cybotech.services.policy_gateway.service import PolicyGateway

from .conftest import report_action


def test_required_sentinel_review_is_a_precondition(db):
    decision = PolicyGateway(db).authorize(report_action(), sentinel_finding=None)
    assert (decision.authorized, decision.failed_check) == (False, "sentinel_review_required")
