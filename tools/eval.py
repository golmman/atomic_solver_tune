"""Benchmark evaluation and loss computation for the CMA-ES tuner."""

import json
import math
import os
import subprocess
import tempfile

from params import decode, write_toml


def load_baseline(path):
    """Load baseline child_evals keyed by position name."""
    with open(path) as f:
        data = json.load(f)
    baseline = {}
    for r in data["results"]:
        baseline[r["name"]] = max(r["child_evals"], 1)
    # If the candidate evaluates a position not in the baseline, use the
    # average baseline value instead of 1 to avoid a wildly inflated ratio.
    if baseline:
        baseline["__mean__"] = sum(baseline.values()) / len(baseline)
    return baseline


def compute_loss(results, baseline, p_wrong=100.0, p_timeout=10.0):
    """Compute a scalar loss from benchmark results and a per-position baseline.

    Lower is better.  Every position contributes `log(1 + child_evals / baseline)`;
    `p_wrong` and `p_timeout` are added once per position with the corresponding
    status.  This matches the documented formula and keeps `p_wrong` dominant.
    """
    loss = 0.0
    details = {"wrong": 0, "timeout": 0, "ok": 0, "efficiency": 0.0}

    for r in results:
        name = r["name"]
        base = baseline.get(name, baseline.get("__mean__", 1))
        child = max(r.get("child_evals", 0), 1)
        wrong = r.get("wrong", False)
        timeout = r.get("timeout", False)
        ratio = child / base

        loss += math.log(1.0 + ratio)
        if wrong:
            loss += p_wrong
            details["wrong"] += 1
        elif timeout:
            loss += p_timeout
            details["timeout"] += 1
        else:
            details["ok"] += 1
            details["efficiency"] += ratio

    return loss, details


def evaluate_candidate(
    x,
    baseline,
    benchmark_path,
    suite="quick",
    timeout_sec=3,
    runs=1,
    p_wrong=100.0,
    p_timeout=10.0,
):
    """Run the benchmark for one latent vector and return (loss, details)."""
    try:
        params = decode(x)
    except ValueError:
        # Latent vector length does not match the current parameter set.
        return float("inf"), {"status": "invalid-decode"}

    if params is None:
        return float("inf"), {"status": "invalid-decode"}

    config_fd, config_path = tempfile.mkstemp(suffix=".toml", prefix="cmaes_cfg_")
    output_fd, output_path = tempfile.mkstemp(suffix=".json", prefix="cmaes_out_")
    os.close(config_fd)
    os.close(output_fd)

    try:
        write_toml(params, config_path)

        cmd = [
            benchmark_path,
            "--config",
            config_path,
            "--suite",
            suite,
            "--first-outcome",
            "--timeout",
            str(timeout_sec),
            "--runs",
            str(runs),
            "--json",
            "--output-file",
            output_path,
        ]

        # The benchmark has an internal per-position timeout, but give the whole
        # subprocess a generous hard cap so a pathological config cannot hang the
        # tuning run. The -1 accounts for the __mean__ fallback key; the fixed
        # 40-position minimum covers larger suites even if the baseline is small.
        num_positions = max(len(baseline) - 1, 40)
        hard_timeout = timeout_sec * num_positions * runs + 60

        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=hard_timeout,
            )
        except subprocess.TimeoutExpired:
            return float("inf"), {"status": "subprocess-timeout", "timeout": hard_timeout}

        if proc.returncode != 0:
            return float("inf"), {"status": "benchmark-failed", "returncode": proc.returncode}

        try:
            with open(output_path) as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            # If we cannot parse JSON, treat as a failed run.
            return float("inf"), {"status": "json-error"}

        results = data.get("results", [])
        loss, details = compute_loss(results, baseline, p_wrong, p_timeout)
        details["returncode"] = proc.returncode
        details["total_child_evals"] = sum(r.get("child_evals", 0) for r in results)
        return loss, details

    finally:
        for p in (config_path, output_path):
            try:
                os.unlink(p)
            except FileNotFoundError:
                pass
