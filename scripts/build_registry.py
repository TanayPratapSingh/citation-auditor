"""Copy the afterlife retraction registry into data/registry.parquet.

afterlife merges Retraction Watch, OpenAlex, PubMed and PMC into one row per
paper (gold.registry). This script copies the columns the auditor needs and
writes a provenance file next to it, so every retraction flag in a report can
name the snapshot it came from.

    python scripts/build_registry.py [--gold ../afterlife/data/gold/afterlife.duckdb]
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
COLUMNS = (
    "doi, pmid, title, verdict, rw_status, natures, first_retraction_date, "
    "flag_openalex, flag_pubmed, flag_pmc, n_flagging"
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default=str(ROOT.parent / "afterlife/data/gold/afterlife.duckdb"))
    args = ap.parse_args()
    out = ROOT / "data/registry.parquet"
    con = duckdb.connect(args.gold, read_only=True)
    con.sql(f"copy (select {COLUMNS} from gold.registry) to '{out}' (format parquet)")
    rows = con.sql("select verdict, count(*) from gold.registry group by 1 order by 2 desc").fetchall()
    meta = {
        # relative to the folder holding both repos, so no local home path is published
        "source": os.path.relpath(os.path.abspath(args.gold), ROOT.parent),
        "source_modified": datetime.fromtimestamp(os.path.getmtime(args.gold), UTC).isoformat(),
        "built": datetime.now(UTC).isoformat(),
        "rows": sum(n for _, n in rows),
        "by_verdict": dict(rows),
    }
    (ROOT / "data/registry.json").write_text(json.dumps(meta, indent=2) + "\n")
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
