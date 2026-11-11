"""Score the auditor on evals/cases.json and write runs/<timestamp>-eval.json.

    python evals/run.py [--no-claims]

Two configurations on the same draft: the full auditor, and the auditor with
the afterlife registry removed (live Crossref and OpenAlex only), so the
registry's contribution to retraction recall is measured rather than assumed.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from auditor import audit  # noqa: E402
from auditor.verify import Registry  # noqa: E402


def _count(rows: list[dict], kinds: tuple[str, ...], pred) -> dict:
    sel = [x for x in rows if x["kind"] in kinds]
    return {"hit": sum(bool(pred(x)) for x in sel), "of": len(sel)}


def score(cases: dict, report: dict) -> dict:
    by_num = {r["number"]: r for r in report["references"]}
    rows = []
    for t in cases["references"]:
        r = by_num.get(t["number"]) or {}
        rows.append({"number": t["number"], "kind": t["kind"], "found": r.get("found"), "status": r.get("status"),
                     "doi_right": bool(t["doi"] and r.get("doi") == t["doi"])})
    real = ("clean", "near_miss", "retracted")
    m = {
        "fabricated_flagged": _count(rows, ("fabricated",), lambda x: x["found"] != "found"),
        "real_found_right_doi": _count(rows, real, lambda x: x["found"] == "found" and x["doi_right"]),
        "real_wrongly_not_found": _count(rows, real, lambda x: x["found"] != "found"),
        "real_matched_wrong_doi": _count(rows, real, lambda x: x["found"] == "found" and not x["doi_right"]),
        "near_miss_found_right_doi": _count(rows, ("near_miss",), lambda x: x["found"] == "found" and x["doi_right"]),
        "retracted_flagged": _count(rows, ("retracted",), lambda x: x["status"] == "retracted"),
        "clean_false_retraction_or_concern": _count(rows, ("clean", "near_miss"), lambda x: x["status"] in ("retracted", "concern")),
    }

    by_sentence = {c["sentence"]: c for c in report["citations"]}
    claims = []
    for c in cases["claims"]:
        sup = (by_sentence.get(c["sentence"]) or {}).get("support") or {}
        v = (sup.get("per_ref") or {}).get(str(c["ref"]), {})
        claims.append({**c, "label": v.get("label"), "elsewhere": [e["ref"] for e in sup.get("elsewhere") or []]})
    if any(x["label"] for x in claims):
        true = [x for x in claims if x["truth"] == "true" and x["label"] != "no_abstract"]
        mis = [x for x in claims if x["truth"] == "misattributed" and x["label"] != "no_abstract"]

        def frac(sel, pred):
            return {"hit": sum(bool(pred(x)) for x in sel), "of": len(sel)}

        m["true_claim_supported"] = frac(true, lambda x: x["label"] == "supported")
        m["true_claim_contradicted"] = frac(true, lambda x: x["label"] == "contradicted")
        m["misattributed_not_supported"] = frac(mis, lambda x: x["label"] != "supported")
        m["misattributed_contradicted"] = frac(mis, lambda x: x["label"] == "contradicted")
        m["misattributed_true_source_named"] = frac(mis, lambda x: x["true_source"] in x["elsewhere"])
        m["claims_without_abstract"] = frac(claims, lambda x: x["label"] in (None, "no_abstract"))
    return {"metrics": m, "references": rows, "claims": claims}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-claims", action="store_true")
    ap.add_argument("--cases", default="evals/cases.json")
    args = ap.parse_args()
    cases = json.loads((ROOT / args.cases).read_text())
    support = None
    if not args.no_claims:
        from auditor.support import Support

        support = Support()
    out = {"created": datetime.now(UTC).isoformat(timespec="seconds"), "cases": args.cases, "seed": cases["seed"], "configs": {}}
    configs = {
        "full": {"registry": Registry(), "support": support},
        "no_registry": {"registry": Registry(ROOT / "data/none.parquet"), "support": None},
    }
    for name, kw in configs.items():
        report = audit.run(cases["text"], **kw)
        out["configs"][name] = {"meta": report["meta"], "summary": report["summary"], **score(cases, report)}
        print(name, json.dumps(out["configs"][name]["metrics"]))
    path = ROOT / f"runs/{datetime.now(UTC):%Y%m%dT%H%M%SZ}-eval-{Path(args.cases).stem}.json"
    path.write_text(json.dumps(out, indent=2, default=str) + "\n")
    print("wrote", path.relative_to(ROOT))


if __name__ == "__main__":
    main()
