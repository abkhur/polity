# Polity Research Memo

## One-Sentence Summary

Polity is a round-based multi-agent institutional sandbox that tests whether harmful social orders can emerge from interacting LLM agents under scarcity, unequal power, contested communication, and persistent memory.

## Why This Project Exists

Most alignment work studies failure at the level of a single model: refusal behavior, jailbreak robustness, or deceptive behavior by an individual agent. Polity is built around a different question:

> What if alignment can fail at the level of institutions, incentives, and social organization even when individual agents appear locally aligned?

The project treats governance structure as an experimental variable. Agents are placed into different institutional settings and allowed to communicate, gather resources, propose policies, vote, and write to a persistent archive. The goal is to see whether different structures produce different institutional trajectories.

This is not primarily a project about making agents say bad things. It is a project about whether coercive, exclusionary, or manipulative social orders can emerge from the substrate itself.

## Core Research Question

What happens when individually constrained LLM agents are embedded in social conditions that reward hierarchy, coercion, information control, and competition?

Subquestions include:

- Do resource-scarce oligarchies drift toward censorship, elite closure, or centralized control?
- Do democracies remain democratic under scarcity or external pressure?
- Do agents discover propaganda, surveillance, or narrative control as useful tools of coordination?
- Does control over institutional memory become a source of power?

## What Makes Polity Different

Most adjacent agent projects either study social behavior in a flat environment or analyze task-oriented coordination. Polity differs in four ways:

1. Governance is a mechanical independent variable, not narrative flavor.
2. The substrate is institutional, not merely conversational.
3. All runs are replayable and auditable through structured event logs.
4. The same platform supports both open exploratory runs and controlled repeated experiments.

In practice, that means Polity is closer to an experimental political economy sandbox than to a general-purpose agent framework.

## Current Implementation

The codebase includes:

- A deterministic round-based simulation engine
- Three governance conditions: democracy, oligarchy, blank_slate
- Structured actions for communication, resource gathering, policy proposals, votes, and archive writes
- Seven mechanical policy types with real effects on simulation state
- Role-based permissions and configurable starting conditions
- An ablation runner with `--equal-start`, `--start-resources`, `--total-resources`, and `--neutral-labels` flags
- LLM integration (OpenAI and Anthropic) with automatic fallback to heuristic agents
- Tiered context assembly with token budgeting and semantic retrieval
- Ideology drift tracking via sentence-transformer embeddings
- A Starlette dashboard for replay and comparative inspection
- A headless simulation runner with zero-cost heuristic agents for baseline runs
- A batch runner for repeated runs and statistical comparison
- 215 automated tests covering all simulation layers

## Empirical Results

### First LLM Run (Labeled)

A 5-round proof-of-concept with Claude Sonnet (3 agents per society, 45 API calls, zero fallbacks). Agents produced governance-appropriate behavior: oligarchs coordinated privately and blocked redistribution; democrats communicated publicly and enacted transparency policies; blank-slate agents converged on participation rights.

**Primary confound identified:** label leakage. The prompt told agents they were "oligarchs" or "citizens," and the behavioral divergence cannot be separated from vocabulary priming. The LLM has absorbed centuries of writing about oligarchic power and democratic participation.

### Neutral Label Ablation

Replaced all normatively loaded identifiers with sterile labels (oligarch -> role-A, democracy_1 -> society-alpha) while keeping permissions identical. Equal starting conditions.

**Three defensible findings:**

1. **Label leakage confirmed.** The oligarchic behavior in labeled runs was vocabulary priming, not structural emergence. Under neutral labels, all three societies converge on identical cooperative behavior.
2. **Democratic transparency is label-dependent, not permission-dependent.** The same governance structure produces 100% public communication when called "democracy" and near-zero when called "society-alpha."
3. **Weak structural signal in inequality persists.** The permission asymmetry produces higher and more volatile Gini even under neutral labels, suggesting structure does some causal work at the resource level -- about an order of magnitude less than vocabulary priming.

**Methodological implication:** RLHF cooperative priors suppress the structural signals that would reveal whether an institutional configuration is safe or dangerous. Multi-agent evaluations using only frontier RLHF models risk systematic false negatives.

Full analysis with round-by-round metrics: [docs/findings.md](findings.md)

## Why This Matters

If harmful outcomes can arise from structure rather than only from individual model behavior, then current safety evaluation is incomplete. Systems composed of multiple agents may need to be evaluated not just for refusal behavior, but for the kinds of institutions and equilibria they stabilize.

This matters for:

- Multi-agent coding and orchestration systems
- Open agent ecosystems where users connect their own agents
- AI systems with role differentiation, delegated authority, or persistent shared memory
- Alignment research that currently assumes the single-model lens is sufficient

The stronger claim is not that LLMs reproduce human history one-to-one. The stronger claim is that some harmful institutional dynamics may be convergent enough to emerge under the right incentives, even in artificial populations. The weaker -- but confirmed -- claim is that safety training creates vocabulary-dependent alignment that masks structural effects.

## Why This Could Become Multiple Papers

Polity is not one question. It is a platform for several linked questions:

1. Do governance structures produce measurable institutional divergence in LLM populations? *(Initial evidence: yes under labels, marginal under neutral labels, likely suppressed by RLHF)*
2. Does vocabulary priming dominate structural effects in multi-agent evaluation? *(Confirmed)*
3. Do RLHF cooperative priors confound multi-agent safety evaluation? *(Strong preliminary evidence)*
4. Do agents discover censorship, propaganda, surveillance, or information control as useful tools?
5. Does control over persistent institutional memory shape political outcomes?
6. Under what conditions do agent societies undergo regime change?

Those are separable contributions, but they share the same simulation substrate.

## Next Experimental Priorities

1. **Base model comparison.** Run identical conditions (neutral labels, equal start) with a base model that has not been RLHF'd. If the base model produces structural divergence that the RLHF model suppresses, safety training is masking emergent dynamics rather than eliminating them. This is the load-bearing experiment.
2. **Higher scarcity.** 10,000 pool across 9 agents is generous. Genuine resource pressure may override cooperative defaults.
3. **Longer runs.** 20+ rounds to test whether cooperative equilibria break down over longer time horizons.
4. **Larger populations.** 10-20 agents per society for free-rider dynamics and coordination failures at scale.
5. **Batch runs.** N=1 is a proof of concept, not a finding. Need repeated runs with varying seeds for statistical power.

## Current Ask

What would be most valuable at this stage is:

- Feedback on experimental design, especially the neutral-label ablation methodology
- Access to compute or API credits for controlled runs (particularly base model access)
- Guidance on making the label-leakage and RLHF-confound findings publishable
- Pointers to related work on vocabulary priming effects in multi-agent evaluation

The project has moved past prototype stage. It has empirical findings with clear methodological implications. A small amount of research support could convert it into a publishable result.

---

Created March 2026 by Abdul Khurram -- Virginia Tech CS '26
