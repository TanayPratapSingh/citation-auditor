"""Does each reference exist, and does any registry say it was retracted?

Existence: a DOI in the reference is looked up in Crossref, then at the DOI
resolver (which covers DataCite and other agencies). Without a DOI, or when the
DOI does not exist, the reference string is searched in Crossref and then
OpenAlex, and a candidate is accepted only when its title appears in the
reference string (fuzzy match >= TITLE_MATCH) and the years differ by at most one.

Status: four independent signals, each reported with its source.
  1. Crossref `updated-by`, which carries Retraction Watch records and publisher notices
  2. OpenAlex `is_retracted`
  3. the afterlife registry (Retraction Watch, OpenAlex, PubMed, PMC merged per paper)
  4. a "RETRACTED:" or "WITHDRAWN:" title prefix on the record itself
Records with the same title as the match are checked too: publishers sometimes
keep two DOIs for one paper and flag only one (Wakefield 1998 is an example).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pyarrow.parquet as pq
from rapidfuzz import fuzz

from auditor.lookup import Lookup, inverted_to_text, strip_tags
from auditor.parse import URL, Reference, fold, title_segment

ROOT = Path(__file__).resolve().parents[1]
TITLE_MATCH = 90  # title similarity, 0 to 100, accepted on its own
TITLE_WITH_AUTHOR = 75  # accepted only when the first author's surname also matches and the year is within one
SAME_TITLE = 95
# Supplementary files and reviews have their own DOIs, and so do journals and series as containers.
SKIP_TYPES = {"component", "peer-review", "grant", "journal", "journal-issue", "journal-volume",
              "book-series", "book-set", "proceedings", "proceedings-series", "report-series"}
NOTICE = re.compile(
    r"^(retraction|notice of retraction|retraction note|retraction notice|editorial expression of concern|"
    r"expression of concern|correction|erratum|corrigendum|withdrawal|removal notice)\b"
)
# IEEE prefixes the paper's own title with "Notice of Retraction:", so that is a flagged paper, not a notice.
FLAG_PREFIX = re.compile(r"^\s*(?:(retracted|withdrawn)\s*(?:article)?\s*[:\-]?|(notice of retraction)\s*:)\s*", re.I)
UPDATE_KIND = {
    "retraction": "retracted",
    "withdrawal": "retracted",
    "removal": "retracted",
    "expression_of_concern": "concern",
    "correction": "corrected",
    "erratum": "corrected",
    "corrigendum": "corrected",
}
SEVERITY = {"retracted": 3, "concern": 2, "disputed": 2, "corrected": 1, "reinstated": 0, "ok": 0}


@dataclass
class Flag:
    kind: str  # retracted | concern | corrected | disputed | reinstated
    source: str
    detail: str = ""
    doi: str | None = None  # the record the flag is about


@dataclass
class Checked:
    number: int
    text: str
    found: str  # found | not_found | doi_not_registered | web_source
    how: str = ""  # doi | doi_other_agency | crossref_search | openalex_search
    doi: str | None = None
    title: str | None = None
    year: int | None = None
    venue: str | None = None
    match: float | None = None
    notes: list[str] = field(default_factory=list)
    searched: list[str] = field(default_factory=list)
    status: str = "ok"
    flags: list[Flag] = field(default_factory=list)
    abstract: str | None = None
    abstract_source: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class Registry:
    def __init__(self, path: Path = ROOT / "data/registry.parquet") -> None:
        self.by_doi: dict[str, dict] = {}
        self.by_pmid: dict[str, dict] = {}
        self.meta = {}
        if not path.exists():
            return
        meta_path = path.with_suffix(".json")
        self.meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
        for row in pq.read_table(path).to_pylist():
            if row["doi"]:
                self.by_doi[row["doi"]] = row
            if row["pmid"]:
                self.by_pmid[row["pmid"]] = row

    def get(self, doi: str | None, pmid: str | None) -> dict | None:
        return (doi and self.by_doi.get(doi)) or (pmid and self.by_pmid.get(pmid)) or None

    @property
    def label(self) -> str:
        when = (self.meta.get("source_modified") or "")[:10]
        return f"afterlife registry ({when})" if when else "afterlife registry"


def norm_title(t: str) -> str:
    t = FLAG_PREFIX.sub("", fold(t))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", t)).strip()


def title_of(item: dict) -> str:
    t = item.get("title")
    return (t[0] if isinstance(t, list) and t else t) or ""


def year_of(item: dict) -> int | None:
    if item.get("publication_year"):
        return item["publication_year"]
    parts = (item.get("issued") or {}).get("date-parts") or [[None]]
    return parts[0][0] if parts and parts[0] else None


def title_score(title: str, ref_text: str) -> float:
    """How well a candidate's title matches the reference.

    The candidate title is compared whole against the reference's title
    segment, which tolerates a dropped word. It may also be found as a
    substring of the full reference string, but only when it is at least 80%
    as long as the title segment: otherwise a journal name ("Journal of
    Mechanical Engineering") or the last words of a title ("Celastrus
    paniculatus") match at 100, both seen in the held out eval.
    """
    nt = norm_title(title)
    if len(nt) < 12:  # short titles ("Editorial") match anything
        return 0.0
    seg = title_segment(ref_text)
    nseg = norm_title(seg) if seg else ""
    score = fuzz.ratio(nt, nseg) if nseg else 0.0
    if not nseg or len(nt) >= 0.8 * len(nseg):
        score = max(score, fuzz.partial_ratio(nt, norm_title(ref_text)))
    return score


def first_surname(item: dict) -> str | None:
    if item.get("author"):
        name = item["author"][0].get("family") or ""
    elif item.get("authorships"):
        name = (item["authorships"][0].get("author") or {}).get("display_name") or ""
    else:
        return None
    words = fold(name).split()  # a name in a non Latin script folds to nothing
    return words[-1] if words else None


def is_notice(title: str) -> bool:
    return not FLAG_PREFIX.match(title) and bool(NOTICE.match(fold(title)))


def _accept(item: dict, ref: Reference) -> float | None:
    title = title_of(item)
    if not title or is_notice(title) or item.get("type") in SKIP_TYPES:
        return None
    y = year_of(item)
    if ref.year and y and abs(ref.year - y) > 1:
        return None
    score = title_score(title, ref.text)
    if score >= TITLE_MATCH:
        return score
    same_author = bool(ref.surname) and first_surname(item) == ref.surname
    if score >= TITLE_WITH_AUTHOR and same_author and ref.year and y:
        return score
    return None


def _doi_of(item: dict) -> str | None:
    d = item.get("DOI") or item.get("doi")
    return d.lower().removeprefix("https://doi.org/") if d else None


def _search(ref: Reference, lk: Lookup, out: Checked) -> tuple[dict, list[dict]] | None:
    query = ref.text.replace(ref.doi, " ") if ref.doi else ref.text
    candidates = [c for c in lk.crossref_search(query) if year_of(c) or c.get("type") not in SKIP_TYPES]
    out.searched.append("Crossref bibliographic search")
    scored = [(s, c) for c in candidates if (s := _accept(c, ref)) is not None]
    if scored:
        score, best = max(scored, key=lambda sc: (sc[0], sc[1].get("score") or 0))
        out.how, out.match = "crossref_search", round(score, 1)
        return best, candidates
    candidates = lk.openalex_search(title_segment(query) or query)
    out.searched.append("OpenAlex search")
    scored = [(s, c) for c in candidates if (s := _accept(c, ref)) is not None]
    if scored:
        score, best = max(scored, key=lambda sc: sc[0])
        out.how, out.match = "openalex_search", round(score, 1)
        return best, candidates
    return None


def _update_flags(item: dict, doi: str, label: str = "") -> list[Flag]:
    flags = []
    for u in item.get("updated-by") or []:
        kind = UPDATE_KIND.get((u.get("type") or "").lower())
        if not kind:
            continue
        src = "Crossref, from Retraction Watch" if u.get("source") == "retraction-watch" else "Crossref, from the publisher"
        when = ((u.get("updated") or {}).get("date-time") or "")[:10]
        detail = f"{u.get('label') or kind} {when}".strip() + (f", notice {u['DOI']}" if u.get("DOI") else "")
        flags.append(Flag(kind, src, label + detail, doi))
    if m := FLAG_PREFIX.match(title_of(item)):
        flags.append(Flag("retracted", "title of the record",
                          label + f"title starts with {(m.group(1) or m.group(2)).upper()}", doi))
    return flags


def _registry_flag(row: dict, registry: Registry, doi: str, label: str = "") -> Flag | None:
    verdict = row.get("verdict")
    if not verdict or verdict == "ok":
        return None
    who = [n for n, k in (("Retraction Watch", "rw_status"), ("OpenAlex", "flag_openalex"),
                          ("PubMed", "flag_pubmed"), ("PMC", "flag_pmc")) if row.get(k)]
    when = row.get("first_retraction_date")
    detail = f"flagged by {', '.join(who) or 'one source'}" + (f", first retraction {when}" if when else "")
    return Flag(verdict, registry.label, label + detail, doi)


def check(ref: Reference, lk: Lookup, registry: Registry) -> Checked:
    out = Checked(ref.number, ref.text, "not_found")
    item, candidates = None, []
    if ref.doi:
        out.searched.append(f"Crossref DOI {ref.doi}")
        item = lk.crossref_work(ref.doi)
        if item:
            out.how = "doi"
            out.match = round(title_score(title_of(item), ref.text), 1)
            if out.match < 60 and len(ref.text) > len(ref.doi) + 40:
                out.notes.append(f"The DOI points to a paper titled \"{title_of(item)}\", which does not appear in the reference text.")
        elif lk.doi_registered(ref.doi):
            out.searched.append("DOI resolver")
            oa_item = lk.openalex_work(ref.doi)
            item = {**(oa_item or {}), "doi": ref.doi, "title": title_of(oa_item or {}) or None}
            out.how = "doi_other_agency"
            if oa_item and _doi_of(oa_item) and _doi_of(oa_item) != ref.doi:
                out.notes.append(f"OpenAlex lists this work under DOI {_doi_of(oa_item)}.")
        else:
            out.searched.append("DOI resolver")
            out.notes.append(f"The DOI {ref.doi} is not registered with any DOI agency.")
    if item is None:
        hit = _search(ref, lk, out)
        if hit:
            item, candidates = hit
            if ref.doi:
                out.notes.append(f"Matched by title instead, to DOI {_doi_of(item)}.")
    if item is None:
        if ref.doi:
            out.found = "doi_not_registered"
        elif URL.search(ref.text) and not re.search(r"doi\.org|arxiv", ref.text, re.I):
            out.found = "web_source"
            out.notes.append("A web page, dataset or report rather than an indexed paper; open the link to check it.")
        return out

    out.found = "found"
    out.doi = _doi_of(item)
    out.title = FLAG_PREFIX.sub("", title_of(item)) or None
    out.year = year_of(item)
    venue = item.get("container-title")
    out.venue = (venue[0] if isinstance(venue, list) and venue else venue) or None

    oa = lk.openalex_work(out.doi) if out.doi else None
    oa = oa if oa and oa.get("id") else (item if item.get("abstract_inverted_index") is not None else None)
    pmid = ((oa or {}).get("ids") or {}).get("pmid", "") or ""
    pmid = pmid.rsplit("/", 1)[-1] or None

    if out.how in ("openalex_search", "doi_other_agency") and out.doi:
        cr = lk.crossref_work(out.doi)
        item = {**item, **(cr or {})}
    out.flags += _update_flags(item, out.doi)
    if oa and oa.get("is_retracted"):
        out.flags.append(Flag("retracted", "OpenAlex", "is_retracted is true", out.doi))
    if row := registry.get(out.doi, pmid):
        if f := _registry_flag(row, registry, out.doi):
            out.flags.append(f)

    # Same paper under another DOI: check every same-title record from the search.
    nt = norm_title(title_of(item))
    if not candidates and len(nt) >= 12:
        candidates = lk.crossref_search(title_of(item))
    # Some notices reuse the paper's exact title; they are known by their DOI in updated-by.
    notices = {(u.get("DOI") or "").lower() for u in item.get("updated-by") or []}
    for c in candidates:
        d = _doi_of(c)
        if not d or d == out.doi or d in notices or is_notice(title_of(c)):
            continue
        if fuzz.ratio(norm_title(title_of(c)), nt) >= SAME_TITLE:
            label = f"same title, other DOI {d}: "
            out.flags += _update_flags(c, d, label)
            if (row := registry.get(d, None)) and (f := _registry_flag(row, registry, d, label)):
                out.flags.append(f)
    out.status = max((f.kind for f in out.flags), key=lambda k: SEVERITY[k], default="ok")
    if out.status == "reinstated":
        out.status = "ok"

    if item.get("abstract"):
        out.abstract = re.sub(r"^abstract\s*", "", strip_tags(item["abstract"]), flags=re.I)
        out.abstract_source = "Crossref"
    elif oa and (text := inverted_to_text(oa.get("abstract_inverted_index"))):
        out.abstract, out.abstract_source = text, "OpenAlex"
    elif pmid and (text := lk.pubmed_abstract(pmid)):
        out.abstract, out.abstract_source = text, "PubMed"
    return out
