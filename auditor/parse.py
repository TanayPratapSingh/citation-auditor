"""Split a draft into body and reference list, and link citing sentences to references.

No model is used here. Reference lists follow a small number of conventions
(numbered "[1]" or "1.", or one entry per paragraph), and citation markers are
either numeric brackets or author and year. Regular expressions handle both,
and every miss is visible in the report as a reference that no sentence cites
or a sentence whose marker matched nothing.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

HEADING = re.compile(
    r"^[ \t]*(?:\d+\.?[ \t]*)?(references|bibliography|works cited|literature cited|reference list)[ \t]*:?[ \t]*$",
    re.I | re.M,
)
NUMBERED = re.compile(r"^[ \t]*(?:\[(\d{1,3})\]|(\d{1,3})\.)[ \t]+", re.M)
BULLET = re.compile(r"^[ \t]*[\u2022\u25cf\u25aa\u2013*-][ \t]+", re.M)
PAGE_NUMBER = re.compile(r"^[ \t]*\d{1,4}[ \t]*$", re.M)
DOI = re.compile(r"10\.\d{4,9}/[^\s\"<>]+", re.I)
ARXIV = re.compile(r"arxiv(?:\.org/abs/|:)\s*(\d{4}\.\d{4,5})", re.I)
URL = re.compile(r"https?:\s*//\S+", re.I)
YEAR = re.compile(r"\b((?:19|20)\d{2})[a-z]?\b")
NUM_MARKER = re.compile(r"\[(\d{1,3}(?:\s*[-–,]\s*\d{1,3})*)\]")
PAREN = re.compile(r"\(([^()]*?(?:\b(?:19|20)\d{2}[a-z]?|n\.d\.)[^()]*)\)")
CITED_YEAR = re.compile(r"\b((?:19|20)\d{2})([a-z]?)\b|n\.d\.")
NARRATIVE = re.compile(
    r"\b((?:[A-Z][\w'\-]+\s+){0,2}[A-Z][\w'\-]+)(?:\s+(?:et al\.?|(?:and|&)\s+[A-Z][\w'\-]+))?\s+\(((?:19|20)\d{2})([a-z]?)\)"
)
ABBREV = ("et al.", "e.g.", "i.e.", "Fig.", "fig.", "vs.", "cf.", "approx.", "Eq.", "No.")
STOPWORDS = {"et", "al", "and", "see", "eg", "ie", "cf", "also", "for", "review", "in"}


@dataclass
class Reference:
    number: int
    text: str
    doi: str | None = None
    year: int | None = None
    surname: str | None = None


@dataclass
class Citation:
    sentence: str
    claim: str
    refs: list[int] = field(default_factory=list)  # Reference.number values
    unmatched: list[str] = field(default_factory=list)  # markers that matched no reference
    scopes: dict[int, str] = field(default_factory=dict)  # reference -> its own clause, when narrower


def _line_break_hyphen(m: re.Match) -> str:
    # "Infor-\nmation" is one word; inside a DOI or URL the hyphen is part of the identifier.
    token = m.group(1)
    keep = "/" in token or "." in token or not (token[-1].isalpha() and m.group(2)[0].islower())
    return token + ("-" if keep else "") + m.group(2)


def clean(text: str) -> str:
    text = re.sub(r"(\S+)-\n\s*(\S)", _line_break_hyphen, text)
    return re.sub(r"\s+", " ", text).strip()


def split_sections(text: str) -> tuple[str, str]:
    """Body and reference list. The last matching heading wins, so a heading
    word that appears earlier in the body does not cut the draft short."""
    matches = list(HEADING.finditer(text))
    if not matches:
        return text, ""
    m = matches[-1]
    return text[: m.start()], text[m.end() :]


def extract_doi(text: str) -> str | None:
    m = DOI.search(text.replace("doi.org/ ", "doi.org/"))
    if not m:
        # arXiv registers a DataCite DOI for every preprint.
        a = ARXIV.search(text)
        return f"10.48550/arxiv.{a.group(1)}" if a else None
    d = m.group(0).rstrip(".,;")
    # A trailing ")" is part of some real DOIs, so drop it only when unbalanced.
    while d.endswith(")") and d.count(")") > d.count("("):
        d = d[:-1].rstrip(".,;")
    return d.lower()


def fold(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def surname(author_segment: str) -> str | None:
    """Family name from 'Smith, J.', 'Smith JA', 'J. A. Smith' or 'van der Berg'."""
    words = [w.strip(".,;:()") for w in author_segment.split()]
    names = [w for w in words if len(w) >= 2 and not (w.isupper() and len(w) <= 3) and w[0].isalpha()]
    names = [w for w in names if fold(w) not in STOPWORDS and w[0].isupper()]
    return fold(names[0]) if names else None


def _reference(number: int, raw: str) -> Reference:
    text = clean(raw)
    year = None
    if "(n.d.)" in text:
        return Reference(number, text, extract_doi(text), None, surname(re.split(r"[,(]", text, maxsplit=1)[0]))
    paren_year = re.search(r"\(((?:19|20)\d{2})[a-z]?\)", text)
    if paren_year:
        year = int(paren_year.group(1))
    elif m := YEAR.search(text):
        year = int(m.group(1))
    return Reference(number, text, extract_doi(text), year, surname(re.split(r"[,(]", text, maxsplit=1)[0]))


def title_segment(text: str) -> str | None:
    """The title part of a reference string, when its style makes that findable.

    APA puts it after "(2020). ", Vancouver and IEEE between the author list and
    the journal; otherwise the longest period separated piece of 4 or more words
    that is not an author list.
    """
    text = URL.sub(" ", text)
    q = re.search(r"[\"\u201c]([^\"\u201d]{15,})[\"\u201d]", text)  # IEEE quotes the title
    if q:
        return q.group(1).strip().rstrip(",.")
    # Vancouver author list: "Baigent C, Keech A, et al. Title..."
    text = re.sub(r"^(?:[A-Z][\w'\-]+(?: [A-Z][\w'\-]+)* [A-Z]{1,3},\s*)*[A-Z][\w'\-]+(?: [A-Z][\w'\-]+)* [A-Z]{1,3}"
                  r"(?:,? et al)?\.\s+", "", text)
    m = re.search(r"\((?:19|20)\d{2}[a-z]?\)\.?\s+(.+?[.?!])(?:\s|$)", text)
    if m and len(m.group(1).split()) >= 3:
        return m.group(1).rstrip(".")
    pieces = [p.strip() for p in re.split(r"(?<=[a-z0-9?!)\]])\.\s+", text)]
    pieces = [p for p in pieces if len(p.split()) >= 4 and not re.search(r"\b[A-Z]\.\s*[A-Z]?\.?,|\bet al\b", p)]
    return max(pieces, key=len) if pieces else None


def split_references(refs_text: str) -> list[Reference]:
    refs_text = PAGE_NUMBER.sub("", refs_text).strip()
    if not refs_text:
        return []
    bullets = list(BULLET.finditer(refs_text))
    if len(bullets) >= 2:
        ends = [b.start() for b in bullets[1:]] + [len(refs_text)]
        return [_reference(i + 1, refs_text[b.end() : e]) for i, (b, e) in enumerate(zip(bullets, ends))]
    # Numbered list: keep only markers that continue the sequence 1, 2, 3, so a
    # wrapped line starting with a volume number ("12. ") does not start an entry.
    starts, expected = [], 1
    for m in NUMBERED.finditer(refs_text):
        if int(m.group(1) or m.group(2)) == expected:
            starts.append(m)
            expected += 1
    if len(starts) >= 2:
        out = []
        for i, m in enumerate(starts):
            end = starts[i + 1].start() if i + 1 < len(starts) else len(refs_text)
            out.append(_reference(i + 1, refs_text[m.end() : end]))
        return out
    blocks = [b for b in re.split(r"\n[ \t]*\n", refs_text) if b.strip()]
    if len(blocks) < 2:
        # One entry per line, joining continuation lines that do not start a new author.
        blocks, cur = [], []
        for line in refs_text.splitlines():
            starts_author = re.match(r"^[A-Z][\w'\-]+,\s+[A-Z]", line.strip())
            if cur and starts_author and cur[-1].rstrip().endswith((".", ")")):
                blocks.append("\n".join(cur))
                cur = []
            if line.strip():
                cur.append(line)
        if cur:
            blocks.append("\n".join(cur))
    return [_reference(i + 1, b) for i, b in enumerate(blocks)]


def sentences(body: str) -> list[str]:
    text = clean(body)
    for a in ABBREV:
        text = text.replace(a, a.replace(".", "\x00"))
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z\[(])", text)
    return [p.replace("\x00", ".").strip() for p in parts if p.strip()]


def expand(spec: str) -> list[int]:
    """'1, 3-5' -> [1, 3, 4, 5]. A range wider than 50 is kept as its two ends."""
    out: list[int] = []
    for part in re.split(r"\s*,\s*", spec.strip()):
        bounds = re.split(r"\s*[-–]\s*", part)
        if len(bounds) == 2:
            a, b = int(bounds[0]), int(bounds[1])
            out.extend(range(a, b + 1) if 0 < b - a < 50 else [a, b])
        else:
            out.append(int(bounds[0]))
    return out


def strip_markers(sentence: str) -> str:
    """The claim as the judge sees it. Narrative citations become "They" or
    "the study": the abstract never names its own authors, and the NLI judge
    reads "Schneider et al. found X" as unsupported by a passage saying X
    (entailment 0.002 with the names, 0.994 with "They", measured 2026-11-10)."""
    claim = NUM_MARKER.sub("", sentence)
    claim = NARRATIVE.sub(lambda m: "They" if m.start() == 0 else "the study", claim)
    claim = PAREN.sub("", claim)
    return re.sub(r"\s+([.,;:])", r"\1", re.sub(r"\s+", " ", claim)).strip()


def match_author(refs: list[Reference], name: str, year: int | None, suffix: str = "") -> list[int]:
    words = name.split()
    for key in dict.fromkeys([name] + words[::-1]):
        if found := _match_key(refs, key, year, suffix):
            return found
    return []


def _match_key(refs: list[Reference], name: str, year: int | None, suffix: str) -> list[int]:
    """References whose opening words contain the cited name and whose year matches.

    Matching words in the opening of the entry rather than one parsed surname
    handles organisations ("Retraction Watch Database (2026)"). A whole word
    must match, so "Si" does not match "Singh"; only when no entry has the
    word does a prefix of 5 or more letters count, for PDFs that drop spaces
    ("TesseractSemantics(2026)"). A letter suffix (2024a) must match when given.
    """
    key = re.sub(r"[^a-z]", "", fold(name))
    pool = []
    for r in refs:
        if r.year != year or not key or (suffix and f"{year}{suffix}" not in r.text):
            continue
        head = re.split(r"\((?:19|20)\d{2}", r.text, maxsplit=1)[0][:80]
        pool.append((r.number, re.findall(r"[a-z]+", fold(head).replace("-", ""))))
    exact = [n for n, words in pool if key in words]
    if exact or len(key) < 5:
        return exact
    return [n for n, words in pool if any(w.startswith(key) for w in words)]


def find_citations(body: str, refs: list[Reference]) -> list[Citation]:
    by_number = {r.number for r in refs}
    out = []
    for s in sentences(body):
        spans: list[tuple[int, int, list[int], bool]] = []  # position, references, narrative?
        unmatched: list[str] = []
        for m in NUM_MARKER.finditer(s):
            nums = expand(m.group(1))
            unmatched += [f"[{n}]" for n in nums if n not in by_number]
            spans.append((m.start(), m.end(), [n for n in nums if n in by_number], False))
        hits: list[tuple[int, int, str, str, str, bool]] = []
        for m in PAREN.finditer(s):
            for part in m.group(1).split(";"):
                y = CITED_YEAR.search(part)
                if y and (name := " ".join(w for w in part[: y.start()].replace(",", " ").split()
                                         if w[0].isupper() and fold(w.strip(".")) not in STOPWORDS)):
                    hits.append((m.start(), m.end(), name, y.group(1), y.group(2) or "", False))
        for m in NARRATIVE.finditer(s):
            # "found X in Smith et al. (2020)" points back; "Smith et al. (2020) found X" points ahead.
            ahead = not re.search(r"\b(?:in|by|from|of|see|per)\s+$", s[: m.start()], re.I)
            hits.append((m.start(), m.end(), m.group(1), m.group(2), m.group(3), ahead))
        for a, b, name, year, suffix, narrative in hits:
            nums = match_author(refs, name, int(year) if year else None, suffix)
            if nums:
                spans.append((a, b, nums, narrative))
            elif not NUM_MARKER.search(s):
                unmatched.append(f"{name} {year or 'n.d.'}{suffix}")
        found = list(dict.fromkeys(n for _, _, ns, _ in spans for n in ns))
        if not (found or unmatched):
            continue
        claim = strip_markers(s)
        # Each reference's own clause. A bracket or parenthetical marker closes the
        # clause before it ("X (A, 2020) and Y [2]"); a narrative marker opens the
        # clause after it ("A et al. (2020) found X"). Bounded by the neighbouring markers.
        spans.sort()
        scopes: dict[int, str] = {}
        for i, (a, b, ns, narrative) in enumerate(spans):
            if narrative:
                hi = spans[i + 1][0] if i + 1 < len(spans) else len(s)
                piece = s[a:hi]
            else:
                lo = spans[i - 1][1] if i else 0
                piece = s[lo:b]
            scope = strip_markers(piece).strip(" ,;:")
            scope = scope[0].upper() + scope[1:] if scope else scope
            for n in ns:
                if len(scope.split()) >= 4 and scope != claim:
                    scopes.setdefault(n, scope)
        out.append(Citation(s, claim, found, list(dict.fromkeys(unmatched)), scopes))
    return out
