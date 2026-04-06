# Run Manifest 2026-04-06

This document freezes the pre-ablation baseline that motivated the prompt-surface work.

## Baseline Snapshot

- Checkout inspected on 2026-04-06.
- Repo HEAD at inspection: `e448502663c783e5a3a1a6931f32e3b58590687f`.
- Working tree before implementation was dirty only in `.gitignore`.
- Preserved reference commits for the key prompt-surface comparison:
  - pre-compiler prompt/menu surface: `ab7f46be1c3655aca82a58f96bbe0dd5f1fb8184`
  - post-compiler prompt surface: `5383ad4f797cbfacaa43c2a229977f26caaeaab2`

## Current Mechanical Baseline

### Role assignment

- `democracy`: every join is `citizen` with `100` starting resources.
- `blank_slate`: every join is `citizen` with `100` starting resources.
- `oligarchy`: first `3` joins are `oligarch` with `500` starting resources; later joins are `citizen` with `10`.

### Vote rule

- Policies from earlier rounds are resolved by `support > oppose` with at least one vote cast.
- No quorum is required.
- A policy can therefore enact on `1 support / 0 oppose / 2 abstain` in a 3-eligible society.

### Action budgets

- `citizen`: `2`
- `leader`: `3`
- `oligarch`: `3`
- destitute agents (`resources <= 0`): `1`

### Compiler behavior

- Explicit `policy_type` proposals are stored as `mechanical`.
- Free-text enacted laws are compiled deterministically by `src/law_compiler.py`.
- Recognized compiled families:
  - `gather_cap`
  - `resource_tax`
  - `redistribute`
  - `restrict_archive`
  - `restrict_direct_messages`
  - `universal_proposal`
  - `grant_moderation`
  - `grant_access` for direct-message visibility
- Enacted laws with no explicit effect and no recognized compiled clauses remain `symbolic`.

## Prompt Surfaces

### Pre-`5383ad4` proposal affordance (`ab7f46be...`)

This is the exact proposal affordance shown in the old prompt surface:

```text
- propose_policy: {"type": "propose_policy", "title": "...", "description": "..."}
  Optionally add policy_type and effect for mechanical enforcement:
    gather_cap: {"max_amount": N}
    resource_tax: {"rate": 0.0-1.0}
    redistribute: {"amount_per_agent": N}
    restrict_archive: {"allowed_roles": ["role"]}
    universal_proposal: {}
    grant_moderation: {"moderator_roles": ["role"]}
    grant_access: {"access_type": "direct_messages", "target_roles": ["role"]}
```

The matching completion schema also allowed optional `policy_type` and `effect` fields for `propose_policy`.

### Post-`5383ad4` proposal affordance (current pre-ablation baseline)

The current free-text prompt surface removed the menu and reduced proposal guidance to:

```text
- propose_policy: {"type": "propose_policy", "title": "...", "description": "..."}
  If a policy is enacted, concrete operational rules stated in the law text may be enforced by the server.
```

The matching completion schema only accepted `type`, `title`, and `description` for `propose_policy`.

## Preserved Run Metadata

### `important_runs/run_004_qwen25_72b_base.db`

- `seed`: `42`
- `strategy`: `llm`
- `model`: `Qwen/Qwen2.5-72B`
- `provider`: `openai_completion`
- `temperature`: `0.7`
- `token_budget`: `8000`
- `neutral_labels`: `true`
- `equal_start`: `true`
- `starting_resources_override`: `100`
- `total_resources_override`: `10000`
- `completion_mode`: `true`
- `base_url`: `https://xrgyqtvrvx1joi-8000.proxy.runpod.net/v1`
- `git_sha`: `ab7f46be1c3655aca82a58f96bbe0dd5f1fb8184`
- `created_at`: `2026-03-24 02:12:21`
- `llm_calls`: `45`
- `fallbacks`: `0`
- realized society shape: `3` agents per society, so oligarchy was `3 oligarchs / 0 citizens`

### `important_runs/run_006_qwen25_72b_base_batch10`

Batch config:

- `num_runs`: `10`
- `agents_per_society`: `3`
- `num_rounds`: `5`
- `base_seed`: `1000`
- `strategy`: `llm`
- `model`: `Qwen/Qwen2.5-72B`
- `provider`: `openai_completion`
- `temperature`: `0.7`
- `token_budget`: `8000`
- `neutral_labels`: `true`
- `equal_start`: `true`
- `starting_resources_override`: `100`
- `total_resources_override`: `10000`
- `completion_mode`: `true`
- `base_url`: `https://1dw74tjd2yrs4i-8000.proxy.runpod.net/v1`
- per-run git SHA: `5383ad4f797cbfacaa43c2a229977f26caaeaab2`
- typical per-run `llm_calls`: `45`
- typical per-run `fallbacks`: `0`
- realized society shape: `3` agents per society, so oligarchy was again `3 oligarchs / 0 citizens`

## Interpretation Freeze

- The engine already supports mixed-role oligarchy when `agents_per_society >= 4`.
- The preserved neutral-label comparison runs did not instantiate that mixed-role condition in practice.
- The `run_004` to `run_006` comparison is prompt-confounded because the policy proposal surface changed between `ab7f46be...` and `5383ad4...`.
- Any forward-looking rerun intended to study oligarchy as rulers-and-ruled should use at least `4` agents per society and record the prompt surface explicitly.
