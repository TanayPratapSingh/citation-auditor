"""Draw the two portfolio charts from the eval run files in runs/.

    .venv/bin/python scripts/portfolio_charts.py <output dir>

Every bar is read from a run file; nothing is typed in. Needs matplotlib, which
the app does not, so it is not in requirements.txt.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

BG, BLUE, BEIGE, GRAY, INK = "#FCFBF9", "#2F78D4", "#DCD8D0", "#6B6B70", "#1C1C1E"
RUNS = Path(__file__).resolve().parent.parent / "runs"
FINAL = "20261111T035354Z-eval-final.json"
DEV_FIRST = "20261111T033453Z-eval.json"
DEV_FINAL = "20261111T034857Z-eval-cases.json"


def metrics(name: str) -> dict:
    return json.loads((RUNS / name).read_text())["configs"]["full"]["metrics"]


def style(ax) -> None:
    ax.set_facecolor(BG)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(BEIGE)
    ax.tick_params(colors=GRAY, labelsize=13, length=0)


def final(out: Path) -> None:
    m = metrics(FINAL)
    clean = m["clean_false_retraction_or_concern"]
    rows = [
        ("Invented references flagged", m["fabricated_flagged"]),
        ("Real references found, right DOI", m["real_found_right_doi"]),
        ("Typo, wrong year, no DOI: still found", m["near_miss_found_right_doi"]),
        ("Retracted papers flagged", m["retracted_flagged"]),
        ("Clean papers not flagged retracted", {"hit": clean["of"] - clean["hit"], "of": clean["of"]}),
        ("True claims: abstract states this", m["true_claim_supported"]),
        ("Wrong paper cited: claim rejected", m["misattributed_not_supported"]),
        ("Wrong paper cited: true source named", m["misattributed_true_source_named"]),
    ]
    fig, ax = plt.subplots(figsize=(16, 6.5), dpi=100)
    fig.patch.set_facecolor(BG)
    style(ax)
    for i, (_label, v) in enumerate(reversed(rows)):
        share = v["hit"] / v["of"]
        ax.barh(i, 1, color=BEIGE, height=0.62)
        ax.barh(i, share, color=BLUE, height=0.62)
        ax.text(1.015, i, f"{v['hit']} of {v['of']}", va="center", fontsize=13, color=GRAY)
        ax.text(share - 0.012, i, f"{100 * share:.0f}%", va="center", ha="right", fontsize=14, color="white",
                fontweight="bold")
    ax.set_yticks(range(len(rows)), [r[0] for r in reversed(rows)], fontsize=14, color=INK)
    ax.set_xlim(0, 1.14)
    ax.set_xticks([0, 0.25, 0.5, 0.75, 1], ["0%", "25%", "50%", "75%", "100%"])
    ax.set_title("A test draft no fix was tuned on: 55 references and 40 claims, built from a seeded random sample\n"
                 "(the claims are copied from abstracts, so the two claim rows are upper bounds)",
                 loc="left", fontsize=16, color=INK, pad=14)
    fig.tight_layout()
    fig.savefig(out, facecolor=BG)
    plt.close(fig)


def fixes(out: Path) -> None:
    first, last = metrics(DEV_FIRST), metrics(DEV_FINAL)
    rows = [
        ("Typo, wrong year, no DOI: still found", "near_miss_found_right_doi"),
        ("Real references found, right DOI", "real_found_right_doi"),
        ("Retracted papers flagged", "retracted_flagged"),
        ("Invented references flagged", "fabricated_flagged"),
    ]
    fig, ax = plt.subplots(figsize=(16, 6.5), dpi=100)
    fig.patch.set_facecolor(BG)
    style(ax)
    h = 0.36
    for i, (_label, key) in enumerate(reversed(rows)):
        for off, m, color, tag in ((h / 2, first, BEIGE, "first run"), (-h / 2, last, BLUE, "final code")):
            v = m[key]
            share = v["hit"] / v["of"]
            ax.barh(i + off, share, color=color, height=h)
            ax.text(share + 0.01, i + off, f"{v['hit']} of {v['of']}, {tag}", va="center", fontsize=12, color=GRAY)
    ax.set_yticks(range(len(rows)), [r[0] for r in reversed(rows)], fontsize=14, color=INK)
    ax.set_xlim(0, 1.25)
    ax.set_xticks([0, 0.25, 0.5, 0.75, 1], ["0%", "25%", "50%", "75%", "100%"])
    ax.set_title("The draft the matcher was fixed against, before and after the fixes\n"
                 "(same 55 references; these gains are not a fair estimate, the chart above is)",
                 loc="left", fontsize=16, color=INK, pad=14)
    fig.tight_layout()
    fig.savefig(out, facecolor=BG)
    plt.close(fig)


if __name__ == "__main__":
    dest = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    dest.mkdir(parents=True, exist_ok=True)
    final(dest / "citation_auditor_01.png")
    fixes(dest / "citation_auditor_02.png")
    print(f"wrote {dest / 'citation_auditor_01.png'} and {dest / 'citation_auditor_02.png'}")
