from cybotech.services.policy_gateway.service import PolicyGateway

from .conftest import completed_finding, report_action


def test_unknown_tool_and_invalid_arguments_are_rejected_before_dispatch(db):
    gateway = PolicyGateway(db)
    unknown = gateway.authorize(
        report_action(tool_name="shell"), sentinel_finding=completed_finding()
    )
    invalid = gateway.authorize(
        report_action(arguments={"destination": "reports@internal.example"}),
        sentinel_finding=completed_finding(),
    )
    assert unknown.failed_check == "unknown_tool"
    assert invalid.failed_check == "invalid_arguments"
