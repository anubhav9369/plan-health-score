# plan-health-score

A **health score (0–100)** for an InspectablePlan — part of the trustworthy
agentic AI prototype for BRJP26100123 (Week 3: policy validation, after the
quota envelope).

## Why

The quota envelope says whether a plan fits its budget. The health score
says whether a plan is *ready*: complete steps, gated irreversibles, known
cost within budget, an approval for this exact version, clean policy
findings, and trace evidence. A health score always exposes its inputs,
rules and version — the report prints the rules version, each input's
weight, its score and its contribution, so a human can see exactly why a
plan scored what it did. Nothing is a black box.

## Use

```bash
python3 plan_health_score.py examples/plan.json
python3 plan_health_score.py examples/plan.json --budget 5
python3 plan_health_score.py examples/plan.json --budget 5 \
    --decisions examples/decisions.jsonl \
    --findings examples/findings.json \
    --evidence examples/evidence.json
python3 plan_health_score.py examples/plan.json --budget 5 \
    --decisions examples/decisions.jsonl \
    --findings examples/findings.json \
    --evidence examples/evidence.json --json
python3 plan_health_score.py examples/plan.json --budget 5 \
    --decisions examples/decisions.jsonl \
    --findings examples/findings.json \
    --evidence examples/evidence.json --out health-report.md
```

Exit codes: `0` = HEALTHY, `1` = NEEDS WORK, `2` = UNHEALTHY,
`3` = usage error (bad JSON, missing file, bad rules).

## Components (rules v1.0.0, `examples/rules.json`)

| Component | Weight | What it checks |
|---|---:|---|
| completeness | 20 | every step has a why and failure points |
| gating | 20 | irreversible steps are gated |
| cost | 15 | cost known and within budget |
| decision | 15 | latest decision approves this exact version |
| policy | 15 | no HIGH findings, no unwaived MEDIUM |
| evidence | 15 | steps covered by trace evidence |

Bands: HEALTHY ≥ 80, NEEDS WORK 60–79, UNHEALTHY < 60.

## Rules

- A missing input is **UNKNOWN, never silently fine**: its component
  scores 0 and the report names it. A plan can never score 100 with an
  unknown input.
- Unpriced steps are **unknown cost, not zero**: the total is a lower
  bound, so the cost limit can never be proven within budget — it reports
  UNKNOWN, never a pass.
- Cost over budget by ≤ 25% scores half; over by more scores 0.
- A HIGH policy finding scores 0 even if everything else is perfect;
  a waived finding does not count against the plan.
- `--rules` loads a custom weights file; the version in the file is
  printed in every report, so a score is always traceable to its rules.

## Examples

- `examples/plan.json` — healthy plan (v2, approved, $1.90 of $5) —
  scores **100.0 HEALTHY**; report in `examples/health-report.md`.
- `examples/plan-unhealthy.json` — v1 with an ungated publish, a step
  with no why, an unpriced step, $6.50 over a $5 budget, sent back for
  changes, one HIGH finding, 1/4 steps evidenced — scores **21.3
  UNHEALTHY**; report in `examples/health-report-unhealthy.md`.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

11 stdlib tests, no dependencies.
