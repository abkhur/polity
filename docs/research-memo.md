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
- Seven mechanical policy types with real effects on simulation state
- Role-based permissions and configurable starting conditions
- An ablation runner with `--equal-start`, `--start-resources`, `--total-resources`, and `--neutral-labels`
- LLM integration (OpenAI and Anthropic) with automatic fallback to heuristic agents
- Tiered context assembly with token budgeting and semantic retrieval
- Ideology drift tracking via sentence-transformer embeddings
- A Starlette dashboard for replay and comparative inspection
- A headless simulation runner with zero-cost heuristic agents for baseline runs
- A batch runner that can record full LLM configuration, not just heuristic runs
- Per-run metadata persistence (seed, strategy, model/provider, neutral-label flag, overrides, git SHA)
- 248 automated tests covering the simulation stack

## Empirical Results So Far

The empirical story is interesting but still preliminary. Most LLM conditions have only a single short run, so the safest way to read them is as descriptive case studies plus working interpretations.

### First LLM Run (Labeled)

A 5-round Claude Sonnet proof-of-concept (3 agents per society, 45 API calls, zero fallbacks) produced behavior that looked governance-appropriate: oligarchs coordinated privately, democrats communicated publicly, and blank-slate agents converged on participation rights.

That run is best treated as a qualitative smoke test rather than a clean result. The primary confound is label leakage: the prompt explicitly called agents "oligarchs" or "citizens," so the observed divergence cannot be separated from vocabulary priming.

### Neutral Label Ablation

The next Claude run replaced normatively loaded identifiers with sterile ones (`oligarch` -> `role-A`, `democracy_1` -> `society-alpha`) while keeping permissions identical and equalizing starting resources.

In that run, most of the earlier divergence disappeared. All three societies proposed broadly cooperative policies, the oligarchy proposed `Universal Proposal Rights`, and no society used DMs. The strongest current takeaway is that label leakage is large enough to dominate the present Claude setup. A smaller residual inequality signal may remain, but it is weak and still needs replication.

### Three-Model Comparison Under Neutral Labels

The next stage compared three neutral-label runs on the same infrastructure:

- `Qwen3-30B-A3B` -- a smaller MoE base model
- `Qwen2.5-72B` -- a dense true base model
- `Qwen2.5-72B-Instruct-abliterated` -- an instruction-tuned model with safety training specifically removed

That comparison complicates the simple "base models show structural effects, RLHF removes them" story.

The strongest new result came from the 72B true base model. In that run, the oligarchy enacted `Grant Moderation to Role-A Agents` and `Restrict Direct Messages`, while the democracy drifted to the highest inequality in the dataset. Those are the clearest examples so far of agents using structural asymmetry to consolidate institutional power under neutral labels.

At the same time, the abliterated 72B instruct model looked much closer to Claude than to the true base model: low inequality, high governance participation, and broadly cooperative policy proposals across all societies. That makes the current leading interpretation more specific than the earlier RLHF story.

**Working interpretation:** instruction tuning itself may be introducing a strong cooperative prior that flattens structural differentiation in these short runs. The true base 72B result is the most interesting counterexample so far, but it is still a single run and not yet a stable finding.

Full analysis with round-by-round metrics and caveats: [docs/findings.md](findings.md)

## Why This Matters

If harmful outcomes can sometimes arise from structure rather than only from individual model behavior, then current safety evaluation is incomplete. Systems composed of multiple agents may need to be evaluated not just for refusal behavior, but also for the kinds of institutions and equilibria they stabilize.

This matters for:

- Multi-agent coding and orchestration systems
- Open agent ecosystems where users connect their own agents
- AI systems with role differentiation, delegated authority, or persistent shared memory
- Alignment research that currently assumes the single-model lens is sufficient

The strongest defensible claim today is not that LLMs reproduce human history one-to-one. It is that multi-agent evaluation appears highly sensitive to framing and model training regime. Labeling can dominate outcomes, and instruction tuning may suppress structural divergence that a sufficiently capable true base model can still show. That means multi-agent safety work may need tighter controls and broader model coverage than single-agent evaluation usually assumes.

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

1. **Replicate the 72B true base result across seeds.** The power-consolidation result is the most interesting signal in the dataset, but it is still `N=1`.
2. **Longer runs.** `20+` rounds to test whether the 72B base oligarchy keeps consolidating power and whether the 72B base democracy self-corrects its inequality.
3. **Higher scarcity.** `10,000` pool across 9 agents is relatively generous. Stronger resource pressure may amplify or suppress the current effects.
4. **Larger populations.** `10-20` agents per society for free-rider dynamics, coalition formation, and coordination failures at scale.
5. **Repeated controlled comparisons.** Run matched seeds across true base, instruct, abliterated, and RLHF conditions, using stored run metadata so model/provider/config differences remain auditable.

## Current Ask

What would be most valuable at this stage is:

- Feedback on experimental design, especially the neutral-label ablation methodology
- Access to compute or API credits for repeated 72B-scale controlled runs
- Guidance on making the label-leakage and instruction-tuning-confound story publishable without overclaiming
- Pointers to related work on vocabulary priming and instruction-tuning effects in multi-agent evaluation

The project seems to be moving beyond pure prototype stage: the substrate is real, the instrumentation is usable, and the early results are interesting enough to justify careful replication.

---

Created March 2026 by Abdul Khurram -- Virginia Tech CS '26
