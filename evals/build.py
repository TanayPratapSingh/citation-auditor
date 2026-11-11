"""Build the planted error test draft: evals/cases.json.

    python evals/build.py   (network: OpenAlex and Crossref; seeded, so rerunning gives the same draft
                             while those services return the same records)

Four kinds of reference, each with a known right answer:

  clean        a random OpenAlex article (seeded sample) cited correctly, half with DOI
  near_miss    a clean article cited with two letters swapped, one title word
               dropped, the year off by one and no DOI; the auditor must still find it
  retracted    a random paper Retraction Watch lists as retracted (seeded sample
               from the afterlife registry), cited by its original title, half with DOI
  fabricated   a title spliced from halves of two real titles, real author names
               and journal, half with an invented DOI under a real prefix

Claims: each clean article is cited twice, once for the last sentence of its
own abstract (true) and once for the last sentence of another clean article's
abstract (misattributed). Claims are copied verbatim from abstracts, so the
supported rate is an upper bound; paraphrased claims would be harder.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from auditor.http import get  # noqa: E402
from auditor.lookup import inverted_to_text  # noqa: E402
from auditor.parse import sentences  # noqa: E402
from auditor.verify import FLAG_PREFIX, title_of, year_of  # noqa: E402

SEED = 20261110
N_CLEAN, N_NEAR, N_RETRACTED, N_FAKE = 20, 10, 15, 10
FILTER = "has_abstract:true,type:article,is_retracted:false,has_doi:true,publication_year:2005-2024,language:en"


def apa(authors: list[str], year, title: str, venue: str, doi: str | None) -> str:
    names = []
    for a in authors[:3]:
        parts = a.split()
        names.append(f"{parts[-1]}, {' '.join(p[0] + '.' for p in parts[:-1])}" if len(parts) > 1 else a)
    who = ", ".join(names) + (", et al." if len(authors) > 3 else "")
    return f"{who} ({year}). {title}. {venue}." + (f" https://doi.org/{doi}" if doi else "")


def clean_sample(rng: random.Random) -> list[dict]:
    body = get(
        "https://api.openalex.org/works",
        {"sample": 60, "seed": SEED, "per-page": 60, "filter": FILTER,
         "select": "doi,title,publication_year,authorships,primary_location,abstract_inverted_index"},
    ).json()
    out = []
    for w in body["results"]:
        abstract = inverted_to_text(w.get("abstract_inverted_index")) or ""
        sents = [s for s in sentences(abstract) if 40 < len(s) < 400]
        venue = ((w.get("primary_location") or {}).get("source") or {}).get("display_name")
        authors = [a["author"]["display_name"] for a in w.get("authorships") or []]
        if len(sents) < 3 or not venue or not authors or not w.get("title") or len(w["title"]) < 30:
            continue
        out.append({"doi": w["doi"].removeprefix("https://doi.org/").lower(), "title": w["title"],
                    "year": w["publication_year"], "venue": venue, "authors": authors, "claim": sents[-1]})
    rng.shuffle(out)
    return out


def retracted_sample() -> list[str]:
    rows = duckdb.sql(
        f"select doi from '{ROOT / 'data/registry.parquet'}' where rw_status = 'retracted' and doi is not null "
        f"using sample reservoir(40 rows) repeatable ({SEED % 100000})"
    ).fetchall()
    return [r[0] for r in rows]


def near_miss(rng: random.Random, p: dict) -> str:
    words = p["title"].split()
    words.pop(rng.randrange(1, len(words) - 1))
    j = max(range(len(words)), key=lambda k: len(words[k]))
    w = words[j]
    words[j] = w[:2] + w[3] + w[2] + w[4:] if len(w) > 4 else w
    return apa(p["authors"], p["year"] + rng.choice((-1, 1)), " ".join(words), p["venue"], None)


def fabricate(rng: random.Random, a: dict, b: dict) -> str:
    ta, tb = a["title"].split(), b["title"].split()
    title = " ".join(ta[: max(3, len(ta) // 2)] + tb[len(tb) // 2 :])
    prefix = a["doi"].split("/")[0]
    doi = f"{prefix}/fake.{rng.randrange(10**6, 10**7)}" if rng.random() < 0.5 else None
    return apa(b["authors"][:1] + a["authors"][1:3], a["year"], title, a["venue"], doi)


def main() -> None:
    global SEED
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--out", default="evals/cases.json")
    args = ap.parse_args()
    SEED = args.seed
    rng = random.Random(SEED)
    papers = clean_sample(rng)
    assert len(papers) >= N_CLEAN + N_NEAR + 2 * N_FAKE, f"only {len(papers)} usable clean papers"
    clean, rest = papers[:N_CLEAN], papers[N_CLEAN:]
    refs: list[str] = []
    truth: list[dict] = []
    body: list[str] = []

    def add(text: str, **t) -> int:
        refs.append(text)
        truth.append({"number": len(refs), **t})
        return len(refs)

    for i, p in enumerate(clean):
        add(apa(p["authors"], p["year"], p["title"], p["venue"], p["doi"] if i % 2 == 0 else None),
            kind="clean", doi=p["doi"])
    for p in rest[:N_NEAR]:
        n = add(near_miss(rng, p), kind="near_miss", doi=p["doi"])
        body.append(f"Related work is summarised elsewhere [{n}].")

    retracted = 0
    for doi in retracted_sample():
        if retracted == N_RETRACTED:
            break
        m = get(f"https://api.crossref.org/works/{doi}").json()["message"]
        title = FLAG_PREFIX.sub("", title_of(m)).strip()
        authors = [f"{a.get('given', '')} {a['family']}".strip() for a in m.get("author") or [] if a.get("family")]
        venue = (m.get("container-title") or [None])[0]
        if not (title and authors and venue and year_of(m)) or len(title) < 30:
            continue
        n = add(apa(authors, year_of(m), title, venue, doi if retracted % 2 == 0 else None), kind="retracted", doi=doi)
        body.append(f"Earlier studies reported related findings [{n}].")
        retracted += 1

    pool = rest[N_NEAR:]
    for k in range(N_FAKE):
        n = add(fabricate(rng, pool[2 * k], pool[2 * k + 1]), kind="fabricated", doi=None)
        body.append(f"This has been shown before [{n}].")

    claims = []
    for i, p in enumerate(clean):
        j = (i + 7) % len(clean)
        claims.append({"sentence": f"{p['claim'].rstrip('.')} [{i + 1}].", "ref": i + 1, "truth": "true"})
        claims.append({"sentence": f"{clean[j]['claim'].rstrip('.')} [{i + 1}].", "ref": i + 1,
                       "truth": "misattributed", "true_source": j + 1})
    body += [c["sentence"] for c in claims]
    rng.shuffle(body)

    text = "Introduction\n\n" + "\n".join(body) + "\n\nReferences\n\n"
    text += "\n".join(f"{i}. {r}" for i, r in enumerate(refs, 1))
    out = {"seed": SEED, "text": text, "references": truth, "claims": claims}
    (ROOT / args.out).write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    kinds: dict[str, int] = {}
    for t in truth:
        kinds[t["kind"]] = kinds.get(t["kind"], 0) + 1
    print(kinds, "claims", len(claims))


if __name__ == "__main__":
    main()
