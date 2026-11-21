"""M.C.F.F. investment engine: Moat → CEO → Financials → Future.

Four gates, evaluated in that order. Each carries a score out of 10, the
evidence behind it, sources, risks, a confidence level and the date it was
last checked, and says where it came from:

- Financials is VERIFIED: scored from the Fundamentals Engine scorecard,
  which reads the company's SEC filings (TTM figures from Finnhub where
  noted). The score is the scorecard's own percentage on a 10-point scale.
- Moat, CEO and Future are judgments. The investor scores them. A drafted
  write-up (from the AI) is labeled INTERPRETATION and does not count toward
  a gate until the investor saves it as their own.

Status
------
BUY ZONE    every gate passes AND the price is at or below the investor's buy
            zone top (or, with no zone set, the app's value zone reads Load or
            Accumulate)
HOLD        every gate passes but the price is above the buy zone, or the
            price is unknown: own it, do not add
DO NOT BUY  any gate fails, or the research is not complete

A lower price alone never produces BUY ZONE: the gates come first.
"""
from __future__ import annotations

from datetime import date

GATES = ("moat", "ceo", "financials", "future")
GATE_LABELS = {"moat": "Moat", "ceo": "CEO", "financials": "Financials", "future": "Future"}
GATE_PROMPTS = {
    "moat": "Competitive advantage: pricing power, switching costs, network effects, intellectual property, scale.",
    "ceo": "Leadership: execution record, insider ownership, capital allocation, alignment with shareholders.",
    "financials": "Revenue and earnings growth, free cash flow, margins, debt, valuation.",
    "future": "Long-term demand for what it sells, technological relevance, disruption risk, 10–20 year outlook.",
}
DEFAULT_PASS = {"moat": 7.0, "ceo": 6.0, "financials": 6.0, "future": 7.0}
CONFIDENCE = ("low", "medium", "high")
GOOD_ZONES = ("load", "accumulate")


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


# ── Financials: verified ─────────────────────────────────────────────────

def financials_gate(fund: dict | None, *, fetched_at: str | None = None, threshold: float | None = None) -> dict:
    """Preserve existing Fundamentals score; fail closed when provenance is insufficient."""
    threshold = threshold if threshold is not None else DEFAULT_PASS["financials"]
    base = {"gate": "financials", "origin": "verified", "threshold": threshold}
    if not fund or fund.get("error") or fund.get("score_percent") is None:
        return {**base, "score": None, "passed": None, "confidence": None,
                "evidence": [], "risks": [], "sources": [],
                "note": (fund or {}).get("verdict_reason") or "No scored Fundamentals Engine evidence."}
    pct = _f(fund.get("score_percent"))
    scored = _f(fund.get("scored_metrics"))
    total = _f(fund.get("total_metrics"))
    if pct is None or not 0 <= pct <= 100 or scored is None or total is None or total <= 0 or scored <= 0 or scored > total:
        return {**base, "score": None, "passed": None, "confidence": "low",
                "evidence": [], "risks": [], "sources": [],
                "note": "Invalid or incomplete metric coverage; financial gate not scored."}
    coverage = scored / total
    evidence = []
    for sec in fund.get("sections") or []:
        for row in sec.get("rows") or []:
            if row.get("passed") is None:
                continue
            evidence.append({"section": sec.get("name"), "label": row.get("label"), "value": row.get("value"),
                             "passed": bool(row.get("passed"))})
    if not evidence:
        return {**base, "score": None, "passed": None, "confidence": "low",
                "evidence": [], "risks": [], "sources": [],
                "note": "No individually verified financial metrics; score withheld."}
    flags = [f if isinstance(f, str) else (f.get("text") or f.get("label") or str(f)) for f in fund.get("red_flags") or []]
    score = round(pct / 10, 1)
    confidence = "high" if coverage >= 0.85 else "medium" if coverage >= 0.6 else "low"
    # Low coverage may describe a partial company filing; it cannot pass the investment gate.
    passed = (score >= threshold and not flags) if coverage >= 0.6 else None
    ends = fund.get("fiscal_period_ends") or []
    sources = [{"label": "Fundamentals Engine (SEC filing data where available)" +
                (f" · fiscal period ended {ends[0]}" if ends else ""),
                "url": f"/research?ticker={fund.get('ticker') or ''}"}]
    for note in fund.get("source_notes") or []:
        sources.append({"label": str(note), "url": None})
    return {**base, "score": score, "passed": passed, "confidence": confidence,
            "evidence": evidence, "risks": flags, "sources": sources[:6],
            "verdict": fund.get("verdict"), "coverage": f"{int(scored)} of {int(total)} metrics scored",
            "verified_on": (fetched_at or "")[:10] or None,
            "note": ("Financial evidence coverage is below 60%; gate cannot pass." if coverage < 0.6
                     else "Red flags fail this gate regardless of score." if flags else None)}

def history_growth(fund: dict | None) -> dict:
    """Compound annual growth of revenue, net income and free cash flow over
    the years the filings cover (newest row first in the history table)."""
    rows = (fund or {}).get("history") or []
    out = {}
    for key, name in (("revenue_num", "revenue"), ("net_income_num", "net_income"), ("fcf_num", "fcf")):
        vals = [(r.get("year_offset"), _f(r.get(key))) for r in rows if _f(r.get(key)) is not None]
        if len(vals) < 2:
            continue
        (y0, new), (y1, old) = vals[0], vals[-1]
        years = (y1 or 0) - (y0 or 0)
        if years <= 0 or old is None or new is None or old <= 0 or new <= 0:
            out[name] = {"cagr": None, "years": years, "note": "not meaningful (a loss year or no base)"}
            continue
        out[name] = {"cagr": (new / old) ** (1 / years) - 1, "years": years}
    return out


# ── Valuation and scenarios ──────────────────────────────────────────────

def default_assumptions(fund: dict | None, pe: float | None) -> dict:
    """Starting points the investor is expected to edit. Derived from the
    company's own history, never from analyst forecasts (which the app does
    not have)."""
    g = history_growth(fund)
    picks = [x["cagr"] for k, x in g.items() if k in ("revenue", "net_income") and x.get("cagr") is not None]
    base = min(picks) if picks else None
    if base is not None:
        base = max(min(base, 0.30), -0.10)
    pe = _f(pe)
    return {
        "years": 5,
        "growth": {"conservative": None if base is None else round(base * 0.5, 4),
                   "base": None if base is None else round(base, 4),
                   "optimistic": None if base is None else round(min(base * 1.5, 0.40), 4)},
        "exit_pe": {"conservative": None if not pe else round(pe * 0.75, 1),
                    "base": None if not pe else round(pe, 1),
                    "optimistic": None if not pe else round(pe * 1.1, 1)},
        "derived_from": ("Growth: the lower of revenue and net-income growth over the filed years "
                         "(base), half of it (conservative), one and a half times it (optimistic). "
                         "Exit P/E: today's trailing P/E, 25% lower, 10% higher. Starting points only: "
                         "replace them with your own."),
        "history": g,
    }


def scenarios(price: float | None, pe: float | None, assumptions: dict) -> list[dict]:
    """Price in N years = EPS now × (1+g)^N × exit P/E; annual return from today's price."""
    price, pe = _f(price), _f(pe)
    n = max(1, min(20, int(_f(assumptions.get("years")) or 5)))
    out = []
    for case in ("conservative", "base", "optimistic"):
        g = _f((assumptions.get("growth") or {}).get(case))
        xpe = _f((assumptions.get("exit_pe") or {}).get(case))
        row = {"case": case, "growth": g, "exit_pe": xpe, "years": n}
        if price and pe and pe > 0 and g is not None and xpe and xpe > 0 and g > -1:
            eps = price / pe
            target = eps * (1 + g) ** n * xpe
            row.update(eps_now=eps, eps_future=eps * (1 + g) ** n, price_target=target,
                       total_return_pct=(target / price - 1) * 100,
                       annual_return_pct=((target / price) ** (1 / n) - 1) * 100)
        else:
            row["missing"] = [k for k, v in (("price", price), ("P/E", pe if pe and pe > 0 else None),
                                              ("growth", g), ("exit P/E", xpe)) if v is None]
        out.append(row)
    return out


# ── Evaluation ───────────────────────────────────────────────────────────

def judged_gate(name: str, entry: dict | None, threshold: float | None = None) -> dict:
    threshold = threshold if threshold is not None else DEFAULT_PASS[name]
    e = entry or {}
    score = _f(e.get("score"))
    counted = e.get("origin") == "investor" and score is not None
    return {"gate": name, "origin": e.get("origin") or None, "threshold": threshold,
            "score": score, "passed": (score >= threshold) if counted else None,
            "counted": counted, "evidence": e.get("evidence") or "", "risks": e.get("risks") or "",
            "sources": e.get("sources") or [], "confidence": e.get("confidence"),
            "verified_on": e.get("verified_on")}


def evaluate(research: dict | None, fin_gate: dict, *, price: float | None, value_zone: dict | None) -> dict:
    research = research or {}
    th = {**DEFAULT_PASS, **{k: _f(v) for k, v in (research.get("thresholds") or {}).items() if _f(v) is not None}}
    # Apply the investor-selected financial threshold to the verified score,
    # while preserving the hard red-flag veto.
    financial = dict(fin_gate)
    financial["threshold"] = th["financials"]
    if financial.get("score") is not None and financial.get("passed") is not None:
        financial["passed"] = financial["score"] >= th["financials"] and not financial.get("risks")
    gates = {"moat": judged_gate("moat", research.get("moat"), th["moat"]),
             "ceo": judged_gate("ceo", research.get("ceo"), th["ceo"]),
             "financials": financial,
             "future": judged_gate("future", research.get("future"), th["future"])}
    reasons = []
    incomplete = [GATE_LABELS[g] for g in GATES if gates[g]["passed"] is None]
    failed = [GATE_LABELS[g] for g in GATES if gates[g]["passed"] is False]

    price = _f(price)
    buy_low, buy_high = _f(research.get("buy_low")), _f(research.get("buy_high"))
    zone_name = ((value_zone or {}).get("zone") or "").lower() or None
    if buy_high:
        in_zone = price is not None and price <= buy_high
        zone_text = (f"Your buy zone: up to ${buy_high:,.2f}" + (f" (from ${buy_low:,.2f})" if buy_low else ""))
        zone_source = "investor"
    elif zone_name:
        in_zone = zone_name in GOOD_ZONES
        top = _f((value_zone or {}).get("accum_top"))
        zone_text = f"App value zone: {(value_zone or {}).get('zone_label') or zone_name.title()}" + \
                    (f" (Accumulate up to ${top:,.2f})" if top else "")
        zone_source = "value_zone"
    else:
        in_zone, zone_text, zone_source = None, "No buy zone set and no value zone available.", None
    below_floor = bool(buy_low and price is not None and price < buy_low)

    if failed:
        status = "DO NOT BUY"
        reasons.append("Fails the " + ", ".join(failed) + " gate" + ("s" if len(failed) > 1 else "") + ".")
    elif incomplete:
        status = "DO NOT BUY"
        reasons.append("Research incomplete: score " + ", ".join(incomplete) + " before buying.")
    elif price is None:
        status = "HOLD"
        reasons.append("No current price, so the buy zone cannot be checked.")
    elif in_zone is None:
        status = "HOLD"
        reasons.append("All four gates pass, but there is no buy zone to price against. Set one.")
    elif in_zone:
        status = "BUY ZONE"
        reasons.append(f"All four gates pass and ${price:,.2f} is inside the buy zone.")
        if below_floor:
            reasons.append(f"The price is below your zone's floor (${buy_low:,.2f}): re-check that the thesis "
                           f"still holds before buying a fall.")
    else:
        status = "HOLD"
        target = buy_high or _f((value_zone or {}).get("accum_top"))
        reasons.append(f"All four gates pass, but ${price:,.2f} is above the buy zone"
                       + (f" (≤ ${target:,.2f})" if target else "") + ". Hold; do not chase.")
    return {"status": status, "reasons": reasons, "gates": gates, "order": list(GATES),
            "valuation": {"price": price, "in_zone": in_zone, "zone_text": zone_text, "zone_source": zone_source,
                          "buy_low": buy_low, "buy_high": buy_high, "value_zone": zone_name}}


def clean_research(d: dict) -> dict:
    """What may be stored for one ticker. Scores are clamped to 0–10."""
    out = {}
    for g in ("moat", "ceo", "future"):
        e = d.get(g) or {}
        score = _f(e.get("score"))
        srcs = []
        for s in (e.get("sources") or [])[:8]:
            if isinstance(s, str):
                s = {"label": s, "url": s if s.startswith("http") else None}
            url = str(s.get("url") or "").strip()
            srcs.append({"label": str(s.get("label") or url)[:160],
                         "url": url[:400] if url.startswith(("http://", "https://")) else None})
        out[g] = {"score": None if score is None else max(0.0, min(10.0, score)),
                  "evidence": str(e.get("evidence") or "")[:4000], "risks": str(e.get("risks") or "")[:2000],
                  "sources": srcs, "confidence": e.get("confidence") if e.get("confidence") in CONFIDENCE else None,
                  "origin": "investor" if e.get("origin") != "ai_draft" else "ai_draft",
                  "verified_on": str(e.get("verified_on") or date.today().isoformat())[:10]}
    for k in ("buy_low", "buy_high"):
        v = _f(d.get(k))
        out[k] = v if v and v > 0 else None
    a = d.get("assumptions") or {}
    out["assumptions"] = {"years": int(max(1, min(20, _f(a.get("years")) or 5))),
                          "growth": {c: _f((a.get("growth") or {}).get(c)) for c in ("conservative", "base", "optimistic")},
                          "exit_pe": {c: _f((a.get("exit_pe") or {}).get(c)) for c in ("conservative", "base", "optimistic")}}
    out["thesis"] = str(d.get("thesis") or "")[:4000]
    return out
