# Prompt-Surface Ablation Memo 2026-04-06

## What changed

- Added `prompt_surface_mode` as a first-class experiment variable across runner config, batch config, CLI, DB metadata, and dashboard summaries.
- Implemented three prompt modes:
  - `legacy_menu`
  - `named_enforceable`
  - `free_text_only`
- `legacy_menu` restores both pieces of the old proposal surface:
  - the old prompt text that names the mechanical menu
  - the old completion guided JSON schema that accepts optional `policy_type` and `effect` fields on `propose_policy`
- Added persistent `run_validity` summaries with:
  - initial role counts
  - `mixed_role_present`
  - `mechanical_enactment_rate`
  - `compiled_enactment_rate`
  - `opposition_rate`
  - `abstention_rate`
  - `single_support_enactment_rate`
  - `mean_action_budget_utilization`
  - final concentration metrics
  - warning flags
- Added lightweight `turn_budgets` logging so budget-utilization reporting is grounded in actual per-turn budgets.
- Exposed prompt mode and validity summaries in batch reports and dashboard research views.
- Added focused tests for prompt-mode wiring and validity-flag behavior.

## What stayed fixed

- Core role system is unchanged.
- Vote rule is unchanged.
- Policy compiler semantics are unchanged.
- New research runs are intended to use `4` agents per society so oligarchy realizes `3 oligarchs + 1 citizen`, but preserved earlier runs remain untouched reference artifacts.
- That means `legacy_menu` with `--agents 4` is a forward-looking matched-condition comparison arm, not a direct historical reproduction of `run_004`, which used `3` agents and therefore realized oligarchy as `3 oligarchs / 0 citizens`.

## What was verified locally

- Full test suite passed after implementation: `293 passed`.
- Mixed-role oligarchy is realized at `4` agents per society and still reports `3` governance-eligible agents by default.
- The new validity layer correctly flags:
  - 3-agent all-elite oligarchy
  - zero-enforceable symbolic enactments
  - low-opposition / high-abstention / single-support enactment patterns

## What happened when trying to run the 72B ablation

- The historical Runpod hostname `https://1dw74tjd2yrs4i-8000.proxy.runpod.net/v1` is reachable at the network level.
- The exact OpenAI-compatible paths required for the historical setup now return `404`:
  - `POST /v1/completions`
  - `POST /v1/chat/completions`
- The older recorded host `https://xrgyqtvrvx1joi-8000.proxy.runpod.net/v1` also returns `404` for the same paths.
- A direct pilot attempt from the sandbox degraded into connection errors and heuristic fallbacks.
- That attempt still wrote two DB artifacts under `runs/prompt_surface_ablation_2026_04_06/pilot_legacy_menu/`:
  - `run_000_seed2000.db`
  - `run_001_seed2001.db`
- Those artifacts confirm that the new wiring worked mechanically:
  - `prompt_surface_mode=legacy_menu` persisted in run metadata
  - `--agents 4` realized mixed-role oligarchy as `3 oligarchs + 1 citizen`
  - `run_validity` and `turn_budgets` persisted correctly
- Those artifacts are not usable behavioral evidence:
  - all `120/120` `llm_usage` rows show `fallback_used=1`
  - all token counts are `0`
  - the logged error is `Connection error`
- The apparent mechanical enactments in those DBs (`restrict_archive`, `gather_cap`, `redistribute`, `resource_tax`, `universal_proposal`) therefore reflect heuristic fallback behavior, not a live 72B ablation.

## What can be claimed now

- The ablation infrastructure is implemented and tested.
- The repo now records the key validity failures that previously had to be inferred manually from raw DB inspection.
- The failed `legacy_menu` pilot still exercised the new instrumentation end-to-end, even though it did not produce interpretable model behavior.
- The next live run on a working 72B endpoint can execute the planned three-arm comparison without further code changes.

## What cannot be claimed yet

- No new interpretable 72B prompt-surface ablation results were produced on 2026-04-06.
- The only new DBs written are fallback-only `legacy_menu` pilot artifacts, not valid pilot or decisive batch results.
- The current memo does not establish any new behavioral conclusion about `legacy_menu` vs `named_enforceable` vs `free_text_only`.

## Ready-to-run commands

These are the commands prepared for execution once a working OpenAI-compatible 72B endpoint is available:

All three commands intentionally use `--agents 4` so the comparison is matched on a mixed-role oligarchy. The `legacy_menu` arm here should therefore be read as a forward-looking rerun under corrected conditions, not as a literal replication of historical `run_004`.

```bash
./.venv/bin/python -m src.batch \
  --runs 2 \
  --agents 4 \
  --rounds 5 \
  --base-seed 2000 \
  --output runs/prompt_surface_ablation_2026_04_06/pilot_legacy_menu \
  --strategy llm \
  --model Qwen/Qwen2.5-72B \
  --base-url <LIVE_VLLM_ENDPOINT> \
  --completion \
  --neutral-labels \
  --equal-start \
  --start-resources 100 \
  --total-resources 10000 \
  --prompt-surface-mode legacy_menu
```

```bash
./.venv/bin/python -m src.batch \
  --runs 2 \
  --agents 4 \
  --rounds 5 \
  --base-seed 2000 \
  --output runs/prompt_surface_ablation_2026_04_06/pilot_named_enforceable \
  --strategy llm \
  --model Qwen/Qwen2.5-72B \
  --base-url <LIVE_VLLM_ENDPOINT> \
  --completion \
  --neutral-labels \
  --equal-start \
  --start-resources 100 \
  --total-resources 10000 \
  --prompt-surface-mode named_enforceable
```

```bash
./.venv/bin/python -m src.batch \
  --runs 2 \
  --agents 4 \
  --rounds 5 \
  --base-seed 2000 \
  --output runs/prompt_surface_ablation_2026_04_06/pilot_free_text_only \
  --strategy llm \
  --model Qwen/Qwen2.5-72B \
  --base-url <LIVE_VLLM_ENDPOINT> \
  --completion \
  --neutral-labels \
  --equal-start \
  --start-resources 100 \
  --total-resources 10000 \
  --prompt-surface-mode free_text_only
```

Repeat the same pattern with `--runs 10` and the `main_*` output directories for the decisive batch.
