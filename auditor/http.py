"""A small HTTP client on the standard library, copied from afterlife/core/http.py.

Every source in this project is a public endpoint that rate limits, times out,
or returns a transient 5xx now and then.

No contact email is sent with requests. Several APIs ask for one to join a
faster pool; that is a choice for whoever deploys this, made through an
environment variable, never a default baked into the code.
"""

from __future__ import annotations

import gzip
import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

USER_AGENT = "citation-auditor/0.1"
RETRYABLE = {429, 500, 502, 503, 504}


class HttpError(RuntimeError):
    def __init__(self, url: str, status: int, body: bytes = b"") -> None:
        super().__init__(f"HTTP {status} for {url}")
        self.url = url
        self.status = status
        self.body = body


@dataclass
class Response:
    url: str
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> object:
        return json.loads(self.body)


def with_query(url: str, params: dict[str, object] | None) -> str:
    if not params:
        return url
    clean = {k: v for k, v in params.items() if v is not None}
    sep = "&" if urllib.parse.urlparse(url).query else "?"
    return url + sep + urllib.parse.urlencode(clean, safe=":|,")


def get(
    url: str,
    params: dict[str, object] | None = None,
    *,
    timeout: float = 60.0,
    attempts: int = 5,
    backoff: float = 1.5,
    headers: dict[str, str] | None = None,
    sleep=time.sleep,
) -> Response:
    """GET with bounded retries on transient failures.

    Retries cover 429, 5xx, and connection errors. A 4xx other than 429 is a
    bug in the request, so it raises immediately rather than burning attempts.
    """
    full = with_query(url, params)
    hdrs = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}
    hdrs.update(headers or {})
    last: Exception | None = None
    for attempt in range(attempts):
        req = urllib.request.Request(full, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                return Response(full, resp.status, dict(resp.headers.items()), body)
        except urllib.error.HTTPError as exc:
            body = exc.read() if hasattr(exc, "read") else b""
            if exc.code not in RETRYABLE:
                raise HttpError(full, exc.code, body) from exc
            last = HttpError(full, exc.code, body)
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            if retry_after and retry_after.isdigit():
                sleep(min(float(retry_after), 60.0))
                continue
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = exc
        if attempt < attempts - 1:
            sleep(backoff**attempt + random.uniform(0, 0.25))
    assert last is not None
    raise last
