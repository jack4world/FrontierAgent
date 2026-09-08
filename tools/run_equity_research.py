#!/usr/bin/env python3
"""Run one equity-research question end to end.

    export SEC_EDGAR_USER_AGENT="Your Name you@example.com"
    uv run python tools/run_equity_research.py "NVIDIA guided FY2028 revenue
        growth of ~70% on its 26 Aug 2026 call. Does that transmit to advanced
        packaging and HBM?"

Harvest, then analysis, then the verification gate. Writes ``report.md`` and
``tables.json`` next to each other and prints a summary.

This is the entry point because the terminal UI cannot reach this workflow: its
modes come from ``apodex/profiles/`` and resolve tools through a separate
registry from the one the workflow engine uses. Wiring it in is its own piece
of work, not a config line.

SEC_EDGAR_USER_AGENT is required — the SEC refuses requests without a
contact address, and every xbrl_verified figure goes through it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from frontier_agent.core.runtime.registries import services as registry
from frontier_agent.core.runtime.registries.agents import AgentRegistry
from frontier_agent.core.runtime.registries.workflows import WorkflowContext
from frontier_agent.core.runtime.resources.manager import ResourceManager
from frontier_agent.infra.config import get_config
from frontier_agent.infra.llm_adapter import create_llm
from frontier_agent.models.node_context import NodeContext
from frontier_agent.scheduling.pipeline_registry import PipelineRegistry
from plugins.tools import get_builtin_tools
from workflows.equity_research import register
from workflows.equity_research.spec import EQUITY_RESEARCH_SPEC


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Run one equity-research question.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("question", help="the research question, in quotes")
    p.add_argument(
        "--out", default="./equity-research-run",
        help="directory for report.md and tables.json (default: %(default)s)",
    )
    p.add_argument(
        "--harvest-turns", type=int, default=45,
        help="turn budget for the harvest phase (default: %(default)s)",
    )
    p.add_argument(
        "--analysis-turns", type=int, default=50,
        help="turn budget for the analysis phase (default: %(default)s)",
    )
    p.add_argument("--task-id", default="equity-research", help="run identifier")
    p.add_argument("--quiet", action="store_true", help="warnings and errors only")
    return p.parse_args()


async def main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    for noisy in ("httpx", "httpcore", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if not os.environ.get("SEC_EDGAR_USER_AGENT"):
        print(
            "SEC_EDGAR_USER_AGENT is not set. The SEC refuses requests without a\n"
            'contact address, so every xbrl_verified figure would fail to verify.\n'
            '  export SEC_EDGAR_USER_AGENT="Your Name you@example.com"',
            file=sys.stderr,
        )
        return 2

    agents = AgentRegistry()
    registry.register(AgentRegistry, agents)
    register(WorkflowContext(PipelineRegistry(), agents))
    registry.register(
        ResourceManager,
        ResourceManager(llm=create_llm(get_config()), tools=dict(get_builtin_tools())),
    )

    from workflows.equity_research.nodes.main import equity_research_node

    ctx = NodeContext(EQUITY_RESEARCH_SPEC.nodes[0], lambda: args.task_id)
    out = await equity_research_node(
        {
            "original_question": args.question,
            "metadata": {"agent": {
                "harvest_max_turns": args.harvest_turns,
                "analysis_max_turns": args.analysis_turns,
            }},
        },
        ctx,
    )

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "report.md").write_text(out.get("final_answer", ""), encoding="utf-8")
    (outdir / "tables.json").write_text(
        json.dumps(
            {k: out.get(f"equity_research_{k}", []) for k in
             ("facts", "claims", "findings", "removed")},
            indent=2, ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    facts = out.get("equity_research_facts", [])
    claims = out.get("equity_research_claims", [])
    verdicts: dict[str, int] = {}
    for finding in out.get("equity_research_findings", []):
        verdicts[finding["verdict"]] = verdicts.get(finding["verdict"], 0) + 1

    print(f"\nfacts {len(facts)}   claims {len(claims)}   -> {outdir}/report.md")
    for verdict, count in sorted(verdicts.items(), key=lambda kv: -kv[1]):
        print(f"  {verdict:18} {count}")

    # A phase that ended this way did not finish. Say so on the way out, not
    # only inside the report — a thin result and a crashed run look identical.
    for phase in ("harvest", "analysis"):
        stop = out.get(f"{phase}_stopped_by")
        if stop in ("llm_error", "max_turns", "wall_deadline", "budget_exhausted"):
            print(f"  ! {phase} did not complete: {stop}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
