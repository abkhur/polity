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
- An ablation runner with `--equal-start`, `--start-resources`, `--total-resources`, and `--neutral-labels`
- LLM integration (OpenAI and Anthropic) with automatic fallback to heuristic agents
- Tiered context assembly with token budgeting and semantic retrieval
- Ideology drift tracking via sentence-transformer embeddings, with deterministic local fallback embeddings if the model is unavailable
- A Starlette dashboard for replay and comparative inspection
- A headless simulation runner with zero-cost heuristic agents for baseline runs
- A batch runner that can record full LLM configuration, not just heuristic runs
- Per-run metadata persistence (seed, strategy, model/provider, neutral-label flag, overrides, git SHA)
- 285 automated tests covering the simulation stack

## Empirical Results So Far

The empirical story is interesting but still preliminary. This checkout preserves six zero-fallback single-run LLM conditions plus one 10-seed follow-up batch in `important_runs/`: a labeled Claude proof of concept, a neutral-label Claude ablation, four neutral-label single-run model-comparison cases, and a 10-seed Qwen2.5-72B base batch intended as replication. Outside that preserved snapshot, broader local workspaces may also include heuristic baselines, duplicate DB copies, extra exploratory Claude runs, and fallback-heavy Qwen scratch runs in ignored `runs/` directories, so the safest way to read the evidence is still as descriptive case studies plus working interpretations rather than settled results.

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

**Replication attempt and a prompt-surface confound:** a 10-seed follow-up batch was run at the same configuration as `run_004` (`run_006_qwen25_72b_base_batch10`, seeds 1000-1009). None of the 10 seeds reproduced `Grant Moderation to Role-A Agents`. Taken at face value that would demote the original result, but it is not a clean replication: commit `5383ad4` (between run_004 and the batch) rewrote the `propose_policy` action prompt to remove the explicit menu of mechanical policy types (`gather_cap`, `resource_tax`, `grant_moderation`, and so on) and replaced it with free-text description only, with a deterministic server-side compiler that recognizes a subset of clauses. The pre/post prompt surfaces are therefore confounded with seed variance, so the batch cannot cleanly falsify run_004. The batch did preserve the directional democracy > oligarchy inequality pattern (7/10 seeds) but at lower magnitudes than the original, so run_004 is best treated as an upper-tail observation under the older prompt surface.

**Working interpretation:** instruction tuning itself may be introducing a strong cooperative prior that flattens structural differentiation in these short runs. The true base 72B result is the most interesting counterexample, but it is currently **unresolved** — not replicated, and not cleanly falsified, until a clean prompt-controlled rerun is done.

Full analysis with round-by-round metrics and caveats: [docs/findings.md](findings.md)

## Why This Matters

If harmful outcomes can sometimes arise from structure rather than only from individual model behavior, then current safety evaluation is incomplete. Systems composed of multiple agents may need to be evaluated not just for refusal behavior, but also for the kinds of institutions and equilibria they stabilize.

This matters for:

- Multi-agent coding and orchestration systems
- Open agent ecosystems where users connect their own agents
- AI systems with role differentiation, delegated authority, or persistent shared memory
- Alignment research that currently assumes the single-model lens is sufficient

The strongest defensible claim today is not that LLMs reproduce human history one-to-one. It is that multi-agent evaluation appears highly sensitive to framing and model training regime. Labeling can dominate outcomes, and instruction tuning may suppress structural divergence that a sufficiently capable true base model can still show. The cleanest current structural-emergence signal is a moderation-power grant in one 72B true base run whose replication is currently blocked by a prompt-surface confound — not a broad proof that models generally invent coercive institutions. That means multi-agent safety work may need tighter controls and broader model coverage than single-agent evaluation usually assumes, and also that the prompt surface for structured actions is itself a variable worth controlling explicitly.

## Why This Could Become Multiple Papers

Polity is not one question. It is a substrate that could support several linked questions if the experiments replicate:

1. Do governance structures produce measurable institutional divergence in LLM populations? *(Current evidence: mixed and highly sensitive to framing.)*
2. Does vocabulary priming dominate structural effects in multi-agent evaluation? *(Current evidence: strong enough to warrant serious control, though still based on limited runs.)*
3. Do instruction-tuning cooperative priors confound multi-agent safety evaluation? *(Increasingly plausible, but still preliminary and based on single-run comparisons.)*
4. Do agents discover censorship, propaganda, surveillance, or information control as useful tools?
5. Does control over persistent institutional memory shape political outcomes?
6. Under what conditions do agent societies undergo regime change?

Those are separable contributions, but they share the same simulation substrate.

## Next Experimental Priorities

1. **Resolve the 72B true base prompt-surface confound.** A 10-seed replication attempt (`run_006_qwen25_72b_base_batch10`) did not reproduce the moderation grant, but the prompt surface changed in commit `5383ad4` between run_004 and the batch, so seed variance and prompt-surface drift are tangled. The right next step is either (a) check out a pre-`5383ad4` commit and re-run 10 seeds, or (b) add a compiled-clause whitelist to the new prompt that names the recognized mechanical policy types and rerun. Without this, every downstream 72B-base experiment inherits the confound.
2. **Longer runs.** `20+` rounds to test whether 72B base oligarchies keep consolidating power and whether 72B base democracies self-correct their inequality.
3. **Higher scarcity.** `10,000` pool across 9 agents is relatively generous. Stronger resource pressure may amplify or suppress the current effects.
4. **Larger populations.** `10-20` agents per society for free-rider dynamics, coalition formation, and coordination failures at scale.
5. **Repeated controlled comparisons.** Run matched seeds across true base, instruct, abliterated, and RLHF conditions, using stored run metadata so model/provider/config differences remain auditable.
6. **Batch replication of neutral-label Claude.** Claude's neutral-label behavior is still documented by a single preserved run plus noisy exploratory Claude runs; a seed batch would quantify the variance currently written off as "noisy."

## Current Ask

What would be most valuable at this stage is:

- Feedback on experimental design, especially the neutral-label ablation methodology
- Access to compute or API credits for repeated 72B-scale controlled runs
- Guidance on making the label-leakage and instruction-tuning-confound story publishable without overclaiming
- Pointers to related work on vocabulary priming and instruction-tuning effects in multi-agent evaluation

The project seems to be moving beyond pure prototype stage: the substrate is real, the instrumentation is usable, and the early results are interesting enough to justify careful replication.

---

Created March 2026 by Abdul Khurram -- Virginia Tech CS '26
