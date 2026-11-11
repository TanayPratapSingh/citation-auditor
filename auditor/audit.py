"""Run every check on one draft and return a report dict.

The report wording follows two rules from the proposal's ethics table: a
reference is never called fabricated, only "not found" with the list of what
was searched, and every verdict carries the passage or record it rests on.
"""

from __future__ import annotations

import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from auditor.lookup import Lookup
from auditor.parse import find_citations, split_references, split_sections
from auditor.verify import Registry, check

LABELS = {
    "supported": "Abstract states this",
    "contradicted": "Abstract says the opposite",
    "not_stated": "Abstract does not state this",
    "no_abstract": "No abstract to check",
}
STATUS = {
    "retracted": "Retracted",
    "concern": "Expression of concern",
    "disputed": "Registries disagree",
    "corrected": "Corrected",
    "ok": "No retraction found",
}


def read_text(path: str | Path | None = None, data: bytes | None = None, name: str = "") -> str:
    name = str(path or name)
    if data is None:
        data = Path(path).read_bytes()
    if name.lower().endswith(".pdf"):
        import pymupdf

        with pymupdf.open(stream=data, filetype="pdf") as doc:
            return "\n".join(page.get_text("text") for page in doc)
    return data.decode("utf-8", "replace")


def run(text: str, lookup: Lookup | None = None, registry: Registry | None = None, support=None, progress=None) -> dict:
    started = time.time()
    lookup = lookup or Lookup()
    registry = registry or Registry()
    progress = progress or (lambda *_: None)
    body, refs_text = split_sections(text)
    refs = split_references(refs_text)
    cites = find_citations(body, refs)

    checked = []
    for i, r in enumerate(refs):
        progress(i / max(len(refs), 1), f"Checking reference {r.number} of {len(refs)}")
        checked.append(check(r, lookup, registry))

    citations = [
        {"sentence": c.sentence, "claim": c.claim, "refs": c.refs, "unmatched": c.unmatched,
         "scopes": {str(k): v for k, v in c.scopes.items()}, "support": None}
        for c in cites
    ]
    if support is not None:
        progress(0.9, "Checking claims against abstracts")
        support.index({c.number: (c.title, c.abstract) for c in checked if c.abstract})
        for c in citations:
            if c["refs"]:
                c["support"] = support.check(c["claim"], c["refs"], c["scopes"])

    cited_in: dict[int, list[int]] = {}
    for i, c in enumerate(citations):
        for n in c["refs"]:
            cited_in.setdefault(n, []).append(i)
    references = [{**c.to_dict(), "cited_in": cited_in.get(c.number, [])} for c in checked]

    labels = Counter(v["label"] for c in citations if c["support"] for v in c["support"]["per_ref"].values())
    summary = {
        "references": len(references),
        "found": sum(r["found"] == "found" for r in references),
        "not_found": sum(r["found"] in ("not_found", "doi_not_registered") for r in references),
        "web_sources": sum(r["found"] == "web_source" for r in references),
        **{k: sum(r["status"] == k for r in references) for k in ("retracted", "concern", "disputed", "corrected")},
        "no_abstract": sum(r["found"] == "found" and not r["abstract"] for r in references),
        "citing_sentences": len(citations),
        "never_cited": sum(not r["cited_in"] for r in references),
        "unmatched_markers": sum(len(c["unmatched"]) for c in citations),
        "claim_checks": dict(labels),
    }
    progress(1.0, "Done")
    return {
        "meta": {
            "created": datetime.now(UTC).isoformat(timespec="seconds"),
            "seconds": round(time.time() - started, 1),
            "network_calls": lookup.calls,
            "registry": registry.meta,
            "models": getattr(support, "models", None),
        },
        "summary": summary,
        "references": references,
        "citations": citations,
    }


def describe(ref: dict) -> str:
    """One plain sentence about a reference, in the report's wording rules."""
    if ref["found"] == "web_source":
        return "Web source, not an indexed paper. " + " ".join(ref["notes"])
    if ref["found"] != "found":
        searched = ", ".join(ref["searched"])
        why = " ".join(ref["notes"])
        return f"Not found. Searched: {searched}. {why}".strip()
    head = STATUS[ref["status"]]
    if ref["status"] != "ok":
        sources = sorted({f["source"] for f in ref["flags"] if f["kind"] == ref["status"]})
        head += f" (per {', '.join(sources)})"
    return head + ("" if ref["abstract"] else ". No abstract available, so claims citing it were not checked")


def markdown(report: dict) -> str:
    s = report["summary"]
    lines = [
        "# Citation audit",
        "",
        f"{s['references']} references, {s['found']} found, {s['not_found']} not found, "
        f"{s['retracted']} retracted, {s['concern']} with an expression of concern. "
        f"{s['citing_sentences']} citing sentences.",
        "",
        "## References",
        "",
    ]
    for r in report["references"]:
        title = r["title"] or r["text"][:90]
        link = f" https://doi.org/{r['doi']}" if r["doi"] else ""
        lines.append(f"{r['number']}. **{title}**{link}  ")
        lines.append(f"   {describe(r)}")
        for f in r["flags"]:
            lines.append(f"   - {f['kind']}: {f['detail']} ({f['source']})")
    lines += ["", "## Claims", ""]
    for c in report["citations"]:
        lines.append(f"- \"{c['sentence']}\"")
        if c["unmatched"]:
            lines.append(f"  - markers with no matching reference: {', '.join(c['unmatched'])}")
        for n, v in ((c["support"] or {}).get("per_ref") or {}).items():
            quote = f": \"{v['quote']}\"" if v.get("quote") else ""
            lines.append(f"  - ref {n}: {LABELS[v['label']]}{quote}")
        for e in (c["support"] or {}).get("elsewhere") or []:
            lines.append(f"  - ref {e['ref']}, which this sentence does not cite, states it: \"{e['quote']}\"")
    return "\n".join(lines) + "\n"
