"""Pipeline spec: ``equity_research``.

One node. Harvest, analysis, and the gate run inside it rather than as separate
DAG nodes, because they share the per-task ledger: splitting them across nodes
would mean serialising the two tables through pipeline state for no gain, and
the gate is a function call, not an agent.
"""

from __future__ import annotations

from frontier_agent.models.pipeline_spec import (
    CompressionConfig,
    ContextPolicy,
    NodeDefinition,
    PipelineSpec,
)
from workflows.equity_research.identity import ENTRY_NODE_ID, MAIN_ROLE_ID, PIPELINE_ID

EQUITY_RESEARCH_SPEC = PipelineSpec(
    pipeline_id=PIPELINE_ID,
    name="Equity Research",
    description=(
        "Supply-chain transmission research. Phase one harvests first-party "
        "figures into a facts table; phase two reasons over them with no "
        "network access and records claims that cite facts by id; a "
        "deterministic gate then re-reads every source and corrects, deletes, "
        "or labels each number before the report is assembled."
    ),
    entry_point=ENTRY_NODE_ID,
    terminal_nodes=[ENTRY_NODE_ID],
    nodes=[
        NodeDefinition(
            node_id=ENTRY_NODE_ID,
            role_id=MAIN_ROLE_ID,
            node_function="workflows.equity_research.nodes.main.equity_research_node",
            context_policy=ContextPolicy(
                include_fields=[
                    "original_question", "current_query", "language",
                    "task_id", "metadata",
                ],
            ),
            compression=CompressionConfig(enabled=False),
            output_fields=[
                "final_answer", "final_content",
                "equity_research_facts",
                "equity_research_claims",
                "equity_research_findings",
                "harvest_stopped_by", "analysis_stopped_by",
            ],
        ),
    ],
)
