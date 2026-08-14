"""CMA-ES tuner for atomic_solver ScorerParams using the benchmark CLI."""

import argparse
import json
import math
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

import cmaes
import eval as eval_mod
from params import SCORER_DEFAULTS, decode, encode, write_toml


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def make_arg_parser():
    p = argparse.ArgumentParser(description="Tune atomic_solver ScorerParams with CMA-ES")
    p.add_argument(
        "--baseline",
        default=os.path.join(ROOT, "baseline_quick.json"),
        help="path to the baseline benchmark JSON",
    )
    p.add_argument(
        "--benchmark",
        default=os.path.join(ROOT, "atomic_solver", "target", "release", "examples", "benchmark"),
        help="path to the benchmark binary",
    )
    p.add_argument("--suite", default="quick", help="benchmark suite")
    p.add_argument("--timeout", type=int, default=3, help="per-position timeout in seconds")
    p.add_argument("--runs", type=int, default=1, help="benchmark runs per position")
    p.add_argument("--max-evals", type=int, default=1200, help="maximum benchmark evaluations")
    p.add_argument("--workers", type=int, default=4, help="parallel evaluation workers")
    p.add_argument("--p-wrong", type=float, default=100.0, help="per-wrong penalty")
    p.add_argument("--p-timeout", type=float, default=10.0, help="per-timeout penalty")
    p.add_argument("--sigma0", type=float, default=None, help="initial step size in log-space (default 0.3; if --resume is given and this is omitted, the previous sigma is reused)")
    p.add_argument("--seed", type=int, default=None, help="random seed")
    p.add_argument(
        "--output-dir",
        default=os.path.join(ROOT, "tools", "runs", datetime.now().strftime("%Y%m%d_%H%M%S")),
        help="directory to write results",
    )
    p.add_argument(
        "--resume",
        default=None,
        help="resume from a previous history.json or best_summary.json (warm start: best_x and optionally sigma are reused; CMA state is reset)",
    )
    p.add_argument(
        "--seed-config",
        dest="seed_config",
        default=None,
        help="start CMA-ES from a TOML config (e.g. an older best_config.toml). Ignored if --resume is given.",
    )
    return p


def save_generation(output_dir, gen, cma, eval_details):
    """Append a generation summary to history.json."""
    history_path = os.path.join(output_dir, "history.json")
    entry = {
        "generation": gen,
        "counteval": cma.counteval,
        "sigma": cma.sigma,
        "best_f": cma.best_f,
        "best_x": cma.best_x,
        "mean_f": cma.history[-1][1] if cma.history else None,
        "details": eval_details,
    }
    if os.path.exists(history_path):
        with open(history_path) as f:
            hist = json.load(f)
    else:
        hist = []
    hist.append(entry)
    with open(history_path, "w") as f:
        json.dump(hist, f, indent=2)


def save_best(output_dir, cma, best_eval_info):
    """Write the best config found so far to TOML and a summary JSON."""
    if cma.best_x is None:
        return
    params = decode(cma.best_x)
    if params is None:
        return
    write_toml(params, os.path.join(output_dir, "best_config.toml"))
    summary = {
        "best_f": cma.best_f,
        "best_x": cma.best_x,
        "sigma": cma.sigma,
        "evals": cma.counteval,
        "params": params,
        "info": best_eval_info,
    }
    with open(os.path.join(output_dir, "best_summary.json"), "w") as f:
        json.dump(summary, f, indent=2)


def main():
    args = make_arg_parser().parse_args()
    os.makedirs(args.output_dir, exist_ok=True)

    if not os.path.exists(args.baseline):
        raise FileNotFoundError(f"baseline not found: {args.baseline}")
    if not os.path.exists(args.benchmark):
        raise FileNotFoundError(f"benchmark binary not found: {args.benchmark}")

    # Fail fast with a helpful message if the binary was built for a different
    # platform (e.g. copied from a Linux container to macOS).
    try:
        subprocess.run(
            [args.benchmark, "--help"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except OSError as exc:
        raise RuntimeError(
            f"Cannot execute benchmark binary {args.benchmark!r}: {exc}. "
            "It may have been built for a different platform. "
            "Rebuild with: cd atomic_solver && cargo clean && "
            "cargo build --release --example benchmark"
        ) from exc

    baseline = eval_mod.load_baseline(args.baseline)
    n = len(SCORER_DEFAULTS)

    x0 = [0.0] * n
    sigma0 = args.sigma0
    resume_info = {}
    if args.resume:
        with open(args.resume) as f:
            data = json.load(f)
        if isinstance(data, list):
            # history.json
            best_entry = min(data, key=lambda e: e.get("best_f", float("inf")))
            x0 = best_entry.get("best_x")
            if x0 is None:
                # Older histories did not store best_x; fall back to the sibling
                # best_summary.json.
                summary_path = os.path.join(os.path.dirname(args.resume), "best_summary.json")
                if os.path.exists(summary_path):
                    with open(summary_path) as f:
                        summary = json.load(f)
                    x0 = summary.get("best_x")
            x0 = x0 or [0.0] * n
            if sigma0 is None:
                sigma0 = best_entry.get("sigma") or 0.3
            resume_info = {"best_f": best_entry.get("best_f"), "source": "history"}
        else:
            # best_summary.json or similar
            x0 = data.get("best_x") or [0.0] * n
            if sigma0 is None:
                sigma0 = data.get("sigma") or 0.3
            resume_info = {"best_f": data.get("best_f"), "source": "summary"}

    seed_info = {}
    if args.seed_config and not args.resume:
        with open(args.seed_config, "rb") as f:
            seed_toml = tomllib.load(f)
        scorer = seed_toml.get("scorer", {})
        x0 = encode(scorer)
        seed_info = {"source": "seed-config", "config": args.seed_config}

    if len(x0) != n:
        print(f"Warning: resume/seed vector length {len(x0)} != {n}; starting from zero vector")
        x0 = [0.0] * n

    if sigma0 is None:
        sigma0 = 0.3

    cma = cmaes.CMAEvolutionStrategy(
        x0,
        sigma0,
        max_evals=args.max_evals,
        seed=args.seed,
    )

    print(f"CMA-ES n={n} lambda={cma.lambd} mu={cma.mu} max_evals={args.max_evals}")
    print(f"output_dir={args.output_dir}")
    print(f"baseline={args.baseline} suite={args.suite} timeout={args.timeout} workers={args.workers}")
    if args.resume:
        print(f"resume={args.resume} source={resume_info.get('source')} start_f={resume_info.get('best_f')} start_sigma={sigma0}")
    elif args.seed_config:
        print(f"seed_config={args.seed_config} start_sigma={sigma0}")

    best_eval_info = {}

    while cma.counteval < args.max_evals:
        gen = cma.generation + 1
        candidates = cma.ask()

        start = time.time()
        eval_args_list = [
            (x, baseline, args.benchmark, args.suite, args.timeout, args.runs, args.p_wrong, args.p_timeout)
            for (x, _, _) in candidates
        ]
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            results = list(pool.map(lambda t: eval_mod.evaluate_candidate(*t), eval_args_list))
        # pool.map preserves order.
        fitnesses = [loss for loss, _ in results]
        details = [d for _, d in results]

        gen_best_idx = min(range(len(fitnesses)), key=lambda i: fitnesses[i])
        old_best_f = cma.best_f
        cma.tell(candidates, fitnesses)

        # Collect detail stats for logging.
        wrong = sum(d.get("wrong", 0) for d in details)
        timeouts = sum(d.get("timeout", 0) for d in details)
        ok = sum(d.get("ok", 0) for d in details)
        total_child = sum(d.get("total_child_evals", 0) for d in details)

        # best_eval_info should describe the global best candidate, which is the
        # generation-best candidate whenever the global best improved.
        if cma.best_f < old_best_f:
            best_eval_info = details[gen_best_idx]

        elapsed = time.time() - start
        print(
            f"gen={gen} evals={cma.counteval} best={cma.best_f:.4f} "
            f"mean={cma.history[-1][1]:.4f} sigma={cma.sigma:.4f} "
            f"ok={ok} wrong={wrong} timeouts={timeouts} "
            f"total_child={total_child} t={elapsed:.1f}s"
        )

        save_generation(args.output_dir, gen, cma, details)
        if gen % 5 == 0 or cma.counteval >= args.max_evals:
            save_best(args.output_dir, cma, best_eval_info)

    save_best(args.output_dir, cma, best_eval_info)
    print("Done. Best config written to", os.path.join(args.output_dir, "best_config.toml"))


if __name__ == "__main__":
    main()
