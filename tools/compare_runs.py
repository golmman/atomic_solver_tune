#!/usr/bin/env python3
"""Compare tuning run summaries against a baseline and each other."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_summary(path):
    with open(path) as f:
        return json.load(f)


def load_baseline_total(path):
    with open(path) as f:
        data = json.load(f)
    return data["aggregates"]["total_child_evals"]


def solver_commit():
    """Return the short commit of the checked-out atomic_solver."""
    try:
        return subprocess.check_output(
            ["git", "-C", str(ROOT / "atomic_solver"), "rev-parse", "--short", "HEAD"],
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def main():
    parser = argparse.ArgumentParser(
        description="Compare tuning run summaries for the same solver version."
    )
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=None,
        help="directory containing run_* folders (default: tools/runs/<solver_commit>)",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help="baseline JSON file (default: baseline_quick_<commit>.json)",
    )
    args = parser.parse_args()

    runs_dir = args.runs_dir
    if runs_dir is None:
        commit = solver_commit()
        runs_dir = ROOT / "tools" / "runs" / commit

    if not runs_dir.is_dir():
        print(f"Runs directory not found: {runs_dir}", file=sys.stderr)
        sys.exit(1)

    run_dirs = sorted(
        p for p in runs_dir.iterdir() if p.is_dir() and p.name.startswith("run_")
    )
    if not run_dirs:
        print(f"No run_* directories in {runs_dir}")
        sys.exit(0)

    # Locate baseline.
    baseline_path = args.baseline
    if baseline_path is None:
        baseline_path = ROOT / f"baseline_quick_{runs_dir.name}.json"
        if not baseline_path.exists():
            candidates = sorted(ROOT.glob("baseline_quick_*.json"))
            if candidates:
                baseline_path = candidates[0]
            else:
                baseline_path = None

    baseline_child = None
    if baseline_path and baseline_path.exists():
        baseline_child = load_baseline_total(baseline_path)
        print(
            f"Baseline: {baseline_path.relative_to(ROOT)}  "
            f"total_child_evals={baseline_child:,}"
        )
    else:
        print("No baseline found; pass --baseline to see vs-baseline improvement.")

    print(
        f"{'run':<26} "
        f"{'evals':>6} "
        f"{'best_f':>10} "
        f"{'child_evals':>13} "
        f"{'vs_base%':>9} "
        f"{'vs_prev%':>9} "
        f"{'wrong':>5} "
        f"{'to':>3}"
    )

    prev_child = None
    for d in run_dirs:
        summary_path = d / "best_summary.json"
        if not summary_path.exists():
            continue
        s = load_summary(summary_path)
        info = s.get("info", {})
        child = info.get("total_child_evals", 0)
        best_f = s.get("best_f", float("nan"))
        evals = s.get("evals", 0)
        wrong = info.get("wrong", 0)
        timeouts = info.get("timeout", 0)

        vs_base = ""
        if baseline_child:
            vs_base = f"{(baseline_child - child) / baseline_child * 100:+.2f}"

        vs_prev = ""
        if prev_child is not None and prev_child > 0:
            vs_prev = f"{(prev_child - child) / prev_child * 100:+.2f}"

        print(
            f"{d.name:<26} "
            f"{evals:>6} "
            f"{best_f:>10.4f} "
            f"{child:>13,} "
            f"{vs_base:>9} "
            f"{vs_prev:>9} "
            f"{wrong:>5} "
            f"{timeouts:>3}"
        )
        prev_child = child


if __name__ == "__main__":
    main()
