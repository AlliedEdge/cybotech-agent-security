from cybotech.agents.assistant import Assistant
from cybotech.agents.contracts import AgentTurn, ModelAction, ToolResult


class _Model:
    name = "test"

    def generate(self, _context):
        return AgentTurn(
            reply="x",
            memories=[],
            actions=[ModelAction(tool_name="read_email", arguments_json='{"id":"email-1"}')],
            done=True,
        )


class _Empty:
    def retrieve(self, _query):
        return []


class _Memory:
    def propose_memory(self, _proposal):
        raise AssertionError("not used")


class _Sentinel:
    def review(self, _proposal):
        return {"status": "completed"}


class _Gateway:
    def authorize(self, _proposal):
        raise RuntimeError("database unavailable")


class _Dispatcher:
    calls = 0

    def execute(self, _proposal):
        self.calls += 1
        return ToolResult(tool_name="read_email", arguments={}, success=True)


class _Events:
    def emit(self, _type, _payload):
        pass


def test_assistant_failure_path_never_dispatches_a_tool():
    dispatcher = _Dispatcher()
    assistant = Assistant(
        _Model(), _Memory(), _Empty(), _Sentinel(), _Gateway(), dispatcher, _Events()
    )
    result = assistant.run("task", "s", "t", [])
    assert result.status == "failed"
    assert dispatcher.calls == 0
