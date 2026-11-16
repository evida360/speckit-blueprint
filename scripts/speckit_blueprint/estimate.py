"""Estimates from the project's own history (DESIGN.md "Estimates").

Size is known before building (ACs, stories, repositories); rates come from finished epics (agent time,
cost, tokens, waiting and calendar time per AC). An epic's estimate is a P50–P90 range of rate × ACs. What
remains is a seeded Monte Carlo over the remaining epics, scheduled on their `Depends on` graph with the
parallelism seen so far. Fewer than three finished epics: low confidence. A cost no session recorded is
unknown, never zero: cost ranges come from the finished epics with a cost, or are None.
"""

from __future__ import annotations

import datetime as _dt
import random

from .metrics import collect, combine
from .status import project_status
from .units import cost as fmt_cost, duration, span


def cost_span(pair) -> str:
    return span(*pair, fmt=fmt_cost) if pair else "cost unknown"

RATE_KEYS = ("agent_s", "cost_usd", "tokens", "wait_s", "calendar_s")
RUNS = 2000


def quantile(values: list, q: float) -> float:
    v = sorted(values)
    if not v:
        return 0.0
    pos = (len(v) - 1) * q
    lo, hi = int(pos), min(int(pos) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


def epic_acs(epic: dict) -> int:
    return max(1, sum(len(s["acs"]) for s in epic["stories"]))


def measured(m: dict) -> dict:
    return {"agent_s": m["agent_s"], "cost_usd": m["cost_usd"], "tokens": float(sum(m["tokens"].values())),
            "wait_s": m["wait_s"], "calendar_s": m["calendar_s"]}


def history(root, cfg: dict, bcfg: dict, bp, parts: dict | None = None) -> list:
    """Per-AC rates of every finished epic with recorded sessions (all blueprints of the project)."""
    parts = parts if parts is not None else collect(root)
    from .config import bp_config
    from .model import Blueprint

    rates = []
    for bid in cfg["blueprints"]:
        b = bp_config(cfg, bid)
        if bid == bcfg["id"]:
            blueprint = bp
        else:
            path = root / b["blueprint_path"]
            if not path.is_file():
                continue
            blueprint = Blueprint(path)
        st = project_status(root, b)
        done = {r["epic"] for r in st.get("epics", []) if r["state"] == "done"}
        for e in blueprint.epics:
            m = combine(parts.get(f"{bid}:{e['id']}", []))
            if e["id"] in done and m["sessions"] and m["agent_s"] > 0:
                acs = epic_acs(e)
                rates.append({"epic": f"{bid}:{e['id']}", **{k: None if v is None else v / acs for k, v in measured(m).items()}})
    return rates


def confidence(n: int) -> str:
    return "none" if n == 0 else "low" if n < 3 else "medium" if n < 6 else "high"


def epic_range(rates: list, acs: int) -> dict:
    out: dict = {}
    for k in RATE_KEYS:
        vals = [r[k] * acs for r in rates if r.get(k) is not None]
        if not vals:  # no finished epic recorded it (a cost): unknown
            out[k] = None
            continue
        p50, p90 = quantile(vals, 0.5), quantile(vals, 0.9)
        if len(vals) == 1:
            p90 = p50 * 1.5  # one finished epic: widen rather than pretend to know
        out[k] = [round(p50, 2), round(p90, 2)]
    return out


def parallelism(parts: dict, bid: str) -> int:
    spans = []
    for key, ps in parts.items():
        if key.startswith(f"{bid}:E"):
            m = combine(ps)
            if m["first"] is not None:
                spans.append((m["first"], m["last"]))
    best = 1
    for s, _ in spans:
        best = max(best, sum(1 for a, b in spans if a <= s <= b))
    return best


def schedule(order: list, durations: dict, deps: dict, k: int, each: bool = False):
    """List scheduling on `k` slots respecting dependencies; returns the finish time (`each`: every epic's)."""
    finish: dict = {}
    slots = [0.0] * max(1, k)
    for e in order:
        ready = max([finish.get(d, 0.0) for d in deps.get(e, [])] or [0.0])
        i = min(range(len(slots)), key=lambda x: slots[x])
        start = max(ready, slots[i])
        finish[e] = start + durations[e]
        slots[i] = finish[e]
    if each:
        return finish
    return max(finish.values()) if finish else 0.0


def estimate(root, cfg: dict, bcfg: dict, bp, epic: dict | None = None, parts: dict | None = None,
             rates: list | None = None, st: dict | None = None) -> dict:
    parts = parts if parts is not None else collect(root)
    rates = rates if rates is not None else history(root, cfg, bcfg, bp, parts)
    bid = bcfg["id"]
    conf = confidence(len(rates))
    if epic is not None:
        out = {"blueprint_id": bid, "epic": epic["id"], "acs": epic_acs(epic), "history": len(rates), "confidence": conf}
        if rates:
            out.update(epic_range([r for r in rates if r["epic"] != f"{bid}:{epic['id']}"] or rates, epic_acs(epic)))
            out["brief"] = (f"agent {span(*out['agent_s'])} · {cost_span(out['cost_usd'])} · "
                            f"calendar {span(*out['calendar_s'], fmt=lambda s: duration(s, True))} ({conf} confidence, {len(rates)} finished epic{'' if len(rates) == 1 else 's'})")
        else:
            out["brief"] = "no finished epic with recorded sessions yet: no estimate"
        return out
    st = st if st is not None else project_status(root, bcfg)
    rows = {r["epic"]: r for r in st.get("epics", [])}
    remaining = [e for e in bp.epics if rows.get(e["id"], {}).get("state") != "done"]
    result = {"blueprint_id": bid, "history": len(rates), "confidence": conf, "epics": {}}
    if not rates:
        result["remaining"] = None
        result["brief"] = "no finished epic with recorded sessions yet: no estimate"
        return result
    spent = {e["id"]: measured(combine(parts.get(f"{bid}:{e['id']}", []))) for e in remaining}
    for e in remaining:
        result["epics"][e["id"]] = {"acs": epic_acs(e), **epic_range(rates, epic_acs(e)), "spent": spent[e["id"]]}
    k = parallelism(parts, bid)
    deps = {e["id"]: [d for b, d in e["depends"] if b is None] for e in remaining}
    # status's delivery order: each epic after the open ones it depends on, so the schedule waits for them
    order = [x for x in (st.get("epic_order") or {}).get("epics", []) if x in deps] or [e["id"] for e in remaining]
    rng = random.Random(42)
    priced = [r for r in rates if r["cost_usd"] is not None]
    sums = {"agent_s": [], "cost_usd": [], "tokens": [], "finish_s": []}
    # the delivery plan's sprints: each run also adds up the open epics of each sprint (where it is finished)
    home = bp.delivery_plan()["home"]
    groups: dict = {}
    for e in remaining:
        if e["id"] in home:
            groups.setdefault(home[e["id"]], []).append(e["id"])
    gsums = {g: {"agent_s": [], "cost_usd": [], "finish_s": []} for g in groups}
    for _ in range(RUNS):
        totals = {"agent_s": 0.0, "cost_usd": 0.0, "tokens": 0.0}
        cal, each = {}, {}
        for e in remaining:
            r = rng.choice(rates)
            acs = epic_acs(e)
            mine = {key: max(0.0, r[key] * acs - spent[e["id"]][key]) for key in ("agent_s", "tokens")}
            if priced:  # an epic finished without a recorded cost lends the cost of one that has it
                rc = r if r["cost_usd"] is not None else rng.choice(priced)
                mine["cost_usd"] = max(0.0, rc["cost_usd"] * acs - (spent[e["id"]]["cost_usd"] or 0.0))
            for key, v in mine.items():
                totals[key] += v
            each[e["id"]] = mine
            cal[e["id"]] = max(0.0, r["calendar_s"] * acs - spent[e["id"]]["calendar_s"])
        for key in totals:
            sums[key].append(totals[key])
        finish = schedule(order, cal, deps, k, each=True)
        sums["finish_s"].append(max(finish.values()) if finish else 0.0)
        for g, ids in groups.items():
            gsums[g]["agent_s"].append(sum(each[i]["agent_s"] for i in ids))
            gsums[g]["cost_usd"].append(sum(each[i].get("cost_usd", 0.0) for i in ids))
            gsums[g]["finish_s"].append(max(finish.get(i, 0.0) for i in ids))
    for key in sums:  # too few finished epics to know the spread: widen P90 as for one epic
        if (len(priced) if key == "cost_usd" else len(rates)) < 3:
            p50 = quantile(sums[key], 0.5)
            sums[key] = sums[key] + [p50 * 1.5] * max(1, len(sums[key]) // 5)
            for g in (g for g in gsums if key in gsums[g]):
                vals = gsums[g][key]
                gsums[g][key] = vals + [quantile(vals, 0.5) * 1.5] * max(1, len(vals) // 5)
    now = _dt.datetime.now().astimezone()
    fin = [quantile(sums["finish_s"], 0.5), quantile(sums["finish_s"], 0.9)]
    result["sprints"] = {
        g: {"epics": len(groups[g]),
            "agent_s": [round(quantile(v["agent_s"], q), 1) for q in (0.5, 0.9)],
            "cost_usd": [round(quantile(v["cost_usd"], q), 2) for q in (0.5, 0.9)] if priced else None,
            "calendar_s": [round(quantile(v["finish_s"], q), 1) for q in (0.5, 0.9)],
            "finish": [(now + _dt.timedelta(seconds=quantile(v["finish_s"], q))).date().isoformat() for q in (0.5, 0.9)]}
        for g, v in gsums.items()
    }
    result["remaining"] = {
        "agent_s": [round(quantile(sums["agent_s"], q), 1) for q in (0.5, 0.9)],
        "cost_usd": [round(quantile(sums["cost_usd"], q), 2) for q in (0.5, 0.9)] if priced else None,
        "tokens": [round(quantile(sums["tokens"], q)) for q in (0.5, 0.9)],
        "calendar_s": [round(x, 1) for x in fin],
        "finish": [(now + _dt.timedelta(seconds=x)).date().isoformat() for x in fin],
        "parallelism": k,
        "epics": len(remaining),
    }
    rem = result["remaining"]
    result["brief"] = (f"{len(remaining)} epics left: agent {span(*rem['agent_s'])} · {cost_span(rem['cost_usd'])} · "
                       f"finish {rem['finish'][0]}" + ("" if rem["finish"][0] == rem["finish"][1] else f" – {rem['finish'][1]}")
                       + f" ({conf} confidence, {len(rates)} finished epic{'' if len(rates) == 1 else 's'}, {k} at a time)")
    return result
