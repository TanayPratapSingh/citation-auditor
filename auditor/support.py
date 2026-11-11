"""Does the cited paper's abstract state what the citing sentence claims?

Three free local models, the same pair taught in the IST 688 reranking lab plus
an NLI judge:

  embed    sentence-transformers/all-MiniLM-L6-v2     finds candidate passages fast
  rerank   cross-encoder/ms-marco-MiniLM-L-6-v2        orders them by relevance to the claim
  judge    cross-encoder/nli-deberta-v3-base           entailment, contradiction or neutral

Passages are abstract sentences, every pair of adjacent sentences, and the
title. The index holds every cited paper in the draft, so a claim can also be
checked against references it does not cite: when the cited paper's abstract
does not state the claim but another reference's abstract does, the report says so.

Labels, per cited reference:
  supported      a top passage is entailment (the NLI model's argmax)
  contradicted   no top passage entails, and the passage the reranker ranks
                 first is contradiction and relevant (sigmoid >= 0.5, the
                 model's own decision boundary). Both gates exist because the
                 NLI model calls unrelated pairs, and passages that report
                 other numbers, contradictions.
  not_stated     neither. The abstract is a summary, so this is never proof that
                 the full paper does not support the claim.
  no_abstract    nothing to check against.
"""

from __future__ import annotations

import numpy as np

from auditor.parse import sentences

EMBED = "sentence-transformers/all-MiniLM-L6-v2"
RERANK = "cross-encoder/ms-marco-MiniLM-L-6-v2"
NLI = "cross-encoder/nli-deberta-v3-base"
RETRIEVE_K = 6  # passages per cited paper from the bi-encoder
JUDGE_K = 3  # of those, how many the reranker passes to the judge


def passages(title: str | None, abstract: str) -> list[str]:
    sents = [s for s in sentences(abstract) if len(s) > 20]
    out = list(sents) + [f"{a} {b}" for a, b in zip(sents, sents[1:])]
    return ([title] if title else []) + out


class Support:
    def __init__(self, embed: str = EMBED, rerank: str = RERANK, nli: str = NLI) -> None:
        from sentence_transformers import CrossEncoder, SentenceTransformer

        self.embedder = SentenceTransformer(embed)
        self.reranker = CrossEncoder(rerank)
        self.judge = CrossEncoder(nli)
        labels = self.judge.model.config.id2label
        self.idx = {labels[i].lower(): i for i in labels}
        self.models = {"embed": embed, "rerank": rerank, "nli": nli}
        self.owner: list[int] = []
        self.text: list[str] = []
        self.vecs = np.zeros((0, 1))

    def index(self, papers: dict[int, tuple[str | None, str]]) -> None:
        """papers: reference number -> (title, abstract)."""
        self.owner, self.text = [], []
        for n, (title, abstract) in papers.items():
            for p in passages(title, abstract):
                self.owner.append(n)
                self.text.append(p)
        if self.text:
            self.vecs = self.embedder.encode(self.text, normalize_embeddings=True, batch_size=64)

    def _top(self, claim_vec: np.ndarray, ref: int, k: int) -> list[int]:
        ids = [i for i, o in enumerate(self.owner) if o == ref]
        if not ids:
            return []
        sims = self.vecs[ids] @ claim_vec
        return [ids[j] for j in np.argsort(-sims)[:k]]

    def _judge(self, claim: str, ids: list[int]) -> dict:
        if not ids:
            return {"label": "no_abstract"}
        rel = np.asarray(self.reranker.predict([(claim, self.text[i]) for i in ids]))
        order = [ids[j] for j in np.argsort(-rel)][:JUDGE_K]
        rel_by_id = dict(zip(ids, (1 / (1 + np.exp(-rel))).tolist()))
        probs = self.judge.predict([(self.text[i], claim) for i in order], apply_softmax=True)
        e, c = self.idx["entailment"], self.idx["contradiction"]
        scored = [
            {
                "passage": self.text[i],
                "relevance": round(rel_by_id[i], 3),
                "entailment": round(float(p[e]), 3),
                "contradiction": round(float(p[c]), 3),
                "nli": max(self.idx, key=lambda k: p[self.idx[k]]),
            }
            for i, p in zip(order, probs)
        ]
        entail = [s for s in scored if s["nli"] == "entailment"]
        # Only the most relevant passage can contradict: a lower passage that
        # reports other numbers reads as a contradiction to the NLI model.
        contra = [s for s in scored[:1] if s["nli"] == "contradiction" and s["relevance"] >= 0.5]
        if entail:
            best = max(entail, key=lambda s: s["entailment"])
            return {"label": "supported", "quote": best["passage"], "score": best["entailment"], "passages": scored}
        if contra:
            best = max(contra, key=lambda s: s["contradiction"])
            return {"label": "contradicted", "quote": best["passage"], "score": best["contradiction"], "passages": scored}
        top = scored[0]
        return {"label": "not_stated", "quote": top["passage"], "score": top["entailment"], "passages": scored}

    def _one(self, claim: str, ref: int) -> dict:
        v = self.embedder.encode([claim], normalize_embeddings=True)[0]
        return self._judge(claim, self._top(v, ref, RETRIEVE_K))

    def check(self, claim: str, cited: list[int], scopes: dict | None = None) -> dict:
        """Verdict per cited reference, plus other references whose abstract entails the claim.

        When a reference has its own clause (scopes), both the clause and the
        whole sentence are judged: supported if either is, contradicted only if
        both are, because a false accusation costs more than a missed problem.
        """
        if not self.text:
            return {"per_ref": {str(n): {"label": "no_abstract"} for n in cited}, "elsewhere": []}
        per_ref = {}
        for n in cited:
            whole = self._one(claim, n)
            scope = (scopes or {}).get(n) or (scopes or {}).get(str(n))
            if not scope or whole["label"] in ("supported", "no_abstract"):
                per_ref[str(n)] = whole
                continue
            part = {**self._one(scope, n), "scope": scope}
            if part["label"] == "supported" or (part["label"] == "not_stated" and whole["label"] == "contradicted"):
                per_ref[str(n)] = part
            else:
                per_ref[str(n)] = whole
        elsewhere = []
        if not any(r["label"] == "supported" for r in per_ref.values()):
            v = self.embedder.encode([claim], normalize_embeddings=True)[0]
            others = sorted({o for o in self.owner if o not in cited})
            best = sorted(others, key=lambda n: -float(self.vecs[self._top(v, n, 1)[0]] @ v))[:3]
            for n in best:
                r = self._judge(claim, self._top(v, n, RETRIEVE_K))
                if r["label"] == "supported":
                    elsewhere.append({"ref": n, "quote": r["quote"], "score": r["score"]})
        return {"per_ref": per_ref, "elsewhere": elsewhere}
