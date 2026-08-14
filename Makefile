# Simple task runner for the atomic_solver CMA-ES parameter tuner.
#
# Each tuning run creates a timestamped directory under
# `tools/runs/<solver_commit>/` and updates the
# `tools/runs/<solver_commit>/latest` symlink.  Repeating `make tune` or
# `make tune-short` automatically warm-starts CMA-ES from the previous best
# config found in `latest/history.json`.

.PHONY: build submodule baseline help clean compare distclean test validate-quick validate-thorough tune tune-short

BENCH      := atomic_solver/target/release/examples/benchmark

# Version all generated artifacts by the checked-out atomic_solver commit so
# baselines and tuning runs from different solver versions do not collide.
SOLVER_COMMIT := $(shell git -C atomic_solver rev-parse --short HEAD 2>/dev/null || echo unknown)
BASELINE      := baseline_quick_$(SOLVER_COMMIT).json
RUNS_DIR      := tools/runs/$(SOLVER_COMMIT)
LATEST        := $(RUNS_DIR)/latest
BEST          := $(LATEST)/best_config.toml

SEED       ?=
SEED_FLAG  := $(if $(SEED),--seed-config $(SEED))

PYTHON     := python3
CARGO      := cargo

help:
	@echo "Targets:"
	@echo "  submodule         ensure the atomic_solver submodule is at the pinned commit"
	@echo "  build             build the release benchmark binary"
	@echo "  baseline          generate baseline_quick_<commit>.json from the default config"
	@echo "  tune              run a full CMA-ES tuning search (~1 h), auto-resumes from versioned latest"
	@echo "  tune-short        run a 240-evaluation smoke test (~15 min), auto-resumes from versioned latest"
	@echo "                    (pass SEED=path/to/best_config.toml to seed from an older version)"
	@echo "  validate-quick    validate tools/runs/<commit>/latest/best_config.toml on quick"
	@echo "  validate-thorough validate the latest tuned config on thorough"
	@echo "  compare           compare run summaries vs baseline and vs previous run"
	@echo "  test              quick syntax/check tests"
	@echo "  clean             remove generated runs and baseline files"
	@echo "  distclean         clean + remove atomic_solver/target/ (full Rust rebuild)"

# ---------------------------------------------------------------------------
# Shared recipes (DRY)
# ---------------------------------------------------------------------------

# $(1) suite, $(2) extra benchmark flags
define do_validate
	@if [ -f "$(BEST)" ]; then \
		$(BENCH) --config "$(BEST)" --suite $(1) --json --first-outcome $(2); \
	else \
		echo "No tuned config at $(BEST). Run 'make tune' or 'make tune-short' first."; \
		exit 1; \
	fi
endef

# $(1) extra tune.py flags (e.g. --max-evals 240)
define do_tune
	@mkdir -p $(RUNS_DIR) && \
	RUN_NAME=run_$$(date +%Y%m%d_%H%M%S) && \
	OUT=$(RUNS_DIR)/$$RUN_NAME && \
	RESUME="" && \
	if [ -f "$(LATEST)/history.json" ]; then \
		RESUME="--resume $(LATEST)/history.json"; \
	fi && \
	$(PYTHON) tools/tune.py --baseline $(BASELINE) --timeout 3 --workers 4 \
		$(1) $$RESUME $(SEED_FLAG) --output-dir $$OUT && \
	if [ -d "$(LATEST)" ] && [ ! -L "$(LATEST)" ]; then \
		mv "$(LATEST)" "$(LATEST).bak"; \
	fi && \
	ln -sfn $$RUN_NAME $(LATEST)
endef

# ---------------------------------------------------------------------------
# Submodule / build
# ---------------------------------------------------------------------------

# Keep the submodule at the commit recorded in the superproject.
# Do NOT use --remote here; that would pull the upstream tip and override the
# pinned commit, making tuning results non-reproducible.
submodule:
	git submodule update --init --recursive

# File target: rebuild the benchmark when Rust sources or Cargo.toml change.
RSRC := $(shell find atomic_solver/src atomic_solver/examples -name '*.rs' 2>/dev/null)
CARGO_MANIFEST := atomic_solver/Cargo.toml

$(BENCH): $(RSRC) $(CARGO_MANIFEST) | submodule
	cd atomic_solver && $(CARGO) build --release --example benchmark

build: $(BENCH)

# ---------------------------------------------------------------------------
# Baseline
# ---------------------------------------------------------------------------

baseline: $(BASELINE)

$(BASELINE): atomic_solver/config.toml $(BENCH)
	$(BENCH) --config atomic_solver/config.toml \
		--suite quick --json --first-outcome \
		--timeout 3 --runs 1 \
		--output-file $(BASELINE)

# ---------------------------------------------------------------------------
# Tuning
# ---------------------------------------------------------------------------

tune: baseline
	$(call do_tune,)

tune-short: baseline
	$(call do_tune,--max-evals 240)

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

validate-quick: build
	$(call do_validate,quick,--timeout 3 --runs 1)

validate-thorough: build
	$(call do_validate,thorough,--timeout 5 --runs 3)

# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------

test:
	$(PYTHON) -m py_compile tools/*.py
	rm -rf tools/__pycache__
	cd atomic_solver && $(CARGO) check

compare:
	$(PYTHON) tools/compare_runs.py

clean:
	rm -rf tools/runs
	rm -rf tools/__pycache__
	rm -f baseline_quick*.json

distclean: clean
	cd atomic_solver && $(CARGO) clean
