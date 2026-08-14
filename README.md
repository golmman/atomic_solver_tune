# atomic_solver_tune

This repository wraps the `atomic_solver` chess engine with a pure-Python,
stdlib-only CMA-ES tuner for its `ScorerParams` move-ordering weights.

The solver itself is in `atomic_solver/`; the optimizer code is in `tools/`.

## Quick start

```bash
# After a fresh clone, ensure the submodule is present.
make submodule

# Build the benchmark binary and run a short tuning smoke test.
make build
make tune-short

# Run another short iteration, warm-starting from the previous best config.
make tune-short

# Or run a full search.
make tune

# Validate the best config found so far.
make validate-quick
make validate-thorough
```

`make build`, `make tune`, `make tune-short`, and validation targets all depend
on `make submodule`, so the `atomic_solver` submodule is kept at the commit
pinned in the superproject before any Rust code is built.

Each `make tune` / `make tune-short` creates a timestamped directory under
`tools/runs/<solver_commit>/` and updates `tools/runs/<solver_commit>/latest`.
If `latest/history.json` exists, the next run on the same solver version
automatically resumes from the previous best latent vector and sigma.

Baselines and run directories are versioned by the checked-out `atomic_solver`
commit, so results from different solver versions do not overwrite each other.

## Targets

| Target                   | Purpose                                                                         |
| ------------------------ | ------------------------------------------------------------------------------- |
| `make submodule`         | Ensure `atomic_solver` is initialized and at the superproject's pinned commit   |
| `make build`             | Build `atomic_solver/target/release/examples/benchmark`                         |
| `make baseline`          | Generate `baseline_quick_<commit>.json` from the default `config.toml`          |
| `make tune-short`        | Run a 240-evaluation CMA-ES smoke test (~15 min); writes to `tools/runs/<commit>/` and updates `latest` |
| `make tune`              | Run a full 1200-evaluation CMA-ES search (~1 h); auto-resumes from versioned `latest` if present |
| `make validate-quick`    | Benchmark `tools/runs/<solver_commit>/latest/best_config.toml` on `quick`       |
| `make validate-thorough` | Benchmark the latest tuned config on `thorough`                                 |
| `make test`              | Compile-check the Python tools and `cargo check` the solver                     |
| `make clean`             | Remove `tools/runs/`, `tools/__pycache__/`, and `baseline_quick_*.json`          |

## Submodule note

`make submodule` runs `git submodule update --init --recursive`, which checks
out the commit recorded in this repository.  It intentionally does **not** use
`--remote` or `--merge`; pulling the upstream tip automatically would change
the solver under the optimizer and make baselines/tuning results
non-reproducible.  If you want a newer `atomic_solver` version, update the
submodule pin explicitly and commit it, then run `make submodule`.

## Tuning a new solver version

When `atomic_solver` changes, baselines and tuning runs are versioned by the
solver's short commit, so old results are preserved.  The usual flow is:

```bash
# 1. Update the submodule to the desired commit and pin it in the superproject.
cd atomic_solver
git fetch origin
git checkout <new-commit>
cd ..
git add atomic_solver
git commit -m "bump atomic_solver"

# 2. If ScorerParams fields changed, update SCORER_DEFAULTS in tools/params.py.

# 3. Generate a baseline for the new version.
make baseline

# 4. Start from the previous version's best config (optional but recommended).
make tune-short SEED=tools/runs/<old-commit>/latest/best_config.toml

# 5. Continue iterating on the new version.
make tune-short
make validate-quick
```

`SEED=...` maps the old `best_config.toml` back into the current latent space.
Missing keys fall back to the current defaults; obsolete keys are ignored.  If
the parameter dimensions are identical, you can also resume the previous run with
`python3 tools/tune.py --resume tools/runs/<old-commit>/<run>/history.json
--output-dir tools/runs/<new-commit>/...`.

## Optimizer details

The tuner evolves a 19-dimensional latent vector through the benchmark
interface defined in `atomic_solver/docs/spec/optimizer_interface.md`.

- `tools/params.py` maps CMA-ES latent vectors to valid `ScorerParams` TOML.
- `tools/cmaes.py` is a pure-Python CMA-ES implementation.
- `tools/eval.py` runs the benchmark and computes a scalar loss.
- `tools/tune.py` is the main CLI.

The loss is `sum log(1 + child_evals / baseline) + 100 * wrong + 10 * timeout`;
`child_evals` is the preferred deterministic metric from the contract.

## Requirements

- Rust toolchain (`cargo`)
- Python 3.11+ (no third-party packages are used by the tuner)
