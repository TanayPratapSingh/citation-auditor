"""Citation Auditor: streamlit run app.py

Short term memory is st.session_state (the current draft and report). Long
term memory is data/library.sqlite (public paper metadata, reused across
drafts) and data/reports/ (only reports the user chose to save).
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

import streamlit as st

from auditor import audit
from auditor.lookup import Cache, Lookup
from auditor.verify import Registry

ROOT = Path(__file__).resolve().parent
SAVED = ROOT / "data/reports"
BADGE = {
    "retracted": ":red[Retracted]",
    "concern": ":orange[Expression of concern]",
    "disputed": ":orange[Registries disagree]",
    "corrected": ":blue[Corrected]",
    "ok": ":green[No retraction found]",
}
FOUND = {
    "not_found": ":red[Not found]",
    "doi_not_registered": ":red[DOI not registered]",
    "web_source": ":gray[Web source]",
}
CLAIM = {
    "supported": ":green[Abstract states this]",
    "contradicted": ":red[Abstract says the opposite]",
    "not_stated": ":orange[Abstract does not state this]",
    "no_abstract": ":gray[No abstract to check]",
}

st.set_page_config(page_title="Citation Auditor", layout="wide")


@st.cache_resource
def registry() -> Registry:
    return Registry()


@st.cache_resource
def support():
    from auditor.support import Support

    return Support()


def sidebar() -> bool:
    with st.sidebar:
        st.header("Citation Auditor")
        st.caption(
            "Checks each reference against Crossref, OpenAlex, PubMed and a retraction registry, "
            "then checks each citing sentence against the cited abstract with local models. "
            "It flags and explains; it never decides for you."
        )
        claims = st.toggle("Check claims against abstracts", value=True, help="Loads three local models on first use.")
        meta = registry().meta
        if meta:
            st.caption(f"Retraction registry: {meta['rows']:,} papers, built from data as of {meta['source_modified'][:10]}.")
        st.divider()
        st.subheader("Saved reports")
        files = sorted(SAVED.glob("*.json"), reverse=True) if SAVED.exists() else []
        for f in files[:10]:
            if st.button(f.stem, key=f"open-{f.name}", use_container_width=True):
                st.session_state.report = json.loads(f.read_text())
        if not files:
            st.caption("None. A report is saved only when you click Save below it.")
        if st.button("Delete saved reports and cached lookups"):
            shutil.rmtree(SAVED, ignore_errors=True)
            n = Cache().clear()
            st.session_state.pop("report", None)
            st.success(f"Deleted saved reports and {n} cached lookups.")
    return claims


def references(report: dict) -> None:
    for r in report["references"]:
        head = FOUND.get(r["found"]) or BADGE[r["status"]]
        title = r["title"] or r["text"][:120]
        with st.expander(f"{r['number']}. {head}  {title}", expanded=r["found"] != "found" or r["status"] != "ok"):
            st.write(r["text"])
            if r["doi"]:
                how = f"  \nMatched by {r['how'].replace('_', ' ')}, title similarity {r['match']}" if r["match"] else ""
                st.markdown(f"[https://doi.org/{r['doi']}](https://doi.org/{r['doi']}){how}")
            st.write(audit.describe(r))
            for f in r["flags"]:
                st.markdown(f"- **{f['kind']}**: {f['detail']} ({f['source']})")
            if r["found"] == "found":  # for other outcomes describe() already includes the notes
                for n in r["notes"]:
                    st.caption(n)
            if r["cited_in"]:
                st.caption(f"Cited in {len(r['cited_in'])} sentence(s).")
            elif r["found"] == "found":
                st.caption("No sentence in the draft cites this reference.")


def claims(report: dict) -> None:
    if not report["citations"]:
        st.info("No citing sentences were found. Markers like [3] or (Smith, 2020) link sentences to references.")
        return
    status = {str(r["number"]): r["status"] for r in report["references"]}
    for c in report["citations"]:
        st.markdown(f"> {c['sentence']}")
        for n in c["refs"]:
            if status.get(str(n)) in ("retracted", "concern"):
                st.markdown(f":red[Reference {n} is {'retracted' if status[str(n)] == 'retracted' else 'under an expression of concern'}.]")
        if c["unmatched"]:
            st.markdown(f":red[Markers with no matching reference: {', '.join(c['unmatched'])}]")
        sup = c.get("support") or {}
        for n, v in (sup.get("per_ref") or {}).items():
            line = f"Reference {n}: {CLAIM[v['label']]}"
            if v.get("quote"):
                line += f"  \n_{v['quote']}_"
            if v.get("scope"):
                line += f"  \nChecked the clause: \"{v['scope']}\""
            st.markdown(line)
        for e in sup.get("elsewhere") or []:
            st.markdown(f":blue[Reference {e['ref']}, which this sentence does not cite, states it:] _{e['quote']}_")
        st.divider()


def main() -> None:
    check_claims = sidebar()
    st.title("Citation Auditor")
    st.write("Upload a draft or paste its text, including the reference list.")
    up = st.file_uploader("Draft", type=["pdf", "txt", "md"])
    pasted = st.text_area("Or paste text", height=160)
    if st.button("Audit", type="primary", disabled=not (up or pasted.strip())):
        text = audit.read_text(data=up.getvalue(), name=up.name) if up else pasted
        bar = st.progress(0.0, "Starting")
        report = audit.run(text, lookup=Lookup(), registry=registry(),
                           support=support() if check_claims else None,
                           progress=lambda p, msg: bar.progress(min(p, 1.0), msg))
        report["meta"]["draft"] = up.name if up else "pasted text"
        st.session_state.report = report
        bar.empty()

    report = st.session_state.get("report")
    if not report:
        return
    s = report["summary"]
    cols = st.columns(6)
    cols[0].metric("References", s["references"])
    cols[1].metric("Not found", s["not_found"])
    cols[2].metric("Retracted", s["retracted"])
    cols[3].metric("Concern", s["concern"])
    cols[4].metric("Citing sentences", s["citing_sentences"])
    cols[5].metric("Not stated in abstract", (s["claim_checks"] or {}).get("not_stated", 0))
    st.caption(f"{report['meta']['draft']}, {report['meta']['seconds']} s, {report['meta']['network_calls']} network calls. "
               "\"Not found\" means not found in the sources listed, not that a reference is fake.")
    tab_refs, tab_claims = st.tabs(["References", "Claims"])
    with tab_refs:
        references(report)
    with tab_claims:
        claims(report)
    c1, c2, c3 = st.columns(3)
    c1.download_button("Download JSON", json.dumps(report, indent=2, default=str), "citation-audit.json")
    c2.download_button("Download Markdown", audit.markdown(report), "citation-audit.md")
    if c3.button("Save this report on this computer"):
        SAVED.mkdir(parents=True, exist_ok=True)
        name = f"{datetime.now():%Y%m%d-%H%M%S}-{Path(report['meta']['draft']).stem}"[:60]
        (SAVED / f"{name}.json").write_text(json.dumps(report, indent=2, default=str))
        st.caption(f"Saved as {name}.")


main()
