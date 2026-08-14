
# AGENTS.md

## Conventions

- Keep source files under ~10 KB. Files larger than 10 KB must include a short
  documented justification in the file header or in `AGENTS.md`. Files larger
  than ~20 KB should normally be split into submodules.
  - this limit does not hold for `docs/`
- Only use reading `git` commands, never writing ones (no `git add`,
  `git rm`, `git commit`, etc.).
- `docs/plans/` contains prompts, implementation plans and reports
  - ignore all `prompt.md` files
  - implementation plans can be found via `find . -type f -name 'plan*.md'`
  - implementation reports can be found via `find . -type f -name 'report*.md'`
  - implementation plans should always be self contained so they can be implemented i a seaparate session
  - the final task of an implementation plan is creating the corresponding implementation report
  - a report should include additional tools/examples used, problems encountered, unresolved parts, missing tests, next steps
  - older plans and reports may not reflect the current state of the application or its goals
- Boy Scout principle: you should leave the codebase as clean or cleaner than you found it

## Tuning workflow

This project tunes `atomic_solver`'s `ScorerParams` through the optimizer
interface contract in `atomic_solver/docs/spec/optimizer_interface.md`.

A pure-Python, stdlib-only CMA-ES tuner lives in `tools/`:

- `tools/params.py` — latent vector to `ScorerParams` TOML mapping, including the
  capture < promotion hierarchy projection.
- `tools/cmaes.py` — CMA-ES implementation (Jacobi eigendecomposition, step-size
  adaptation, covariance update).  No NumPy/SciPy dependency.
- `tools/eval.py` — benchmark subprocess wrapper and loss computation.
- `tools/tune.py` — main CLI; supports parallel evaluation workers.

The root `Makefile` provides phony targets for the common steps:

```bash
make submodule      # ensure the atomic_solver submodule is initialized and at the pinned commit
make build          # build the benchmark binary
make baseline       # generate baseline_quick_<commit>.json from the default config
make tune-short     # 240-evaluation CMA-ES smoke test (~15 min), creates a timestamped run dir under tools/runs/<commit>/ and updates latest/
make tune           # 1200-evaluation CMA-ES run (~1 h); auto-resumes from versioned latest/history.json when present
make validate-quick # benchmark tools/runs/<commit>/latest/best_config.toml on quick
make validate-thorough
make compare        # compare run summaries vs baseline and vs previous run
make promote        # copy latest/best_config.toml to best/best_config_<commit>.toml for VC
make test           # py_compile tools/*.py + cargo check
make clean          # remove tools/runs/, tools/__pycache__/, and baseline_quick_*.json
make distclean      # make clean + cargo clean in atomic_solver (full Rust rebuild)
```

Iteration flow:
- `make tune` and `make tune-short` each create
  `tools/runs/<solver_commit>/run_<timestamp>/` and update the
  `tools/runs/<solver_commit>/latest` symlink to point to it.
- If `tools/runs/<solver_commit>/latest/history.json` already exists, the next
  `make tune` or `make tune-short` on the same solver version automatically
  warm-starts CMA-ES from the previous best latent vector and sigma.
- To tune a newer `atomic_solver` version, update and pin the submodule,
  update `SCORER_DEFAULTS` in `tools/params.py` if the parameter set changed,
  run `make baseline`, and optionally seed from an older best config with
  `make tune-short SEED=tools/runs/<old_commit>/latest/best_config.toml`.

Key tuning facts:

- The tuner fixes the six `pieces.*` values and the two top thresholds
  (`score_winning_capture`, `score_promotion`) in the first pass; it evolves the
  remaining 19 non-piece parameters.
- The loss uses `child_evals / baseline_child_evals` per position plus a strong
  penalty for `wrong` and a smaller penalty for `timeout`, matching the
  optimizer contract's preference for `child_evals` as the deterministic metric.
- `thorough` validation should be run on promising configs; `quick` is the
  training set.
- `make submodule` runs `git submodule update --init --recursive` to keep the
  solver at the commit pinned in this repository.  It intentionally does not
  use `--remote` or `--merge`; pulling the upstream tip automatically would
  change the solver and invalidate baselines/tuning results.
- `tools/tune.py --seed-config` and `make tune SEED=...` encode an older
  `best_config.toml` into the current latent space.  Missing keys fall back to
  the current `SCORER_DEFAULTS`; extra keys are ignored, so a config from an
  older solver version can seed tuning for a newer one.

## Conversational Guidelines

- You are not just a simple coder but a consultant for the user
- Push back if the users ideas or tasks are not sound or need clarification
- Feel free to ask questions where decisions are needed
- Explain the trade-offs for decision options
