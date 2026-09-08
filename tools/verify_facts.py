#!/usr/bin/env python3
"""Run the equity-research gate over a facts file, against live sources.

No model and no agent loop — just the harvest tools and the deterministic gate.
Use it to check the gate's behaviour on a real filing, to calibrate a tag-map
entry, or to confirm a quotation actually appears in the document it cites.

    python tools/verify_facts.py tools/examples/nvda_q2fy27.json

The input is a JSON list of ``emit_fact`` argument objects. Each is emitted into
a scratch ledger, then every cited source is re-fetched and every number
re-checked. Requires SEC_EDGAR_USER_AGENT for the EDGAR calls.

Exit code is 1 if any fact was deleted or corrected, so it can gate a script.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plugins.tools.finance.emit import emit_fact
from plugins.tools.finance.ledger import (
    clear_ledger,
    ledger_facts,
)
from workflows.equity_research.sources import (
    lookups_for,
    resolve_source_texts,
    resolve_xbrl_values,
)
from workflows.equity_research.verification import Verdict, verify

_FAILING = {Verdict.DELETED, Verdict.CORRECTED}


async def main(path: str) -> int:
    if not os.environ.get("SEC_EDGAR_USER_AGENT"):
        print("warning: SEC_EDGAR_USER_AGENT unset; SEC will refuse the request",
              file=sys.stderr)

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    clear_ledger()
    try:
        print(f"=== emitting {len(payload)} fact(s) ===")
        for entry in payload:
            # ``_``-prefixed keys are notes for the reader, not tool arguments.
            args = {k: v for k, v in entry.items() if not k.startswith("_")}
            result = json.loads(await emit_fact.ainvoke(args))
            status = result.get("status")
            marker = {"recorded": " ", "conflict": "!", "rejected": "x"}.get(status, "?")
            print(f" {marker} {status:9} {result.get('fact_id') or result.get('error', '')}")

        facts = ledger_facts()
        print(f"\n=== re-reading sources for {len(facts)} fact(s) ===")
        xbrl = await resolve_xbrl_values(facts)
        texts = await resolve_source_texts(facts)
        xbrl_lookup, source_text = lookups_for(xbrl, texts)

        result = verify(facts, [], xbrl_lookup=xbrl_lookup, source_text=source_text)

        print("\n=== gate ===")
        for finding in result.findings:
            line = f" {finding.verdict.value:17} {finding.fact_id}"
            if finding.authoritative_value is not None:
                line += (f"\n     reported={finding.reported_value:,}"
                         f"  filed={finding.authoritative_value:,}")
            print(line)

        failed = [f for f in result.findings if f.verdict in _FAILING]
        print(f"\n surviving {len(result.facts)}/{len(facts)};"
              f" {len(failed)} needed intervention")
        return 1 if failed else 0
    finally:
        clear_ledger()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(asyncio.run(main(sys.argv[1])))
