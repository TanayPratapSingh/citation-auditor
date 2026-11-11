"""python -m auditor.cli DRAFT.pdf|.txt|.md [--no-claims] [--out reports]

Writes <out>/<timestamp>-<name>.json and .md. reports/ is gitignored because a
report quotes the draft, and drafts are unpublished work.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from auditor import audit


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="auditor")
    ap.add_argument("draft")
    ap.add_argument("--no-claims", action="store_true", help="skip the model based claim check")
    ap.add_argument("--out", default="reports")
    args = ap.parse_args(argv)

    support = None
    if not args.no_claims:
        from auditor.support import Support

        support = Support()
    text = audit.read_text(args.draft)
    report = audit.run(text, support=support, progress=lambda p, msg: print(f"{p:4.0%} {msg}", flush=True))
    report["meta"]["draft"] = Path(args.draft).name
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{Path(args.draft).stem}"
    (out / f"{stem}.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
    (out / f"{stem}.md").write_text(audit.markdown(report))
    print(json.dumps(report["summary"], indent=2))
    print(f"wrote {out / stem}.json and .md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
