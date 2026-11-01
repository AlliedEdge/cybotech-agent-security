"""Supervisor for the existing bounded LangGraph assistant workflow.

The inherited StateGraph coordinates the independent primary model, optional
memory-review model, action reviewer, memory enforcement and gateway. Keeping
the existing graph implementation preserves its tested execution boundary.
"""

from cybotech.agents.graph import PrimaryAssistantGraph


class SupervisorGraph(PrimaryAssistantGraph):
    """Entry point for the shared assistant workflow."""
