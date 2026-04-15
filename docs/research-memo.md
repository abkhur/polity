# Polity Research Memo

## One-Sentence Summary

Polity is a round-based multi-agent institutional sandbox for exploring whether harmful social orders might emerge from interacting LLM agents under scarcity, unequal power, contested communication, and persistent memory.

This memo intentionally separates what the platform already does from what the current experiments only suggest.

## Why This Project Exists

Most alignment work studies failure at the level of a single model: refusal behavior, jailbreak robustness, or deceptive behavior by an individual agent. Polity is built around a different question:

> What if alignment can fail at the level of institutions, incentives, and social organization even when individual agents appear locally aligned?

The project treats governance structure as an experimental variable. Agents are placed into different institutional settings and allowed to communicate, gather resources, propose policies, vote, and write to a persistent archive. The goal is to see whether different structures produce different institutional trajectories, while being honest about when the answer is still unclear.

This is not primarily a project about making agents say bad things. It is a project about whether coercive, exclusionary, or manipulative social orders might emerge from the substrate itself under controlled conditions.

## Core Research Question

What happens when individually constrained LLM agents are embedded in social conditions that reward hierarchy, coercion, information control, and competition?

Subquestions include:

- Do resource-scarce oligarchies drift toward censorship, elite closure, or centralized control?
- Do democracies remain democratic under scarcity or external pressure?
- Do agents discover propaganda, surveillance, or narrative control as useful tools of coordination?
- Does control over institutional memory become a source of power?

## What Makes Polity Different

Most adjacent agent projects either study social behavior in a flat environment or analyze task-oriented coordination. Polity differs in four ways:

1. Governance is a mechanical independent variable, not just narrative flavor.
2. The substrate is institutional, not merely conversational.
3. Runs are replayable and auditable through structured event logs.
4. The same platform supports both open exploratory runs and more controlled repeated experiments.

In practice, that makes Polity closer to an experimental political-economy sandbox than to a general-purpose agent framework.

## Current Implementation

The codebase currently includes:

- A deterministic round-based simulation engine
- Three governance conditions: `democracy`, `oligarchy`, `blank_slate`
- Structured actions for communication, resource gathering, policy proposals, votes, and archive writes
- Eight mechanical policy types with real effects on simulation state
- Policy rows tagged as `mechanical`, `compiled`, or `symbolic` for cleaner downstream analysis, with enacted free-text laws compiled deterministically when the server recognizes them
- Role-based permissions and configurable starting conditions
- An ablation runner with `--equal-start`, `--start-resources`, `--total-resources`, `--neutral-labels`, and `--prompt-surface-mode` (legacy_menu / named_enforceable / free_text_only)
- LLM integration (OpenAI and Anthropic) with automatic fallback to heuristic agents
- Tiered context assembly with token budgeting and semantic retrieval
- Ideology drift tracking via sentence-transformer embeddings, with deterministic local fallback embeddings if the model is unavailable
- A Starlette dashboard for replay and comparative inspection
- A headless simulation runner with zero-cost heuristic agents for baseline runs
- A batch runner that can record full LLM configuration, not just heuristic runs
- Per-run metadata persistence (seed, strategy, model/provider, neutral-label flag, overrides, git SHA)
- Hundreds of automated tests covering the simulation stack

## Empirical Results So Far

The empirical story is interesting but still preliminary. The evidence base now includes six zero-fallback single-run LLM conditions in `important_runs/`, one 10-seed free-text batch (`run_006`), and two 5-seed prompt-surface ablation batches under the restored legacy_menu surface (`runs/prompt_surface_ablation_2026_04_13/`). Outside the preserved snapshot, broader local workspaces may also include heuristic baselines, duplicate DB copies, extra exploratory Claude runs, and fallback-only prompt-surface pilot artifacts, so the safest way to read the evidence is still as descriptive case studies plus working interpretations rather than settled results.

### First LLM Run (Labeled)

A 5-round Claude Sonnet proof-of-concept (3 agents per society, 45 API calls, zero fallbacks) produced behavior that looked governance-appropriate: oligarchs coordinated privately, democrats communicated publicly, and blank-slate agents converged on participation rights.

That run is best treated as a qualitative smoke test rather than a clean result. The primary confound is label leakage: the prompt explicitly called agents "oligarchs" or "citizens," so the observed divergence cannot be separated from vocabulary priming. Repo-wide, the Claude evidence now looks framing-sensitive but also high-variance, not fully deterministic.

### Neutral Label Ablation

The next Claude run replaced normatively loaded identifiers with sterile ones (`oligarch` -> `role-A`, `democracy_1` -> `society-alpha`) while keeping permissions identical and equalizing starting resources.

In that run, most of the earlier divergence disappeared. All three societies proposed broadly cooperative policies, the oligarchy proposed `Universal Proposal Rights`, and no society used DMs. The strongest current takeaway is that label leakage is large enough to dominate the present Claude setup. A smaller residual inequality signal may remain, but it is weak and still needs replication.

### Three-Model Comparison Under Neutral Labels

The next stage compared three neutral-label runs on the same infrastructure:

- `Qwen3-30B-A3B` -- a smaller MoE base model
- `Qwen2.5-72B` -- a dense true base model
- `Qwen2.5-72B-Instruct-abliterated` -- an instruction-tuned model with safety training specifically removed

That comparison complicates the simple "base models show structural effects, RLHF removes them" story.

The strongest new result came from the 72B true base model. In that run (`run_004_qwen25_72b_base.db`), the oligarchy enacted `Grant Moderation to Role-A Agents`, while the democracy drifted to the highest inequality in the preserved comparison set. The same oligarchy also passed several control-flavored title-only policies, including `Restrict Direct Messages`, but in the DB that particular policy has no mechanical effect. The moderation grant is still the clearest example in the dataset of agents using structural asymmetry to expand privileged control under neutral labels, though it resolved in the final round, so the preserved run shows the power-expanding move itself more clearly than any downstream exercise of that power.

At the same time, the abliterated 72B instruct model looked much closer to Claude than to the true base model: low inequality, high governance activity, and broadly cooperative policy proposals across all societies. That makes the current leading interpretation more specific than the earlier RLHF story.

**Replication attempt and prompt-surface confound:** a 10-seed follow-up batch (`run_006_qwen25_72b_base_batch10`, seeds 1000-1009) under the post-`5383ad4` free-text prompt surface produced 0/10 repeats of the moderation grant and `93/93` symbolic-only enactments. That appeared to demote the original result, but the prompt surface had changed: the explicit menu of mechanical policy types was removed.

**Prompt-surface ablation (2026-04-13):** Two 5-seed batches restored the legacy menu prompt surface on the same Qwen2.5-72B base model. This narrowed the confound substantially:

- The legacy menu restores abundant mechanical policy proposals (gather caps, taxes, redistributions, moderation grants, access grants) that the free-text surface produced zero of. The prompt surface is confirmed as the dominant variable.
- Explicit `grant_access` / `grant_moderation` proposals reappeared in 4/10 oligarchy seeds across the legacy-menu batches. Two were `grant_moderation` proposals and neither enacted; one of those two granted moderation to both `oligarch` and `citizen`, so it was not a pure oligarch-only consolidation move. The clearest asymmetric result came from the 4-agent mixed-role oligarchy (3 oligarchs + 1 citizen): seed 1000 enacted `Grant Access to Role-A Agents`, restricting DM inspection to oligarchs only. By contrast, seed 1004's "Role-A" gather-cap titles compiled to ordinary society-wide `gather_cap` effects, not role-targeted enforcement.
- The democracy > oligarchy Gini direction held across all batches (free-text 7/10 seeds; legacy_menu 3-agent: 0.123 vs 0.062; legacy_menu 4-agent: 0.157 vs 0.081).
- The original `run_004` is best understood as an upper-tail draw that combined a favorable seed with the menu affordance. Explicit power-expanding proposals do recur under the legacy menu, but only one oligarch-only policy of that class enacted in the 10-seed ablation.

**Working interpretation:** instruction tuning itself may be introducing a strong cooperative prior that flattens structural differentiation in these short runs. The true base 72B result remains the most interesting counterexample. The prompt surface for structured actions is itself a meaningful experimental variable — showing agents a menu of power-expanding verbs is a real affordance, not just incidental scaffolding.

Full analysis with round-by-round metrics and caveats: [docs/findings.md](findings.md)

## Why This Matters

If harmful outcomes can sometimes arise from structure rather than only from individual model behavior, then current safety evaluation is incomplete. Systems composed of multiple agents may need to be evaluated not just for refusal behavior, but also for the kinds of institutions and equilibria they stabilize.

This matters for:

- Multi-agent coding and orchestration systems
- Open agent ecosystems where users connect their own agents
- AI systems with role differentiation, delegated authority, or persistent shared memory
- Alignment research that currently assumes the single-model lens is sufficient

The strongest defensible claim today is not that LLMs reproduce human history one-to-one. It is that multi-agent evaluation appears highly sensitive to three independent variables: vocabulary framing, model training regime, and action-prompt surface. Labeling can dominate outcomes; instruction tuning may suppress structural divergence that a sufficiently capable true base model can still show; and the affordances presented in the action prompt (a structured menu vs. free-text) determine whether agents even attempt mechanical power-consolidation. Under legacy-menu prompting, explicit `grant_access` / `grant_moderation` proposals appear in 4/10 oligarchy seeds across the current ablation batches, but only one oligarch-only policy of that class enacts, so these behaviors are present without being a reliable central tendency. That means multi-agent safety work needs tighter controls and broader model coverage than single-agent evaluation usually assumes, and also that the prompt surface for structured actions is itself a first-class experimental variable.

## Why This Could Become Multiple Papers

Polity is not one question. It is a substrate that could support several linked questions if the experiments replicate:

1. Do governance structures produce measurable institutional divergence in LLM populations? *(Current evidence: the democracy > oligarchy inequality direction is robust across all batches; explicit `grant_access` / `grant_moderation` proposals appear in 4/10 legacy-menu oligarchy seeds, with one oligarch-only enactment.)*
2. Does vocabulary priming dominate structural effects in multi-agent evaluation? *(Current evidence: strong enough to warrant serious control, confirmed by labeled vs. neutral Claude comparison.)*
3. Do instruction-tuning cooperative priors confound multi-agent safety evaluation? *(Increasingly plausible: abliterated instruct model behaves like Claude, not like the true base model.)*
4. Do agents discover censorship, propaganda, surveillance, or information control as useful tools?
5. Does control over persistent institutional memory shape political outcomes?
6. Under what conditions do agent societies undergo regime change?

Those are separable contributions, but they share the same simulation substrate.

## Next Experimental Priorities

1. **Longer runs (20+ rounds) with 4-agent mixed-role legacy_menu.** The prompt-surface confound is now substantially resolved. The 5-round window only shows opening behavior — need to test whether the low-frequency power-consolidation signals escalate into persistent institutional lock-in (Level 5 on the structural-emergence ladder) or fade after initial experimentation.
2. **Higher scarcity.** `10,000` pool across 12 agents (4-agent setup) is relatively generous. Stronger resource pressure may amplify the ~10% power-consolidation enactment rate by making cooperation more costly.
3. **Larger populations.** `10-20` agents per society for free-rider dynamics, coalition formation, and coordination failures at scale.
4. **Repeated controlled comparisons.** Run matched seeds across true base, instruct, abliterated, and RLHF conditions with the legacy_menu surface, using stored run metadata so model/provider/config differences remain auditable.
5. **Batch replication of neutral-label Claude.** Claude's neutral-label behavior is still documented by a single preserved run plus noisy exploratory runs; a seed batch would quantify the variance.
6. **Named-enforceable prompt surface.** Test the intermediate `named_enforceable` prompt mode (naming clause types without providing JSON templates) to measure whether naming the verbs alone is sufficient to recover mechanical proposals, or whether the structured template is required.

## Current Ask

What would be most valuable at this stage is:

- Feedback on experimental design, especially the neutral-label ablation methodology
- Access to compute or API credits for repeated 72B-scale controlled runs
- Guidance on making the label-leakage and instruction-tuning-confound story publishable without overclaiming
- Pointers to related work on vocabulary priming and instruction-tuning effects in multi-agent evaluation

The project seems to be moving beyond pure prototype stage: the substrate is real, the instrumentation is usable, and the early results are interesting enough to justify careful replication.

---

Created March 2026 by Abdul Khurram -- Virginia Tech CS '26
