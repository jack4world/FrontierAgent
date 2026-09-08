# Equity Research

Supply-chain transmission research. Given a driver event — a guidance change, a
capex revision — it works out whether and how that transmits to upstream
suppliers, and produces a report in which every figure is traceable to a source
and every conclusion states what would prove it wrong.

## Running it

```bash
export SEC_EDGAR_USER_AGENT="Your Name you@example.com"

uv run python tools/run_equity_research.py \
  "NVIDIA guided FY2028 revenue growth of ~70% on its 26 Aug 2026 call. \
   Does that transmit to advanced packaging (TSMC) and HBM?"
```

Writes `report.md` and `tables.json` to `./equity-research-run` (`--out` to
change). `--harvest-turns` / `--analysis-turns` bound each phase.

`SEC_EDGAR_USER_AGENT` is required: the SEC refuses requests without a contact
address, and every hard-verified figure goes through it. `SERPER_API_KEY` and
`JINA_API_KEY` in `.env` are what let the harvest reach anything that is not an
SEC filing.

**Not available in the terminal UI.** Its modes come from `apodex/profiles/`
and resolve tools through a different registry than the workflow engine uses;
wiring this in is a separate piece of work.

### Checking a set of figures without a model

```bash
uv run python tools/verify_facts.py tools/examples/nvda_q2fy27.json
```

Runs the harvest tools and the gate over a JSON facts file — no model, no agent
loop. Use it to confirm a quotation exists in the document it cites, or to
calibrate a tag-map entry.

## How it works

One node, three stages:

| stage | tools | job |
|---|---|---|
| harvest | network, EDGAR, `emit_fact` | gather sourced figures; may not conclude |
| analysis | no network at all | reason over them; record claims |
| gate | none — a pure function | re-read every source, judge every figure |

The separation that matters is the tool boundary, not parallelism. An analyst
that can search will go and find support for the conclusion it already holds,
and withholding the tools is the only thing that reliably prevents it. It has
no shell either — withholding search while leaving a `curl` open would be
theatre — and no free-text terminal, because an ungated way to hand in prose is
an escape hatch from every constraint on `emit_claim`.

### The two tables

`facts` hold numbers; `claims` cite facts by id and never restate a number.
That split is what makes verification a pure function rather than a second
model: checking a figure degenerates to comparing two values. A verifier that
is itself an LLM carries the failure mode it exists to remove.

`fact_id` is a content hash over identity — entity, metric, period, source —
and pointedly not over the value. Two agents citing the same number collapse to
one entry; two agents citing *different* numbers for the same identity collide,
which surfaces the disagreement instead of averaging it away.

### Verdicts

| verdict | meaning |
|---|---|
| `match` | agrees with the filing |
| `corrected` | disagreed; overwritten with the filed value |
| `quote_confirmed` | the quoted sentence is in the cited document |
| `deleted` | the quoted sentence is **not** in it — removed, not corrected |
| `source_unreachable` | the document could not be fetched; kept, labelled |
| `unverified` | secondary source, nothing to check against |
| `unresolved` | no XBRL observation for that concept and period |
| `unknown_tier` | tier not recognised; never silently gated |

A fabricated quotation is deleted rather than corrected: there is no right
value to substitute. A figure the gate could not reach is not the same as one
that failed, which is why those are separate verdicts.

## What it cannot do

- **Forward guidance is not in XBRL.** It lives in filings and transcripts, so
  it is `quoted_primary`: verified by quotation, never by value.
- **TSMC monthly data and Korean memory filers are not on EDGAR.** TSMC files a
  20-F annually; SK Hynix and Samsung file in Korea. They are in the node map
  regardless — a claim citing them beats one citing neither — but the hard gate
  cannot reach them.
- **The verifiable and the differentiated pull apart.** SEC filings are the
  most-read documents in the market; what is hard to verify is often what is
  worth knowing. Expect most figures on a supply-chain question to be
  `secondary`, and read the source grading rather than the tier alone.
- **It does not know what is priced.** `basis: consensus` facts and a claim's
  `consensus_delta` are how a view is placed against the market, and they are
  only as good as the consensus figures the harvest found.

## Files

```
nodes/main.py     the node: harvest → analysis → gate
prompts.py        one system prompt per phase
sources.py        async source resolution, report rendering
verification.py   the gate — pure, sync, no LLM
authority.py      grading of sources the gate cannot verify
../../plugins/tools/finance/     emit / compute / edgar / ledger / chain
../../plugins/skills/ai-compute-chain/   chain graph, node map, tag map
```

The skill directory holds **structure only, never figures**. A live run once
read calibration values out of the tag map and emitted them as harvested market
data; a test now fails if any value reappears there.
