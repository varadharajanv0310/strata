# strata — the ideology shift (v1 → v2)

> Written 2026-07-04, the day the approach changed. Companion to [SCOPE.md](SCOPE.md)
> (the roles-only charter, unchanged) and [GRID_PLAN.md](GRID_PLAN.md) (the build plan
> this shift produced).

## v1: the gatekeeper's creed

strata was founded on a strict data constitution. The product promised to be *free,
public, honest, roles-only* — and "honest" was interpreted in the strongest possible
way: **if a number couldn't be defended, it didn't enter the building.** Only
published, licensed, methodologically transparent sources qualified: official
statistics (BLS, ONS, Eurostat, ILOSTAT), government disclosures (H-1B filings), open
surveys (Stack Overflow), API-licensed aggregates (Adzuna). Every number carried its
source, sample size, currency, and vintage. The three salary lenses — advertised,
realized, official — were never blended. The LLM extraction layer was built
abstain-first: better to say "unknown" than to guess.

This creed had real virtues, and it built a real thing: a warehouse where every cell
can testify about where it came from. But it had one enormous, quietly accepted cost:
**the product was shaped by the data, not by the question.** We shipped 16 curated
roles across 7 countries with pooled experience — because that's what defensible data
covers. The dream of answering *any* career question was silently narrowed to
answering the questions official statistics happen to address.

## The critique that broke it

The break came from a simple observation about how people actually behave: someone
who wants to know what a data scientist with 5 years' experience earns in India
**googles it** — and gets an answer. That answer exists. It's on AmbitionBox,
PayScale, Glassdoor. It covers hundreds of roles official data has never heard of,
sliced by every year of experience. Meanwhile our "honest" grid, for that exact
question, offered either a pooled median or an empty cell.

The critique had three prongs:

1. **Coverage is not optional.** There are hundreds of tech roles — niche ones,
   brand-new ones — and there will never be a published dataset for "MLOps Engineer,
   3 years, Bengaluru." A product that refuses the only data that exists at that
   granularity isn't being honest; it's being empty.
2. **The purity concern was misaimed.** Refusing aggregator estimates doesn't protect
   anyone — users just leave and get the same numbers with *worse* labeling. And
   legal caution about republishing is a **publish-day** concern being paid at
   **localhost prices**. We are a local research build; the worry was premature by an
   entire product phase.
3. **The direction of fit was backwards.** We had let available data define the
   product's structure. The right order is the reverse: *define the grid the mission
   demands — every role × every country × every experience year × every ladder rung —
   then go get, or make, the data that fills it.*

## The insight that survived the collision

One piece of the old worldview turned out to be load-bearing, and it reshaped the new
one rather than dying: **the Google-answer numbers are manufactured too.** Nobody
*has* the long-tail dataset. Glassdoor's "DS, 5 years, India" is self-reports plus
postings, interpolated over sparse cells. The real question was never *download vs.
work for it* — everyone works for it. The question is **whose manufacturing machine
produces the number.**

That reframing did two things. It removed the last principled objection to aggregator
data (they're estimates like ours, just pre-computed), and it clarified what our own
GPU machine is *for*: the LLM extraction over raw postings manufactures the dimensions
no aggregator publishes — work arrangement, on-call load, education gates,
certifications, seniority-vs-track, honesty flags — with per-posting provenance.
**Aggregators fill the salary × experience grid wide; our machine fills every other
axis deep.** Complementary machines, one grid.

## v2: the labeler's creed

The shift, compressed into one sentence: **honesty moved from the gate to the label.**
We no longer ask "is this source pure enough to admit?" We ask "is this row labeled
truthfully enough that a reader — or future-us on publish day — knows exactly what it
is?"

Concretely:

- **Admit everything useful, label everything admitted.** Official stats, disclosed
  filings, surveys, API aggregates, harvested aggregator estimates, and our own
  LLM-manufactured aggregates all enter the warehouse — each row stamped with `kind`
  (official / advertised / realized / **estimate**), source, sample size, and
  retrieval date. Estimates never blend into the other lenses; they are their own lens.
- **Coverage is a first-class value.** An empty cell is its own kind of dishonesty — a
  lie of omission that sends the user back to Google. "Some data, honestly labeled"
  beats "no data, proudly withheld."
- **The publish-day option is preserved, not spent.** Because provenance is per-row,
  keeping or dropping aggregator data when strata goes public is a one-flag decision.
  We deferred the licensing question; we didn't delete it — and deferring cost nothing
  *because* the labeling discipline survived.
- **Localhost pragmatism, engineering courtesy.** ToS anxiety is shelved until
  publishing matters; polite crawling is kept anyway (throttles, fetch-once caching,
  circuit breakers) because it's good engineering, not because anyone's watching.
- **The product defines the grid; the pipeline fills it.** Role catalog: from 16
  curated roles to 300–600 canonical roles, thousands of specializations, tens of
  thousands of alias surfaces — with automatic admission for roles that didn't exist
  last quarter. Experience: from pooled medians to per-year observations and fitted
  pay-vs-experience curves that can answer *any* year. Progression: from curated
  multipliers to an empirical rung ladder with per-country years-and-pay at every
  step. Cells without data render as honest "no data yet" — never dropped, never faked.

## What did NOT change

- **Roles-only, absolutely.** Employers are still never a product axis. Even in the
  liberal regime, Blind and Comparably were rejected on sight, and AmbitionBox's
  per-company tables go unparsed. The pivot loosened *where data comes from*, never
  *what the product is about*.
- **No fabrication.** The abstain machinery, the no-extrapolation rule on fitted
  curves, the refusal to blend lenses, sample sizes shown as-is (n=14 rendered as
  n=14) — the anti-fabrication spine is intact. We got *more permissive about
  sources* and stayed *exactly as strict about claims*.
- **The engineering constitution.** Read-once/extract-richly, cache-is-checkpoint,
  resumable everything, granular local commits — unchanged.

## The one-line version

**v1 built a small, pure grid and called the emptiness around it integrity. v2 builds
the whole grid the question deserves — from every machine that can fill it, ours
included — and puts the integrity in the labels.**
