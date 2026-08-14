# Report 1: CMA-ES parameter tuner for `atomic_solver`

This report documents the implementation that grew out of
`docs/plans/init/prompt.md`: building an external optimizer that uses the
benchmark interface defined in `atomic_solver/docs/spec/optimizer_interface.md`
to minimize the work required to solve the benchmark suites.

## Summary

A pure-Python, stdlib-only CMA-ES tuner was created under the workspace root.
It treats the solver as a black box, repeatedly invoking the `benchmark`
example with candidate `ScorerParams` TOML configs and optimizing a scalar loss
based on the `child_evals` metric.

A short 240-evaluation run on the `quick` suite produced a config that:

- reduces total `child_evals` on `quick` by ~6.1% (22,378,178 vs 23,828,952),
- keeps 0 wrong outcomes,
- and generalizes to `thorough`: 24/29 solved vs 23/29 for the default, with
  0 wrong and ~2.3% fewer total `child_evals`.

The workflow is wrapped in a root `Makefile` with phony targets for building,
baselining, tuning, validating, and testing. Baselines and run directories are
versioned by the checked-out `atomic_solver` commit so that tuning newer
solver versions does not overwrite older results.

## Files changed / created

New files:

- `Makefile` — build/baseline/tune/validate/test task runner
- `tools/params.py` — latent vector ↔ `ScorerParams` mapping with validation
- `tools/cmaes.py` — pure-Python CMA-ES implementation
- `tools/eval.py` — benchmark subprocess wrapper and loss computation
- `tools/tune.py` — main tuning CLI with parallel evaluation, resume, and
  cross-version seeding
- `.gitignore` — ignores generated tuning artifacts and Python bytecode
- `baseline_quick_c71ba0b.json` — baseline for the current solver commit
- `tools/runs/c71ba0b/run_240/` — artifact directory for the 240-evaluation run
- `tools/runs/c71ba0b/latest` — symlink to the most recent run for this commit

Updated files:

- `README.md` — project overview, quick start, target table, cross-version flow
- `AGENTS.md` — tuning workflow, Makefile targets, cross-version notes

The `atomic_solver/` source was not modified.

## Parameterization

`tools/params.py` exposes a 19-dimensional latent space. For the first pass:

- all six `pieces.*` values are fixed,
- `score_winning_capture` and `score_promotion` are fixed (they are large,
  almost-categorical thresholds),
- the remaining 19 non-piece `ScorerParams` fields are encoded in log-space
  around their default values.

Latent values are decoded with `round(default * exp(xi))`, clamped to safe ranges,
and projected so that the maximum non-winning capture score stays below
`score_promotion` (the capture < promotion hierarchy from
`ScorerParams::validate`). Decoding returns `None` for any candidate that would
overflow or violate the hierarchy; `tune.py` assigns `inf` loss to those
candidates.

## CMA-ES implementation

`tools/cmaes.py` is a self-contained implementation using:

- rank-μ + rank-one covariance update,
- cumulative step-size adaptation,
- Jacobi eigendecomposition for sampling,
- no NumPy/SciPy dependency.

`tune.py` wraps it with a `ThreadPoolExecutor` to evaluate `lambda` candidates
in parallel. The current settings are `n=19`, `lambda=12`, `mu=6`, default
`sigma0=0.3`.

### Loss

```
L = sum_i log(1 + child_evals_i / baseline_child_evals_i)
    + 100 * wrong_count
    + 10 * timeout_count
```

Every evaluated position contributes `log(1 + child_evals / baseline)`, then a
single `P_WRONG = 100` penalty is added for each wrong outcome and a
`P_TIMEOUT = 10` penalty for each timeout.  `child_evals` is the deterministic
metric preferred by the optimizer interface contract, and the strong
`P_WRONG` penalty reflects the contract's correctness priority.

### Resume / seed / cross-version support

- `--resume history.json` (or `best_summary.json`) warm-starts CMA-ES from the
  previous best latent vector and `sigma`. The full CMA state (covariance,
  evolution paths) is reset, so this is a warm start, not a byte-for-byte
  continuation.
- `--seed-config best_config.toml` encodes a TOML config into the current
  latent space and uses it as the CMA-ES mean. Missing keys fall back to the
  current defaults; extra keys are ignored, which lets an older solver version's
  `best_config.toml` seed a newer version even if `ScorerParams` changed.
- `make tune-short SEED=...` is the Makefile convenience wrapper.

## Makefile

The Makefile is DRY'd with two `define` macros (`do_tune`, `do_validate`) and
provides:

| target | purpose |
|--------|---------|
| `make submodule` | `git submodule update --init --recursive` to the pinned commit |
| `make build` | build the release `benchmark` binary |
| `make baseline` | generate `baseline_quick_<commit>.json` |
| `make tune-short` | 240-eval smoke test, resumes from versioned `latest` |
| `make tune` | 1200-eval search, resumes from versioned `latest` |
| `make validate-quick` | benchmark versioned `latest/best_config.toml` on `quick` |
| `make validate-thorough` | benchmark versioned `latest/best_config.toml` on `thorough` |
| `make test` | `py_compile` the Python tools + `cargo check` |
| `make clean` | remove `tools/runs/`, `tools/__pycache__/`, and `baseline_quick_*.json` |

All build/tune/validate targets depend on `make submodule` (as an order-only
prerequisite) so the solver is always at the pinned commit before Rust code is
compiled. The Makefile intentionally does **not** use `git submodule update
--remote --merge`; bumping the submodule is an explicit commit operation to keep
results reproducible.

## Verification performed

- `make test` — Python syntax check and `cargo check` both pass.
- `make -n tune-short` / `make -n validate-quick` — dry-run commands are correct.
- `make baseline` — generated `baseline_quick_c71ba0b.json` with total
  `child_evals` 23,828,952 on `quick`.
- `make validate-quick` against `tools/runs/c71ba0b/latest/best_config.toml` —
  reproduced 22,378,178 total `child_evals`, 0 wrong, 0 timeouts.
- Manual `thorough` validation (timeout 5 s, runs 3) — 24/29 solved, 5 timeouts,
  0 wrong, 129,688,357 total `child_evals` vs 132,799,953 for the default.
- A 12-evaluation `--seed-config` smoke test confirmed that seeding from a
  `best_config.toml` correctly sets the CMA-ES starting point (missing keys fall
  back to defaults; extra keys are ignored).

## Problems encountered and decisions

- **Gradient descent is impossible.** `atomic_solver` is not differentiable, so
  the "gradient descent" in the prompt had to mean zeroth-order/black-box
  optimization. SPSA was considered but rejected because the loss surface is
  noisy and discrete; CMA-ES was chosen for its ability to handle
  non-smoothness and maintain covariance in latent space.
- **Parameter constraints.** `ScorerParams` validation (especially capture <
  promotion and i32 overflow limits) requires projection after decoding. Some
  latent vectors decode to invalid configs and receive `inf` loss.
- **History resume needed `best_x`.** Older `history.json` files did not store
  the best latent vector, so `tune.py` now includes `best_x` in every history
  entry and falls back to `best_summary.json` for older histories.
- **Cross-version reproducibility.** Without versioned baselines, switching
  `atomic_solver` commits would silently compare configs evaluated on different
  solvers. Baselines and run directories are now keyed by the short solver
  commit.

## Unresolved parts / missing tests

- Piece values and the top thresholds (`score_winning_capture`,
  `score_promotion`) are still fixed. A future pass can expand the tuned
  parameter set, but needs extra care for the capture/promotion hierarchy.
- No automated test asserts that the tuned config actually improves over the
  default. The current verification is manual.
- Training is done only on `quick`; `thorough` is used for validation. A much
  longer run could train on `thorough`, but each evaluation is ~5x slower.
- CMA-ES hyperparameters (`lambda`, `mu`, `sigma0`) were not themselves tuned.
- No multi-seed runs were performed; results may vary with random seed.
- True CMA-ES state serialization (covariance, evolution paths) is not
  implemented; only warm starts from `best_x`/`sigma` are supported.

## Next steps

1. Run a full 4-hour (or longer) CMA-ES search from the current best config to
   see if more improvement is possible.
2. Experiment with tuning the six `pieces.*` values and the two top thresholds
   while keeping the hierarchy constraints.
3. Add an automated regression/validation step that compares the tuned config
   against the default on `quick` and `thorough`.
4. Consider training on `thorough` once the search budget allows it.
5. Add a small comparison script that reports per-version improvement from
  `best_summary.json` and `baseline_quick_*.json`.
