# Simple task runner for the atomic_solver CMA-ES parameter tuner.
#
# Each tuning run creates a timestamped directory under
# `tools/runs/<solver_commit>/` and updates the
# `tools/runs/<solver_commit>/latest` symlink.  Repeating `make tune` or
# `make tune-short` automatically warm-starts CMA-ES from the previous best
# config found in `latest/history.json`.

.PHONY: build submodule baseline baseline-thorough help clean compare distclean promote test validate-quick validate-quick-summary validate-thorough validate-thorough-summary tune tune-short tune-thorough tune-thorough-short

BENCH      := atomic_solver/target/release/examples/benchmark

# Version all generated artifacts by the checked-out atomic_solver commit so
# baselines and tuning runs from different solver versions do not collide.
SOLVER_COMMIT := $(shell git -C atomic_solver rev-parse --short HEAD 2>/dev/null || echo unknown)
BASELINE       := baseline_quick_$(SOLVER_COMMIT).json
BASELINE_THOROUGH := baseline_thorough_$(SOLVER_COMMIT).json
RUNS_DIR       := tools/runs/$(SOLVER_COMMIT)
LATEST         := $(RUNS_DIR)/latest
LATEST_THOROUGH := $(RUNS_DIR)/latest-thorough
BEST           := $(LATEST)/best_config.toml
BEST_THOROUGH  := $(LATEST_THOROUGH)/best_config.toml
PROMOTED       := best/best_config_$(SOLVER_COMMIT).toml

# For thorough validation, prefer the thorough-tuned config if it exists;
# otherwise fall back to the quick-tuned config.
VALIDATE_THOROUGH_BEST = $(shell if [ -f "$(BEST_THOROUGH)" ]; then echo "$(BEST_THOROUGH)"; else echo "$(BEST)"; fi)

SEED       ?=
SEED_FLAG  := $(if $(SEED),--seed-config $(SEED))

PYTHON     := python3
CARGO      := cargo

help:
	@echo "Targets:"
	@echo "  submodule         ensure the atomic_solver submodule is at the pinned commit"
	@echo "  build             build the release benchmark binary"
	@echo "  baseline          generate baseline_quick_<commit>.json from the default config"
	@echo "  baseline-thorough generate baseline_thorough_<commit>.json from the default config"
	@echo "  tune              run a full CMA-ES tuning search (~1 h) on quick, auto-resumes from versioned latest"
	@echo "  tune-short        run a 240-evaluation smoke test (~15 min) on quick, auto-resumes from versioned latest"
	@echo "  tune-thorough     run a full CMA-ES tuning search (~3 h) on thorough, auto-resumes from versioned latest-thorough"
	@echo "  tune-thorough-short run a 240-evaluation smoke test (~30 min) on thorough, auto-resumes from versioned latest-thorough"
	@echo "                    (pass SEED=path/to/best_config.toml to seed from an older version)"
	@echo "  validate-quick         validate tools/runs/<commit>/latest/best_config.toml on quick"
	@echo "  validate-quick-summary validate the latest tuned config on quick and print a summary"
	@echo "  validate-thorough      validate the latest tuned config on thorough"
	@echo "  validate-thorough-summary validate the latest tuned config on thorough and print a summary"
	@echo "  compare           compare run summaries vs baseline and vs previous run"
	@echo "  promote           copy latest/best_config.toml to best/best_config_<commit>.toml for VC"
	@echo "  test              quick syntax/check tests"
	@echo "  clean             remove generated runs and baseline files"
	@echo "  distclean         clean + remove atomic_solver/target/ (full Rust rebuild)"

# ---------------------------------------------------------------------------
# Shared recipes (DRY)
# ---------------------------------------------------------------------------

# $(1) suite, $(2) extra benchmark flags, $(3) best config path
define do_validate
	@if [ -f "$(3)" ]; then \
		$(BENCH) --config "$(3)" --suite $(1) --json --first-outcome $(2); \
	else \
		echo "No tuned config at $(3). Run 'make tune' or 'make tune-thorough' first."; \
		exit 1; \
	fi
endef

# $(1) suite, $(2) extra benchmark flags, $(3) best config path
define do_validate_summary
	@if [ -f "$(3)" ]; then \
		out=$$(mktemp); \
		$(BENCH) --config "$(3)" --suite $(1) --json --first-outcome $(2) > $$out 2>/dev/null; \
		$(PYTHON) tools/summarize_validation.py $$out; \
		rm -f $$out; \
	else \
		echo "No tuned config at $(3). Run 'make tune' or 'make tune-thorough' first."; \
		exit 1; \
	fi
endef

# $(1) extra tune.py flags, $(2) baseline file, $(3) timeout, $(4) suite, $(5) latest symlink
define do_tune
	@mkdir -p $(RUNS_DIR) && \
	RUN_NAME=run_$$(date +%Y%m%d_%H%M%S) && \
	OUT=$(RUNS_DIR)/$$RUN_NAME && \
	RESUME="" && \
	if [ -f "$(5)/history.json" ]; then \
		RESUME="--resume $(5)/history.json"; \
	fi && \
	$(PYTHON) tools/tune.py --baseline $(2) --suite $(4) --timeout $(3) --workers 4 \
		$(1) $$RESUME $(SEED_FLAG) --output-dir $$OUT && \
	if [ -d "$(5)" ] && [ ! -L "$(5)" ]; then \
		mv "$(5)" "$(5).bak"; \
	fi && \
	ln -sfn $$RUN_NAME $(5)
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

baseline-thorough: $(BASELINE_THOROUGH)

$(BASELINE): atomic_solver/config.toml $(BENCH)
	$(BENCH) --config atomic_solver/config.toml \
		--suite quick --json --first-outcome \
		--timeout 3 --runs 1 \
		--output-file $(BASELINE)

$(BASELINE_THOROUGH): atomic_solver/config.toml $(BENCH)
	$(BENCH) --config atomic_solver/config.toml \
		--suite thorough --json --first-outcome \
		--timeout 5 --runs 3 \
		--output-file $(BASELINE_THOROUGH)

# ---------------------------------------------------------------------------
# Tuning
# ---------------------------------------------------------------------------

tune: baseline
	$(call do_tune,,$(BASELINE),3,quick,$(LATEST))

tune-short: baseline
	$(call do_tune,--max-evals 240,$(BASELINE),3,quick,$(LATEST))

tune-thorough: baseline-thorough
	$(call do_tune,,$(BASELINE_THOROUGH),5,thorough,$(LATEST_THOROUGH))

tune-thorough-short: baseline-thorough
	$(call do_tune,--max-evals 240,$(BASELINE_THOROUGH),5,thorough,$(LATEST_THOROUGH))

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

validate-quick: build
	$(call do_validate,quick,--timeout 3 --runs 1,$(BEST))

validate-quick-summary: build
	$(call do_validate_summary,quick,--timeout 3 --runs 1,$(BEST))

validate-thorough: build
	$(call do_validate,thorough,--timeout 5 --runs 3,$(VALIDATE_THOROUGH_BEST))

validate-thorough-summary: build
	$(call do_validate_summary,thorough,--timeout 5 --runs 3,$(VALIDATE_THOROUGH_BEST))

# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------

test:
	$(PYTHON) -m py_compile tools/*.py
	rm -rf tools/__pycache__
	cd atomic_solver && $(CARGO) check

compare:
	$(PYTHON) tools/compare_runs.py

promote:
	@if [ -f "$(BEST)" ]; then \
		mkdir -p $(dir $(PROMOTED)); \
		cp "$(BEST)" "$(PROMOTED)"; \
		echo "Promoted $(BEST) -> $(PROMOTED)"; \
	else \
		echo "No tuned config at $(BEST). Run 'make tune' or 'make tune-short' first."; \
		exit 1; \
	fi

clean:
	rm -rf tools/runs
	rm -rf tools/__pycache__
	rm -f baseline_*.json

distclean: clean
	cd atomic_solver && $(CARGO) clean
