#!/usr/bin/env python3
"""Health score for an InspectablePlan.

A plan's health is a single 0-100 number built from declared, versioned
inputs — never a black box. Every report prints the rules version, each
input's weight, its score, and its contribution, so a human can see exactly
why a plan scored what it did.

Components (default rules v1.0.0, see examples/rules.json):
  completeness  steps have a why and failure points          weight 20
  gating        irreversible steps are gated                 weight 20
  cost          cost known and within budget                 weight 15
  decision      latest decision approves this exact version  weight 15
  policy        no HIGH findings, no unwaived MEDIUM         weight 15
  evidence      steps covered by trace evidence              weight 15

An input that is missing is UNKNOWN, never silently fine: its component
scores 0 and the report says so. A plan can never score 100 with an
unknown input.

Bands: HEALTHY >= 80, NEEDS WORK 60-79, UNHEALTHY < 60.
Exit codes: 0 HEALTHY, 1 NEEDS WORK, 2 UNHEALTHY, 3 usage error.

Usage:
  python3 plan_health_score.py examples/plan.json
  python3 plan_health_score.py examples/plan.json --budget 5
  python3 plan_health_score.py examples/plan.json --decisions examples/decisions.jsonl \\
      --findings examples/findings.json --evidence examples/evidence.json
  python3 plan_health_score.py examples/plan.json --rules examples/rules.json --json
  python3 plan_health_score.py examples/plan.json --out health-report.md

Plan JSON: {"id", "version", "goal", "steps": [{"id", "action", "why",
"failure_points": [...], "reversible": bool, "gated": bool, "cost": number}],
"constraints": [...], "assumptions": [...]}.
Decisions JSONL: one {"plan_id", "version", "decision", "reviewer", "reason"}
per line; only the latest decision for this plan id + version counts.
Findings JSON: {"findings": [{"id", "severity": "high|medium|low",
"waived": bool}]}.
Evidence JSON: {"steps_covered": [step ids], "images": [{"path", "caption"}]}.
Rules JSON: {"version", "band_healthy", "band_needs_work", "components":
[{"id", "weight", "description"}]}.
"""
import json
import sys

DEFAULT_RULES = {
    "version": "1.0.0",
    "band_healthy": 80,
    "band_needs_work": 60,
    "components": [
        {"id": "completeness", "weight": 20,
         "description": "every step has a why and failure points"},
        {"id": "gating", "weight": 20,
         "description": "irreversible steps are gated"},
        {"id": "cost", "weight": 15,
         "description": "cost known and within budget"},
        {"id": "decision", "weight": 15,
         "description": "latest decision approves this exact version"},
        {"id": "policy", "weight": 15,
         "description": "no HIGH findings, no unwaived MEDIUM"},
        {"id": "evidence", "weight": 15,
         "description": "steps covered by trace evidence"},
    ],
}

USAGE = "usage: plan_health_score.py <plan.json> [--budget N] " \
    "[--decisions decisions.jsonl] [--findings findings.json] " \
    "[--evidence evidence.json] [--rules rules.json] [--json] [--out FILE]"


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_jsonl(path):
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def plan_hash(plan):
    """Content hash of a plan version, for decision matching."""
    import hashlib
    blob = json.dumps(plan, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def score_completeness(plan):
    steps = plan.get("steps") or []
    if not steps:
        return 0.0, "no steps in plan"
    complete = sum(1 for s in steps
                   if s.get("why") and s.get("failure_points"))
    return complete / len(steps), f"{complete}/{len(steps)} steps complete"


def score_gating(plan):
    steps = plan.get("steps") or []
    irreversible = [s for s in steps if s.get("reversible") is False]
    if not irreversible:
        return 1.0, "no irreversible steps"
    gated = sum(1 for s in irreversible if s.get("gated"))
    return gated / len(irreversible), \
        f"{gated}/{len(irreversible)} irreversible steps gated"


def score_cost(plan, budget):
    steps = plan.get("steps") or []
    if budget is None:
        return None, "no budget given — cost unchecked"
    unpriced = [s.get("id", "?") for s in steps
                if not isinstance(s.get("cost"), (int, float))]
    if unpriced:
        return None, "unpriced steps: " + ", ".join(unpriced) + \
            " — total is a lower bound, never a pass"
    total = sum(s["cost"] for s in steps)
    if total <= budget:
        return 1.0, f"${total:.2f} of ${budget:.2f} budget"
    over = (total - budget) / budget
    if over <= 0.25:
        return 0.5, f"${total:.2f} over ${budget:.2f} budget by {over:.0%}"
    return 0.0, f"${total:.2f} over ${budget:.2f} budget by {over:.0%}"


def score_decision(plan, decisions):
    if decisions is None:
        return None, "no decisions file given"
    latest = None
    for d in decisions:
        if d.get("plan_id") == plan.get("id") and \
                d.get("version") == plan.get("version"):
            latest = d
    if latest is None:
        return 0.0, "no decision recorded for this plan version"
    decision = latest.get("decision")
    if decision == "approve":
        return 1.0, f"approved by {latest.get('reviewer', 'unknown')}"
    if decision == "request-changes":
        return 0.5, "sent back for changes"
    return 0.0, f"decision: {decision}"


def score_policy(findings):
    if findings is None:
        return None, "no findings file given"
    items = findings.get("findings", []) if isinstance(findings, dict) \
        else findings
    active = [f for f in items if not f.get("waived")]
    highs = [f for f in active if f.get("severity") == "high"]
    mediums = [f for f in active if f.get("severity") == "medium"]
    if highs:
        return 0.0, f"{len(highs)} HIGH finding(s) unwaived"
    if mediums:
        return 0.5, f"{len(mediums)} MEDIUM finding(s) unwaived"
    return 1.0, "no unwaived findings"


def score_evidence(plan, evidence):
    if evidence is None:
        return None, "no evidence file given"
    steps = plan.get("steps") or []
    if not steps:
        return 0.0, "no steps in plan"
    covered = set(evidence.get("steps_covered") or [])
    hit = sum(1 for s in steps if s.get("id") in covered)
    return hit / len(steps), f"{hit}/{len(steps)} steps have evidence"


SCORERS = {
    "completeness": lambda plan, ctx: score_completeness(plan),
    "gating": lambda plan, ctx: score_gating(plan),
    "cost": lambda plan, ctx: score_cost(plan, ctx["budget"]),
    "decision": lambda plan, ctx: score_decision(plan, ctx["decisions"]),
    "policy": lambda plan, ctx: score_policy(ctx["findings"]),
    "evidence": lambda plan, ctx: score_evidence(plan, ctx["evidence"]),
}


def score_plan(plan, ctx, rules):
    components = []
    total = 0.0
    for comp in rules["components"]:
        cid = comp["id"]
        weight = comp["weight"]
        scorer = SCORERS.get(cid)
        if scorer is None:
            raise ValueError(f"unknown component in rules: {cid}")
        value, detail = scorer(plan, ctx)
        if value is None:
            status, contribution = "UNKNOWN", 0.0
        else:
            status = "OK" if value >= 1.0 else ("PARTIAL" if value > 0 else "FAIL")
            contribution = round(value * weight, 1)
        total += contribution
        components.append({
            "id": cid, "weight": weight, "value": value,
            "contribution": contribution, "status": status,
            "detail": detail, "description": comp.get("description", ""),
        })
    score = round(total, 1)
    if score >= rules["band_healthy"]:
        band = "HEALTHY"
    elif score >= rules["band_needs_work"]:
        band = "NEEDS WORK"
    else:
        band = "UNHEALTHY"
    return {"score": score, "band": band, "rules_version": rules["version"],
            "components": components}


def render_markdown(plan, result):
    lines = [f"# Health score: {result['score']}/100 — {result['band']}",
             "",
             f"Plan: `{plan.get('id', '?')}` v{plan.get('version', '?')} — "
             f"{plan.get('goal', '')}",
             f"Rules version: {result['rules_version']}",
             "",
             "| Component | Weight | Score | Contribution | Status | Detail |",
             "|---|---:|---:|---:|---|---|"]
    for c in result["components"]:
        value = "unknown" if c["value"] is None else f"{c['value']:.2f}"
        lines.append(f"| {c['id']} | {c['weight']} | {value} | "
                     f"{c['contribution']} | {c['status']} | {c['detail']} |")
    unknowns = [c["id"] for c in result["components"] if c["value"] is None]
    lines += ["", f"**Total: {result['score']}/100 — {result['band']}**"]
    if unknowns:
        lines.append("")
        lines.append("Unknown inputs (scored 0, never assumed fine): " +
                     ", ".join(unknowns) + ".")
    return "\n".join(lines) + "\n"


def parse_args(argv):
    args = {"plan": None, "budget": None, "decisions": None,
            "findings": None, "evidence": None, "rules": None,
            "json": False, "out": None}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--decisions", "--findings", "--evidence", "--rules", "--out"):
            if i + 1 >= len(argv):
                raise ValueError(f"{a} needs a value")
            args[a[2:]] = argv[i + 1]
            i += 2
        elif a == "--budget":
            if i + 1 >= len(argv):
                raise ValueError("--budget needs a value")
            try:
                args["budget"] = float(argv[i + 1])
            except ValueError:
                raise ValueError("--budget must be a number")
            i += 2
        elif a == "--json":
            args["json"] = True
            i += 1
        elif a.startswith("-"):
            raise ValueError(f"unknown option: {a}")
        elif args["plan"] is None:
            args["plan"] = a
            i += 1
        else:
            raise ValueError(f"unexpected argument: {a}")
    if args["plan"] is None:
        raise ValueError("a plan file is required")
    return args


def main(argv):
    try:
        args = parse_args(argv)
        plan = load_json(args["plan"])
        rules = load_json(args["rules"]) if args["rules"] else DEFAULT_RULES
        for key in ("version", "band_healthy", "band_needs_work", "components"):
            if key not in rules:
                raise ValueError(f"rules file missing '{key}'")
        ctx = {
            "budget": args["budget"],
            "decisions": load_jsonl(args["decisions"]) if args["decisions"] else None,
            "findings": load_json(args["findings"]) if args["findings"] else None,
            "evidence": load_json(args["evidence"]) if args["evidence"] else None,
        }
    except (ValueError, OSError, json.JSONDecodeError) as e:
        print(f"error: {e}", file=sys.stderr)
        print(USAGE, file=sys.stderr)
        return 3

    result = score_plan(plan, ctx, rules)
    if args["json"]:
        text = json.dumps(result, indent=2) + "\n"
    else:
        text = render_markdown(plan, result)
    if args["out"]:
        try:
            with open(args["out"], "w", encoding="utf-8") as f:
                f.write(text)
        except OSError as e:
            print(f"error: cannot write {args['out']}: {e}", file=sys.stderr)
            return 3
    print(text, end="")
    return {"HEALTHY": 0, "NEEDS WORK": 1, "UNHEALTHY": 2}[result["band"]]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
