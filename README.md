# Citation Auditor

Checks every reference in a draft before it goes out: does the paper exist, has it been retracted, and does its abstract state what the citing sentence claims?

Everything runs on a laptop for free. Lookups go to public APIs (Crossref, OpenAlex, PubMed, the DOI resolver). The claim check uses three small local models. No paid LLM is involved.

## Run it

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/streamlit run app.py                          # the app
.venv/bin/python -m auditor.cli draft.pdf               # or the command line; writes reports/
.venv/bin/python -m pytest -q                           # offline tests
```

The first claim check downloads the three models from Hugging Face into the usual cache.

## What it does to each reference

1. **Finds it.**
   - A DOI in the reference is looked up in Crossref. If Crossref doesn't have it, it is checked at the DOI resolver, which covers DataCite, so arXiv and Zenodo work.
   - Without a DOI, the reference string goes to Crossref's bibliographic search, then to OpenAlex search.
   - A candidate is accepted when:
     - its title matches the reference at 90 or more out of 100, or
     - it matches at 75 or more, the first author's surname is the same, and the years differ by at most one.
2. **Checks its status** against four independent signals, each shown with its source:
   - Crossref `updated-by` (Retraction Watch records and publisher notices)
   - OpenAlex `is_retracted`
   - the [afterlife](https://github.com/TanayPratapSingh/afterlife) registry (Retraction Watch, OpenAlex, PubMed and PMC merged per paper)
   - a `RETRACTED:` title prefix
3. **Checks records with the same title too.** Publishers sometimes keep two DOIs for one paper and flag only one. Wakefield 1998 in the Lancet is the example: the unflagged twin DOI is ranked first by Crossref's own search.
4. **Fetches the abstract**, from Crossref, then OpenAlex, then PubMed.

## What it does to each citing sentence

Citation markers (`[3]`, `[1, 4-6]`, `(Smith et al., 2020)`, `Smith and Lee (2019)`, `(Org, n.d.)`) link sentences to references. Each claim then goes through three local models:

| Step | Model | Why this one |
| --- | --- | --- |
| Retrieve | `sentence-transformers/all-MiniLM-L6-v2` | Fast bi-encoder over every abstract sentence and sentence pair in the draft's reference list |
| Rerank | `cross-encoder/ms-marco-MiniLM-L-6-v2` | Orders the top passages by relevance to the claim |
| Judge | `cross-encoder/nli-deberta-v3-base` | Entailment, contradiction or neutral |

The index holds every cited abstract, not only the one a sentence cites. So when the cited paper does not state a claim but another reference in the same list does, the report names that other reference.

Labels:

- **Abstract states this.**
- **Abstract says the opposite.** Used only when the most relevant passage contradicts the claim. When a reference has its own clause in a sentence that cites several papers, the clause and the whole sentence must both contradict.
- **Abstract does not state this.** An abstract is a summary, so this is a prompt to check, never proof.
- **No abstract to check.**

## Wording rules

- A reference is never called fabricated. It is "not found", followed by the list of what was searched.
- Web pages, datasets and reports without a DOI are labelled as web sources to open by hand, not as missing.
- Every verdict quotes the passage or record it rests on.
- The app saves nothing unless you click Save. One button deletes saved reports and the lookup cache. The cache holds only public paper metadata, never the draft.

## Results

The test drafts are built by `evals/build.py` from a seeded random sample. Each has 55 references:

- **20 clean.** Random OpenAlex articles, half cited with a DOI.
- **10 near misses.** Real papers cited with two letters swapped, one title word dropped, the year off by one and no DOI.
- **15 retracted.** A random sample of papers Retraction Watch lists as retracted, cited by their original title.
- **10 invented.** Titles spliced from halves of two real titles, with real author names and journals; half carry a made up DOI under a real prefix.

Each draft also has 40 claims. Each clean paper is cited once for the last sentence of its own abstract and once for the last sentence of another paper's abstract.

The matcher was revised twice while looking at results: once on the development set, and once on the first held out set, which exposed two more bugs. The third set was built only after both revisions and is the honest estimate.

| Metric | Dev set, first run | Dev set, final code | Held out 1, final code | **Final set (untouched)** |
| --- | --- | --- | --- | --- |
| Invented references flagged | 10/10 | 10/10 | 10/10 | **10/10** |
| Real references found with the right DOI | 33/45 | 43/45 | 44/45 | **44/45** |
| Near misses found with the right DOI | 1/10 | 8/10 | 9/10 | **10/10** |
| Retracted papers flagged | 14/15 | 15/15 | 15/15 | **15/15** |
| Clean papers wrongly flagged retracted or concern | 0/30 | 0/30 | 0/30 | **0/30** |
| True claims labelled "states this" | 18/19 | 20/20 | 19/20 | **19/20** |
| Misattributed claims not labelled "states this" | 18/19 | 19/20 | 19/20 | **20/20** |
| Misattributed claims where the true source was named | 16/19 | 19/20 | 18/20 | **20/20** |
| Run file in `runs/` | `20261111T033453Z-eval.json` | `20261111T034857Z-eval-cases.json` | `20261111T034930Z-eval-holdout.json` | `20261111T035354Z-eval-final.json` |

The final set's first, uncached run took 192.9 s and 141 network calls for 55 references (`runs/20261111T034815Z-eval-final.json`, same code apart from fix 3 below, which does not touch single line references).

**Registry ablation.** With the afterlife registry removed, retraction recall is 15/15 on all three sets (the `no_registry` config in each of the run files above). Live Crossref data already flagged every sampled paper. The registry would matter when Crossref lacks a flag that PubMed or OpenAlex has; these samples held no such case, so the registry's value is unmeasured here.

**These numbers flatter the claim check.** The true claims are copied verbatim from abstracts, so the true claim rate is an upper bound. The misattributed claims come from unrelated fields, which makes them easy to reject. Real misattribution is usually a nearby paper on the same topic.

## Real paper test

On the afterlife manuscript (the paper in [afterlife](https://github.com/TanayPratapSingh/afterlife), 18 references):

- 14 references found and 4 labelled web sources.
- All 21 citing sentences linked, with no unmatched markers.
- 0 retractions, which is correct.
- Claims: 6 "states this", 15 "not stated", 7 not checkable because the source has no abstract.

The report quotes the draft, so it stays in the gitignored `reports/` folder.

## What broke on real data, and the fix

| # | Found on | What broke | Fix |
| --- | --- | --- | --- |
| 1 | JASIST PDF | Bullet (`•`) reference lists were read as 2 references instead of 18 | Bullet lists recognised |
| 2 | JASIST PDF | Page numbers inside the reference list joined entries | Lone number lines dropped |
| 3 | JASIST PDF, tests | Line break hyphens split words (`Infor-mation`) and cut DOIs that break after a digit (`s11192-`) | Hyphen kept inside DOIs and URLs, removed inside words |
| 4 | JASIST PDF | Organisation authors (`Retraction Watch Database (2026)`) and PDFs that drop spaces (`TesseractSemantics`) did not link | Match the name against the entry's opening words |
| 5 | JASIST PDF | `Si et al. (2026)` also linked `Singh (2026)` | Whole word match first, prefix only for 5 or more letters |
| 6 | JASIST PDF | `(Retraction Watch, n.d.)` never linked | `n.d.` supported |
| 7 | JASIST PDF | Datasets and GitHub links were reported as "not found" | Labelled web sources |
| 8 | JASIST PDF | arXiv references were not found | arXiv IDs mapped to DataCite DOIs |
| 9 | JASIST PDF | The NLI judge read "Schneider et al. found X" as neutral against a passage stating X (entailment 0.002, versus 0.994 for "They found X") | Narrative citations become "They" or "the study" before judging |
| 10 | JASIST PDF | Passages reporting other numbers read as contradictions | Only the top ranked passage can contradict |
| 11 | JASIST PDF | Sentences citing two papers for two parts were judged whole | Per reference clause, judged together with the whole sentence |
| 12 | Live API | Crossref search ranks Wakefield 1998's unflagged twin DOI first | Same title records are checked for retraction too |
| 13 | Live API | Some retraction notices reuse the paper's exact title | Notices known from `updated-by` are excluded as twins |
| 14 | Dev set | Near misses scored 79 to 89 against the whole reference string | Compare with the title segment; accept 75 or more with a matching author |
| 15 | Dev set | IEEE titles retracted papers `Notice of Retraction: ...`, so the paper was discarded as a notice | That prefix marks a retracted paper |
| 16 | Dev set | A supplementary file DOI (`.s001`) tied with the article | Component and review records skipped |
| 17 | Held out 1 | An invented reference matched the journal's own Crossref record (type `journal`) | Container records skipped |
| 18 | Held out 1 | A two word encyclopedia entry matched the end of a longer title | Substring matches need 80% of the title length |
| 19 | Held out 1 | A family name in a non Latin script crashed the matcher | Handled |
| 20 | NLI check | NLI calls unrelated pairs contradictions (1.0 for a statin sentence against "Vaccines cause autism") | Contradiction needs the reranker to call the passage relevant |

## Known limits

- **Abstracts only.** A claim drawn from a paper's results section reads as "not stated".
- **Paraphrase.** NLI misses some paraphrases: "increased mortality" against an abstract that says "decreased survival" reads as "not stated".
- **Preprints.** A preprint can match instead of the journal version (1 of 45 in the final set, a retracted paper's Research Square preprint, which was still flagged through its same title twin).
- **Sentence splitting.** Text with unusual abbreviations can split a sentence badly. One true claim in the final set was lost this way.
- **Coverage.** Crossref and OpenAlex cover recent English language journals better than books, older work and regional journals, so "not found" is more likely for those.
- **A bad DOI in the source data.** One clean test paper carried a DOI that OpenAlex lists but no agency has registered (`10.29063/ajrh2023/v27i1.6`). Reporting it as unregistered is correct.
- **Registry age.** The afterlife registry is a snapshot (`data/registry.json` gives its date). Live Crossref data covers retractions since then.
- **OpenAlex budget.** Without an API key, OpenAlex shares a small free daily budget per IP address. Set `OPENALEX_API_KEY` if you hit it.

## Layout

| Path | What |
| --- | --- |
| `auditor/parse.py` | Reference list and citation marker parsing |
| `auditor/lookup.py` | Crossref, OpenAlex, PubMed and DOI resolver calls, behind a SQLite cache |
| `auditor/verify.py` | Matching, retraction signals, abstracts |
| `auditor/support.py` | Retrieve, rerank, judge |
| `auditor/audit.py` | Runs everything, report wording, Markdown export |
| `app.py` | Streamlit app |
| `evals/` | Test draft builder, scorer, and the three test drafts |
| `runs/` | Every evaluation result quoted above |
| `scripts/build_registry.py` | Copies the afterlife registry into `data/registry.parquet` (needs a local afterlife build; the parquet is committed so nobody else does) |
