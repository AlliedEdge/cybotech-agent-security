"""Gateway dispatch target; never exposed directly in the model context."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from cybotech.agents.contracts import ActionProposal, Document, ToolResult


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ReadArguments(Arguments):
    id: str


class SearchArguments(Arguments):
    query: str = Field(max_length=200)


class SendArguments(Arguments):
    destination: str
    content: str = Field(min_length=1, max_length=8000)

    @field_validator("destination")
    @classmethod
    def synthetic_destination(cls, value: str) -> str:
        local, separator, domain = value.partition("@")
        if (
            not local
            or separator != "@"
            or "@" in domain
            or any(char.isspace() for char in value)
            or not domain.endswith(".example")
        ):
            raise ValueError("Only reserved .example destinations are supported")
        return value


class SandboxDispatcher:
    def __init__(self, documents: list[Document]) -> None:
        self.documents = {doc.id: doc for doc in documents}
        self.deliveries: list[dict[str, str]] = []
        self.calls: list[ToolResult] = []

    def execute(self, proposal: ActionProposal) -> ToolResult:
        arguments = proposal.arguments
        payload: dict[str, Any]
        try:
            if proposal.tool_name in {"read_email", "read_document"}:
                read = ReadArguments.model_validate(arguments)
                document = self.documents[read.id]
                expected = "email" if proposal.tool_name == "read_email" else "document"
                if document.source_type != expected:
                    raise ValueError("source type mismatch")
                payload = {"document": document.model_dump(mode="json")}
            elif proposal.tool_name == "search_customers":
                search = SearchArguments.model_validate(arguments)
                customers = [
                    {"id": "synthetic-customer-1", "name": "Lumenleaf Demo", "open_tickets": 3}
                ]
                payload = {
                    "customers": [
                        row for row in customers if search.query.lower() in str(row["name"]).lower()
                    ]
                }
            elif proposal.tool_name == "send_report":
                send = SendArguments.model_validate(arguments)
                self.deliveries.append(send.model_dump())
                payload = {
                    "simulated": True,
                    "destination": send.destination,
                    "delivery_id": f"delivery-{len(self.deliveries)}",
                }
            else:
                raise ValueError("unknown tool")
            result = ToolResult(
                tool_name=proposal.tool_name, arguments=arguments, success=True, result=payload
            )
        except (ValidationError, ValueError, KeyError):
            result = ToolResult(
                tool_name=proposal.tool_name,
                arguments=arguments,
                success=False,
                error="Invalid sandbox tool request",
            )
        self.calls.append(result)
        return result
