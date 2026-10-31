from cybotech.services.policy_gateway.service import PolicyGateway
from cybotech.services.sentinel.service import SentinelFinding

from .conftest import report_action


def test_sentinel_proceed_cannot_override_gateway_denial(db):
    finding = SentinelFinding(risk="low", rationale="Proceed", disposition="gateway_evaluation")
    action = report_action(
        arguments={"destination": "not-allowed@example.net", "content": "synthetic"}
    )
    assert (
        PolicyGateway(db).authorize(action, sentinel_finding=finding).failed_check
        == "destination_not_approved"
    )
