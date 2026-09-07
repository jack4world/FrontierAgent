---
name: AI Compute Supply Chain
description: >-
  Node graph and transmission edges for the AI compute hardware chain, from
  hyperscaler capex through packaging to optical, thermal and power. Load the
  node file for the link you are analysing.
version: 0.1.0
tags: [equity-research, supply-chain, ai-compute]
allowed_tools: [read_text, emit_fact, emit_claim]
---

# AI Compute Supply Chain

This file is the **index**: the nodes and the edges between them. It is
deliberately small, because every analyst carries it. The detail for each link
lives in `nodes/<link>.md` — load only the one you were assigned.

## How to use this

1. Find your assigned link in the edge table.
2. `read_text` the corresponding `nodes/<link>.md`.
3. Emit every number you rely on with `emit_fact` **before** you reason about it.
4. Emit each conclusion with `emit_claim`, citing facts by id.

Never write a figure into prose without a `fact_id` behind it. A number that is
not in the facts table cannot be verified, and anything unverifiable is treated
as unsupported no matter how confident the sentence around it sounds.

## Nodes

| Node | Meaning |
|---|---|
| `csp_capex` | Hyperscaler capital expenditure — the chain's driver |
| `accelerators` | GPU / custom ASIC demand |
| `advanced_packaging` | CoWoS and equivalent advanced packaging capacity |
| `hbm` | High-bandwidth memory supply |
| `optical_modules` | 800G/1.6T datacom optics |
| `thermal` | Liquid cooling |
| `power` | Rack and facility power delivery |

## Edges

Direction is the claim being made; nothing here asserts that the edge held in
any particular quarter.

| From | To | Notes |
|---|---|---|
| `csp_capex` | `accelerators` | Direct; the shortest link on the chain |
| `accelerators` | `advanced_packaging` | Capacity-constrained rather than demand-constrained in some periods |
| `accelerators` | `hbm` | Content-per-unit as well as unit growth |
| `accelerators` | `optical_modules` | Cluster topology drives modules-per-accelerator; this ratio is not constant |
| `csp_capex` | `thermal` | Follows rack density, not spend directly |
| `csp_capex` | `power` | Facility-level, longest lead time |

## What is expected of you on your link

- **The counter-evidence field is not optional.** You are the person who knows
  this link best, which makes you the only one positioned to say where it
  breaks. Four analysts each arguing their own link transmits produces a
  report that agrees with itself and has checked nothing.
- **Find a quarter where your edge failed.** Every edge above has one. An edge
  that has never failed is usually an edge nobody has looked at closely.
- **Do not quantify without a derivation.** Direction plus a lag is a complete,
  acceptable answer. A number with no arithmetic behind it is worse than no
  number, because it reads as precision.

## Known coverage limits

Two figures on this chain cannot be hard-verified, by construction:

- **Forward capex guidance** is not in XBRL, which carries reported actuals
  only. It is `quoted_primary`: quote the sentence verbatim from the 8-K or
  press release, and the gate will confirm the sentence exists.
- **TSMC monthly revenue and packaging capacity** are not on EDGAR. TSMC files
  a 20-F annually; the monthly series is published to the TWSE. Treat it as
  `secondary` unless you have a first-party document to quote.

Neither is a gap to be closed later. They are properties of the sources, and
the report should say so where it relies on them.
