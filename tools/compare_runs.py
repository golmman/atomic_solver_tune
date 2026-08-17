#!/usr/bin/env python3
"""Compare tuning run summaries against their own baselines and each other."""

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


def resolve_baseline(summary, runs_dir_name):
    """Find the baseline JSON to use for a run.

    New summaries store the exact baseline path. Legacy summaries fall back to
    a baseline named after the suite and solver commit.
    """
    baseline_str = summary.get("baseline")
    if baseline_str:
        baseline_path = Path(baseline_str)
        if not baseline_path.is_absolute():
            baseline_path = ROOT / baseline_path
        if baseline_path.exists():
            return baseline_path

    suite = summary.get("suite", "quick")
    # Prefer a baseline named after the suite recorded in the summary.
    baseline_path = ROOT / f"baseline_{suite}_{runs_dir_name}.json"
    if baseline_path.exists():
        return baseline_path

    # Legacy quick-baseline fallback.
    candidates = sorted(ROOT.glob("baseline_quick_*.json"))
    if candidates:
        return candidates[0]

    return None


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

    baselines = {}
    prev_child = {}
    rows = []

    header = (
        f"{'run':<26} "
        f"{'suite':>8} "
        f"{'evals':>6} "
        f"{'best_f':>10} "
        f"{'child_evals':>13} "
        f"{'vs_base%':>9} "
        f"{'vs_prev%':>9} "
        f"{'solved':>6} "
        f"{'wrong':>5} "
        f"{'to':>3}"
    )
    print(header)
    print("-" * len(header))

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
        solved = info.get("ok", 0)
        suite = s.get("suite", "quick")

        baseline_path = resolve_baseline(s, runs_dir.name)
        baseline_child = None
        if baseline_path and baseline_path.exists():
            if baseline_path not in baselines:
                baselines[baseline_path] = load_baseline_total(baseline_path)
            baseline_child = baselines[baseline_path]

        vs_base = ""
        if baseline_child and baseline_child > 0:
            vs_base = f"{(baseline_child - child) / baseline_child * 100:+.2f}"

        prev = prev_child.get(suite)
        vs_prev = ""
        if prev is not None and prev > 0:
            vs_prev = f"{(prev - child) / prev * 100:+.2f}"

        print(
            f"{d.name:<26} "
            f"{suite:>8} "
            f"{evals:>6} "
            f"{best_f:>10.4f} "
            f"{child:>13,} "
            f"{vs_base:>9} "
            f"{vs_prev:>9} "
            f"{solved:>6} "
            f"{wrong:>5} "
            f"{timeouts:>3}"
        )

        prev_child[suite] = child


if __name__ == "__main__":
    main()
