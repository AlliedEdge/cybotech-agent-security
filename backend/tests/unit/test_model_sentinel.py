from types import SimpleNamespace

from cybotech.agents.contracts import ActionProposal
from cybotech.services.sentinel.model import ModelFinding, ModelSentinel


def proposal():
    return ActionProposal(
        tool_name="search_customers",
        arguments={"query": "Lumenleaf"},
        proposed_by="primary_assistant",
        session_id="s",
        task_id="t",
        user_task_description="Search customers",
        retrieved_memory_ids=[],
    )


def client(response=None, error=None):
    def parse(**_kwargs):
        if error:
            raise error
        return SimpleNamespace(choices=[SimpleNamespace(message=response)])

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)))


def test_model_reviewer_flags_unrelated_action():
    parsed = ModelFinding(risk="high", rationale="Unrelated request", suspicious=True)
    reviewer = ModelSentinel(client(SimpleNamespace(refusal=None, parsed=parsed)), "fake")
    finding = reviewer.review(proposal())
    assert finding.disposition == "flag_for_denial"
    assert finding.status == "completed"


def test_model_reviewer_failure_is_unavailable():
    reviewer = ModelSentinel(client(error=TimeoutError()), "fake")
    assert reviewer.review(proposal()).status == "unavailable"
