"""Seam B — workflow registration, asserted against fresh in-memory registries.

Same shape as ``tests/test_agent_team_workflow.py``. No model is started: the
properties under test are properties of the registration itself, which is
exactly why they are cheap to pin.
"""

from __future__ import annotations

import importlib

import pytest

from frontier_agent.core.runtime.registries.agents import AgentRegistry
from frontier_agent.core.runtime.registries.workflows import WorkflowContext
from frontier_agent.scheduling.pipeline_registry import PipelineRegistry
from plugins.tools import get_builtin_tools
from workflows.agent_team import WEB_TOOL_NAMES
from workflows.equity_research import (
    ANALYST_ROLE_ID,
    MAIN_ROLE_ID,
    PIPELINE_ID,
    register,
)


@pytest.fixture
def agents() -> AgentRegistry:
    registry = AgentRegistry()
    register(WorkflowContext(PipelineRegistry(), registry))
    return registry


def test_equity_research_registers_its_two_roles() -> None:
    # No pipeline yet: both shipped coordinator nodes resolve their LLM and
    # tool pool from role ids fixed at import, so pointing a spec at one would
    # silently run these roles as somebody else's. Wiring a coordinator node
    # is the next slice; the roles and their tool boundaries stand on their own.
    pipelines = PipelineRegistry()
    agents = AgentRegistry()

    register(WorkflowContext(pipelines, agents))

    assert agents.has(MAIN_ROLE_ID)
    assert agents.has(ANALYST_ROLE_ID)


def test_every_tool_the_roles_name_actually_resolves(agents: AgentRegistry) -> None:
    # ResourceManager builds a role's toolset with
    # ``[t for name, t in self._tools.items() if name in allowed]`` — a name it
    # cannot resolve is silently dropped, not reported. So a role can name
    # emit_fact, start clean, and simply never have it. Asserting the name is
    # in the list proves nothing; asserting it resolves is the real invariant.
    available = set(get_builtin_tools())

    for role_id in (MAIN_ROLE_ID, ANALYST_ROLE_ID):
        role = agents.get(role_id)
        missing = [name for name in role.allowed_tools if name not in available]
        assert missing == [], f"{role_id} names unregistered tools: {missing}"


def test_the_analyst_cannot_reach_the_network(agents: AgentRegistry) -> None:
    # The load-bearing invariant of the whole design. An analyst that can
    # search will go and find support for the conclusion it already has;
    # withholding the tools is the only thing that actually prevents it,
    # and it is a property of the role, not of the prompt.
    analyst = agents.get(ANALYST_ROLE_ID)

    assert WEB_TOOL_NAMES.isdisjoint(analyst.allowed_tools)


def test_the_analyst_has_no_shell_either(agents: AgentRegistry) -> None:
    # Withholding web_search while leaving a shell in place would be
    # theatre: one curl and the analyst is back on the open internet.
    analyst = agents.get(ANALYST_ROLE_ID)

    assert "bash" not in analyst.allowed_tools
    assert "run_python_code" not in analyst.allowed_tools


def test_the_analyst_can_still_record_findings_and_load_chain_knowledge(
    agents: AgentRegistry,
) -> None:
    # Cutting off the network must not cut off the job: the analyst still
    # emits into the two tables and still loads its own link's notes.
    analyst = agents.get(ANALYST_ROLE_ID)

    assert "emit_fact" in analyst.allowed_tools
    assert "emit_claim" in analyst.allowed_tools
    assert analyst.enable_skills
    # Skill metadata is injected into the prompt, but loading a SKILL.md body
    # needs read_text — without it the analyst can see its knowledge and not
    # open it.
    assert "read_text" in analyst.allowed_tools


def test_data_collection_is_a_tool_on_the_coordinator_not_a_separate_agent(
    agents: AgentRegistry,
) -> None:
    # Harvesting is deterministic API work. Giving it its own reasoning agent
    # buys nothing and adds a turn in which numbers can be mis-transcribed.
    coordinator = agents.get(MAIN_ROLE_ID)

    assert "fetch_xbrl_metric" in coordinator.allowed_tools
    assert not agents.has("equity_research_harvester")


# ── Pipeline ──────────────────────────────────────────────────────────────


@pytest.fixture
def pipelines() -> PipelineRegistry:
    registry = PipelineRegistry()
    register(WorkflowContext(registry, AgentRegistry()))
    return registry


def test_the_pipeline_is_registered_and_entered_at_our_own_role(
    pipelines: PipelineRegistry,
) -> None:
    # Borrowing a shipped coordinator node would resolve its own role ids at
    # import, silently running these roles as somebody else's. The entry node
    # must name our role.
    spec = pipelines.get(PIPELINE_ID)

    node = next(n for n in spec.nodes if n.node_id == spec.entry_point)
    assert node.role_id == MAIN_ROLE_ID
    assert (node.node_function or "").startswith("workflows.equity_research.")


def test_the_node_function_actually_imports(pipelines: PipelineRegistry) -> None:
    # A dotted path is a string until something dereferences it. Left unchecked,
    # a typo surfaces inside the scheduler long after the run has started and
    # the harvest has already been paid for.
    spec = pipelines.get(PIPELINE_ID)
    node = next(n for n in spec.nodes if n.node_id == spec.entry_point)

    module_path, _, attr = (node.node_function or "").rpartition(".")
    resolved = getattr(importlib.import_module(module_path), attr)

    assert callable(resolved)


# ── Each phase must hold the tools its own prompt demands ─────────────────

# Tools that need sub-agent or task-board machinery the single-node pipeline
# never sets up. Bound to a role, they are dead ends the model can still call.
_UNSERVICEABLE = frozenset({
    "create_subagent", "assign_task", "collect_reports", "stop_subagent",
    "add_task", "update_task", "finish_planning",
})


def test_the_harvest_phase_can_record_what_its_prompt_tells_it_to_record(
    agents: AgentRegistry,
) -> None:
    # The harvest prompt is an instruction to call emit_fact. Without the tool
    # bound, the phase runs its full turn budget and records nothing — and
    # nothing in the run reports an error, because an unbound tool is simply
    # absent rather than refused.
    coordinator = agents.get(MAIN_ROLE_ID)

    assert "emit_fact" in coordinator.allowed_tools


def test_neither_role_carries_a_tool_this_pipeline_cannot_service(
    agents: AgentRegistry,
) -> None:
    # The role lists were written for a coordinator that spawned sub-agents.
    # The node runs two sequential phases instead, so those tools now lead
    # nowhere; leaving them bound invites the model to delegate into a void.
    for role_id in (MAIN_ROLE_ID, ANALYST_ROLE_ID):
        dead = _UNSERVICEABLE.intersection(agents.get(role_id).allowed_tools)
        assert dead == set(), f"{role_id} carries unserviceable tools: {sorted(dead)}"
