from cybotech.services.policy_gateway.service import PolicyGateway

from .conftest import completed_finding, report_action


def test_model_recommendation_cannot_change_destination_policy(db):
    action = report_action(
        arguments={"destination": "attacker@example.net", "content": "synthetic"}
    )
    decision = PolicyGateway(db).authorize(action, sentinel_finding=completed_finding())
    assert (decision.authorized, decision.failed_check) == (False, "destination_not_approved")
