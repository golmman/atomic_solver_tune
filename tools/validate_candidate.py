#!/usr/bin/env python3
"""Repeated benchmark validation of a candidate config with a promotion verdict.

child_evals is time-limited, so a single run is noisy.  This runs the candidate
(and the previous best config, if given) `--repeats` times, averages the
results, and reports whether a difference is large enough to trust, plus the
candidate against the recorded baseline.  Verdict logic:

  - More `wrong` answers than the previous config -> DON'T (correctness first).
  - Otherwise the difference must exceed the larger of the two configs' own
    min-max spreads (measured noise) to be PROMOTE or DON'T; else INCONCLUSIVE.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile

BENCHMARK_DEFAULT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "atomic_solver", "target", "release", "examples", "benchmark",
)


def fmt_int(n):
    return f"{int(n):,}"


def fmt_m(n):
    return f"{n / 1e6:.1f}M"


def run_benchmark(benchmark, config, suite, timeout, runs, tag):
    """Run the benchmark once for a config and return its aggregate numbers."""
    config_fd, config_path = tempfile.mkstemp(suffix=".toml", prefix="val_cfg_")
    output_fd, output_path = tempfile.mkstemp(suffix=".json", prefix="val_out_")
    os.close(config_fd)
    os.close(output_fd)
    try:
        with open(config_path, "wb") as f:
            f.write(open(config, "rb").read())
        cmd = [
            benchmark,
            "--config", config_path,
            "--suite", suite,
            "--first-outcome",
            "--timeout", str(timeout),
            "--runs", str(runs),
            "--json",
            "--output-file", output_path,
        ]
        # Generous hard cap: the benchmark has its own per-position timeout.
        hard_timeout = timeout * 60 * runs + 60
        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=hard_timeout,
            )
        except subprocess.TimeoutExpired:
            return {"status": "subprocess-timeout", "tag": tag}
        if proc.returncode != 0:
            return {"status": "benchmark-failed", "returncode": proc.returncode, "tag": tag}
        with open(output_path) as f:
            data = json.load(f)
        agg = data["aggregates"]
        return {
            "status": "ok",
            "tag": tag,
            "child_evals": agg["total_child_evals"],
            "solved": agg["solved"],
            "wrong": agg["wrong"],
            "timeouts": agg["timeouts"],
            "positions": len(data.get("results", [])),
        }
    except (OSError, json.JSONDecodeError) as e:
        return {"status": f"error: {e}", "tag": tag}
    finally:
        for p in (config_path, output_path):
            try:
                os.unlink(p)
            except FileNotFoundError:
                pass


def collect(benchmark, config, suite, timeout, runs, repeats):
    """Run the benchmark `repeats` times, returning (runs, ok_count)."""
    results = []
    ok = 0
    for i in range(repeats):
        r = run_benchmark(benchmark, config, suite, timeout, runs, i + 1)
        if r["status"] == "ok":
            ok += 1
            results.append(r)
        else:
            print(f"  warning: repeat {i + 1} failed ({r['status']})", file=sys.stderr)
    if not results:
        return None, 0
    return results, ok


def summarize(results):
    """Collapse repeated runs into averaged aggregate numbers."""
    n = len(results)
    child = [r["child_evals"] for r in results]
    return {
        "n": n,
        "avg_child": sum(child) / n,
        "min_child": min(child),
        "max_child": max(child),
        "spread_pct": (max(child) - min(child)) / (sum(child) / n) * 100 if n > 1 else 0.0,
        "max_wrong": max(r["wrong"] for r in results),
        "max_timeouts": max(r["timeouts"] for r in results),
        "min_solved": min(r["solved"] for r in results),
        "positions": results[0]["positions"],
    }


def verdict(cand, prev):
    """Decide whether the candidate is worth promoting. Returns (level, text)."""
    if prev is None:
        return "?", "no previous config to compare against"
    if cand["max_wrong"] > prev["max_wrong"]:
        return "DON'T", "candidate has more wrong answers than the previous config"
    if cand["max_wrong"] < prev["max_wrong"]:
        return "PROMOTE", "candidate has fewer wrong answers than the previous config"
    if cand["n"] < 2 or prev["n"] < 2:
        return "INCONCLUSIVE", "fewer than 2 repeats per config; cannot estimate noise"

    delta = cand["avg_child"] - prev["avg_child"]
    delta_pct = delta / prev["avg_child"] * 100
    threshold = max(cand["spread_pct"], prev["spread_pct"])
    if delta < 0 and -delta_pct > threshold:
        return "PROMOTE", f"candidate is better by {abs(delta_pct):.2f}%, outside the measured noise of {threshold:.2f}%"
    if delta > 0 and delta_pct > threshold:
        return "DON'T", f"candidate is worse by {delta_pct:.2f}%, outside the measured noise of {threshold:.2f}%"
    return "INCONCLUSIVE", f"|diff| of {abs(delta_pct):.2f}% is within the measured noise of {threshold:.2f}%"


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--benchmark", default=BENCHMARK_DEFAULT, help="benchmark binary path")
    p.add_argument("--config", required=True, help="candidate config TOML to validate")
    p.add_argument("--suite", default="thorough", help="benchmark suite (quick|thorough)")
    p.add_argument("--timeout", type=int, default=5, help="per-position timeout in seconds")
    p.add_argument("--runs", type=int, default=3, help="benchmark runs per position (time stats only)")
    p.add_argument("--repeats", type=int, default=3, help="number of benchmark invocations per config")
    p.add_argument("--baseline", default=None, help="recorded baseline JSON for context")
    p.add_argument("--previous", default=None, help="previous best config TOML to compare against")
    args = p.parse_args()

    if not os.path.isfile(args.benchmark):
        print(f"error: benchmark not found: {args.benchmark}", file=sys.stderr)
        sys.exit(1)
    if not os.path.isfile(args.config):
        print(f"error: config not found: {args.config}", file=sys.stderr)
        sys.exit(1)
    if args.previous and not os.path.isfile(args.previous):
        print(f"note: previous config not found ({args.previous}); "
              "comparing against baseline only", file=sys.stderr)
        args.previous = None

    print(f"Candidate : {args.config}")
    print(f"Previous  : {args.previous or '(none)'}")
    print(f"Baseline  : {args.baseline or '(none)'}")
    print(f"Suite     : {args.suite}  (timeout {args.timeout}s, runs {args.runs}, "
          f"{args.repeats} repeats each)")
    if args.previous and os.path.abspath(args.previous) == os.path.abspath(args.config):
        print("note: candidate == previous (already promoted); skipping head-to-head")
        args.previous = None
    print()

    print("Collecting candidate runs...")
    cand_runs, cand_ok = collect(args.benchmark, args.config, args.suite,
                                 args.timeout, args.runs, args.repeats)
    cand = summarize(cand_runs) if cand_runs else None
    if cand is None:
        print("error: all candidate runs failed")
        sys.exit(1)

    prev = None
    if args.previous:
        print("Collecting previous config runs...")
        prev_runs, prev_ok = collect(args.benchmark, args.previous, args.suite,
                                     args.timeout, args.runs, args.repeats)
        prev = summarize(prev_runs) if prev_runs else None
        if prev is None:
            print("warning: all previous-config runs failed; skipping head-to-head",
                  file=sys.stderr)
    print()

    # --- Report ---
    def row(label, cand_val, prev_val=""):
        print(f"  {label:<16} {cand_val:>14}  {prev_val:>14}")

    print("metric             candidate        previous")
    print("-" * 48)
    row("repeats", cand["n"], prev["n"] if prev else "")
    row("positions", cand["positions"], prev["positions"] if prev else "")
    row("solved (worst)", f"{cand['min_solved']}/{cand['positions']}",
        f"{prev['min_solved']}/{prev['positions']}" if prev else "")
    row("wrong (worst)", cand["max_wrong"], prev["max_wrong"] if prev else "")
    row("timeouts (worst)", cand["max_timeouts"], prev["max_timeouts"] if prev else "")
    row("child_evals avg", fmt_m(cand["avg_child"]), fmt_m(prev["avg_child"]) if prev else "")
    if cand["n"] > 1:
        row("  min", fmt_m(cand["min_child"]), fmt_m(prev["min_child"]) if prev else "")
        row("  max", fmt_m(cand["max_child"]), fmt_m(prev["max_child"]) if prev else "")
        row("  spread %", f"{cand['spread_pct']:.2f}", f"{prev['spread_pct']:.2f}" if prev else "")

    base_total = None
    if args.baseline and os.path.isfile(args.baseline):
        try:
            with open(args.baseline) as f:
                base_total = json.load(f)["aggregates"]["total_child_evals"]
        except (OSError, KeyError, json.JSONDecodeError) as e:
            print(f"warning: could not read baseline: {e}", file=sys.stderr)
    print()
    if base_total:
        base_pct = (cand["avg_child"] - base_total) / base_total * 100
        print(f"vs baseline (recorded): {fmt_m(cand['avg_child'])} vs {fmt_m(base_total)} "
              f"-> {base_pct:+.2f}%")
    if prev:
        delta_pct = (cand["avg_child"] - prev["avg_child"]) / prev["avg_child"] * 100
        print(f"vs previous  (fresh runs):  {fmt_m(cand['avg_child'])} vs {fmt_m(prev['avg_child'])} "
              f"-> {delta_pct:+.2f}%")
    print()

    level, text = verdict(cand, prev)
    print(f"VERDICT: {level} — {text}")


if __name__ == "__main__":
    sys.exit(main())
