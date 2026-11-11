"""Public lookups: Crossref, OpenAlex, PubMed and the DOI resolver, behind one cache.

The cache is the long term library: metadata of public papers, keyed by DOI or
by a hash of the search string. It never holds the user's draft. Entries older
than MAX_AGE_DAYS are refetched, because retraction status changes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
import urllib.parse
from html import unescape
from pathlib import Path

from auditor import http

CROSSREF = "https://api.crossref.org/works"
OPENALEX = "https://api.openalex.org/works"
EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
HANDLE = "https://doi.org/api/handles/"
MAX_AGE_DAYS = 14
CROSSREF_SELECT = "DOI,title,author,issued,container-title,type,updated-by,abstract,score"
OPENALEX_SELECT = "id,doi,title,publication_year,ids,is_retracted,abstract_inverted_index,authorships,type"


class Cache:
    def __init__(self, path: str | Path | None = None) -> None:
        path = path or os.environ.get("AUDITOR_LIBRARY", Path(__file__).resolve().parents[1] / "data/library.sqlite")
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.execute("create table if not exists cache (key text primary key, value text, fetched real)")

    def get(self, key: str):
        row = self.db.execute("select value, fetched from cache where key = ?", (key,)).fetchone()
        if row and time.time() - row[1] < MAX_AGE_DAYS * 86400:
            return json.loads(row[0])
        return None

    def put(self, key: str, value) -> None:
        self.db.execute("insert or replace into cache values (?, ?, ?)", (key, json.dumps(value), time.time()))
        self.db.commit()

    def clear(self) -> int:
        n = self.db.execute("delete from cache").rowcount
        self.db.commit()
        return n


def _key(kind: str, text: str) -> str:
    return f"{kind}:{hashlib.sha256(text.encode()).hexdigest()[:32]}"


class Lookup:
    """Every network call the auditor makes goes through here."""

    def __init__(self, cache: Cache | None = None, get=http.get, pause: float = 0.25) -> None:
        self.cache = cache or Cache()
        self.get = get
        # shortcut: one request at a time with a pause, fine for one draft; batch jobs need a token bucket
        self.pause = pause
        self.calls = 0

    def _fetch(self, key: str, fn):
        hit = self.cache.get(key)
        if hit is not None:
            return hit["v"]
        time.sleep(self.pause)
        self.calls += 1
        value = fn()
        self.cache.put(key, {"v": value})
        return value

    def _json(self, url: str, params: dict | None = None):
        try:
            return self.get(url, params, timeout=30, attempts=3).json()
        except http.HttpError as exc:
            if exc.status == 404:
                return None
            raise

    def crossref_work(self, doi: str) -> dict | None:
        url = f"{CROSSREF}/{urllib.parse.quote(doi, safe='/()')}"
        body = self._fetch(f"crossref:{doi}", lambda: self._json(url))
        return body["message"] if body else None

    def crossref_search(self, text: str, rows: int = 5) -> list[dict]:
        params = {"query.bibliographic": text[:500], "rows": rows, "select": CROSSREF_SELECT}
        body = self._fetch(_key("crossref-q", text), lambda: self._json(CROSSREF, params))
        return (body or {}).get("message", {}).get("items", [])

    @staticmethod
    def _openalex(params: dict) -> dict:
        key = os.environ.get("OPENALEX_API_KEY")  # optional; without it OpenAlex shares a small daily budget per IP
        return {**params, "api_key": key} if key else params

    def openalex_work(self, doi: str) -> dict | None:
        url = f"{OPENALEX}/doi:{urllib.parse.quote(doi, safe='/()')}"
        return self._fetch(f"openalex:{doi}", lambda: self._json(url, self._openalex({"select": OPENALEX_SELECT})))

    def openalex_search(self, text: str, rows: int = 5) -> list[dict]:
        params = {"search": re.sub(r"[^\w\s]", " ", text)[:300], "per-page": rows, "select": OPENALEX_SELECT}
        params = self._openalex(params)
        body = self._fetch(_key("openalex-q", text), lambda: self._json(OPENALEX, params))
        return (body or {}).get("results", [])

    def doi_registered(self, doi: str) -> bool:
        """True when the DOI exists with any agency (Crossref, DataCite, mEDRA, ...)."""
        body = self._fetch(f"handle:{doi}", lambda: self._json(HANDLE + urllib.parse.quote(doi, safe="/()")))
        return bool(body) and body.get("responseCode") == 1

    def pubmed_abstract(self, pmid: str) -> str | None:
        def fetch():
            params = {"db": "pubmed", "id": pmid, "retmode": "xml", "tool": "citation-auditor"}
            xml = self.get(EFETCH, params, timeout=30, attempts=3).body.decode("utf-8", "replace")
            parts = re.findall(r"<AbstractText[^>]*>(.*?)</AbstractText>", xml, re.S)
            return " ".join(strip_tags(p) for p in parts) or None

        return self._fetch(f"pubmed-abstract:{pmid}", fetch)


def strip_tags(s: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def inverted_to_text(index: dict | None) -> str | None:
    if not index:
        return None
    pos = sorted((p, w) for w, ps in index.items() for p in ps)
    return " ".join(w for _, w in pos)
