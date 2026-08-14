#!/usr/bin/env python3
"""Compare two benchmark validation JSON files (e.g. tuned vs default)."""

import argparse
import json
import sys


def load(path):
    with open(path) as f:
        return json.load(f)


def fmt(n, decimals=0):
    if decimals == 0:
        return f"{int(n):,}"
    return f"{n:,.{decimals}f}"


def pct_diff(a, b):
    if b == 0:
        return float("inf") if a else 0.0
    return (a - b) / b * 100


def main():
    parser = argparse.ArgumentParser(
        description="Compare two benchmark JSON files (tuned vs baseline)."
    )
    parser.add_argument("tuned", help="tuned benchmark JSON")
    parser.add_argument("baseline", help="baseline benchmark JSON")
    args = parser.parse_args()

    try:
        tuned = load(args.tuned)
        baseline = load(args.baseline)
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    ta = tuned["aggregates"]
    ba = baseline["aggregates"]

    rows = [
        ("suite", tuned.get("suite", "?"), baseline.get("suite", "?"), ""),
        ("solved", ta["solved"], ba["solved"], f"{ta['solved'] - ba['solved']:+d}"),
        ("wrong", ta["wrong"], ba["wrong"], f"{ta['wrong'] - ba['wrong']:+d}"),
        ("timeouts", ta["timeouts"], ba["timeouts"], f"{ta['timeouts'] - ba['timeouts']:+d}"),
        (
            "child_evals",
            fmt(ta["total_child_evals"]),
            fmt(ba["total_child_evals"]),
            f"{ta['total_child_evals'] - ba['total_child_evals']:+,.0f} ({pct_diff(ta['total_child_evals'], ba['total_child_evals']):+.2f}%)",
        ),
        (
            "total_time (s)",
            fmt(ta["total_time"], 2),
            fmt(ba["total_time"], 2),
            f"{ta['total_time'] - ba['total_time']:+.2f}s ({pct_diff(ta['total_time'], ba['total_time']):+.2f}%)",
        ),
        (
            "mean_pv_len",
            fmt(ta["mean_pv_len"], 2),
            fmt(ba["mean_pv_len"], 2),
            f"{ta['mean_pv_len'] - ba['mean_pv_len']:+.2f} ({pct_diff(ta['mean_pv_len'], ba['mean_pv_len']):+.2f}%)",
        ),
    ]

    col1 = max(len(r[0]) for r in rows)
    col2 = max(len(str(r[1])) for r in rows)
    col3 = max(len(str(r[2])) for r in rows)

    print(f"{'metric':<{col1}}  {'tuned':>{col2}}  {'baseline':>{col3}}  change")
    print("-" * (col1 + col2 + col3 + 18))
    for name, t, b, change in rows:
        print(f"{name:<{col1}}  {str(t):>{col2}}  {str(b):>{col3}}  {change}")


if __name__ == "__main__":
    main()
