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
    return baseline


def compute_loss(results, baseline, p_wrong=100.0, p_timeout=10.0):
    """Compute a scalar loss from benchmark results and a per-position baseline.

    Lower is better.  `p_wrong` and `p_timeout` are added per position with the
    corresponding status.
    """
    loss = 0.0
    details = {"wrong": 0, "timeout": 0, "ok": 0, "efficiency": 0.0}

    for r in results:
        name = r["name"]
        base = baseline.get(name, 1)
        child = max(r.get("child_evals", 0), 1)
        wrong = r.get("wrong", False)
        timeout = r.get("timeout", False)

        if wrong:
            loss += p_wrong
            details["wrong"] += 1
        elif timeout:
            loss += p_timeout * (1.0 + child / base)
            details["timeout"] += 1
        else:
            loss += math.log(1.0 + child / base)
            details["ok"] += 1
            details["efficiency"] += child / base

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
    params = decode(x)
    if params is None:
        return 1e9, {"status": "invalid-decode"}

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

        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        try:
            with open(output_path) as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            # If we cannot parse JSON, treat as a failed run.
            return 1e9, {"status": "json-error", "returncode": proc.returncode}

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
