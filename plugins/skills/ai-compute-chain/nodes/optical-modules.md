# Link: optical modules

Datacom optics inside the AI cluster — 800G today, 1.6T at the next node. Read
this only if you were assigned this link.

## What actually drives the number

The demand for modules is **not** a fixed multiple of accelerator units. It is
set by the network topology the cluster is built to, and that ratio has moved
between build generations. Any claim of the form "accelerator units × constant"
is the shape a consensus report takes; if you cannot source the ratio for the
specific build being discussed, say direction only and leave the number out.

Three things move independently, and conflating them is the usual error:

1. **Accelerator unit growth** — how many chips ship.
2. **Modules per accelerator** — set by topology (rail-optimised vs fat-tree,
   scale-up domain size). Changes when the reference design changes.
3. **Price per module** — falls through a generation, then steps up at each
   speed transition. Revenue can fall while units rise.

A claim about this link that does not separate these three is not a
transmission argument, it is a mood.

## Where the numbers come from

| Quantity | Best available source | Tier |
|---|---|---|
| Module vendor revenue, datacom segment | 10-Q / 10-K segment disclosure | `xbrl_verified` where a tag exists, else `quoted_primary` |
| Vendor forward guidance | Earnings press release (8-K) | `quoted_primary` — quote it verbatim |
| Modules-per-accelerator ratio | Vendor/CSP technical disclosure | `quoted_primary` if first-party, else `secondary` |
| Speed-mix transition timing | Vendor commentary | `quoted_primary` |

Segment revenue is the trap: several vendors report a combined datacom+telecom
line, and telecom moves on a completely different cycle. If the disclosure does
not split them, **say so in the claim** rather than treating the combined figure
as a datacom read.

## What would falsify a "capex transmits to modules" claim

Use one of these, or something better you can source, as the `falsification`
field. Never write a falsification condition you have no way to observe.

- Datacom segment revenue fails to grow sequentially in the quarter the lag
  implies.
- Vendor guidance rises while book-to-bill falls — orders being pulled in
  rather than added.
- Unit growth continues while revenue flattens — a price-step-down quarter,
  which looks like a broken edge but is not one.
- The CSP's capex increase lands in buildings and power rather than IT
  equipment. **This is the most common way this edge fails**, and it is
  visible: capex composition is disclosed.

## Counter-evidence you are expected to look for

You cannot search the web. Ask the coordinator to harvest, then cite the facts.
At minimum, look for the quarter in the recent past where capex guidance rose
and this link did **not** transmit, and explain why. If you claim the edge
holds without having examined a quarter where it did not, you have restated
consensus.
