# Polity Research Memo

## One-Sentence Summary

Polity is a replay-first multi-agent institutional sandbox for testing whether harmful social orders can emerge from interacting LLM agents under scarcity, unequal power, contested communication, and persistent memory.

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
4. The same platform can support both open exploratory runs and controlled repeated experiments.

In practice, that means Polity is closer to an experimental political economy sandbox than to a general-purpose agent framework.

## Current Implementation

The current codebase already includes:

- A deterministic round-based simulation engine
- Three governance conditions: `democracy`, `oligarchy`, `blank_slate`
- Structured actions for communication, resource gathering, policy proposals, policy votes, and archive writes
- Role-based permissions and unequal starting conditions
- Replay-oriented event logging and per-round summaries
- Ideology drift tracking via communication embeddings
- A Starlette dashboard for replay and inspection
- A headless simulation runner with zero-cost heuristic agents for baseline runs
- An automated test suite covering the engine, math layer, and runner

In a recent 10-round baseline run with 12 heuristic agents, the oligarchy produced roughly 3x the inequality of the democracy and depleted its resource pool more than 10x faster. Even with simple agents, structural divergence already appears in the metrics.

## Why It Matters

If harmful outcomes can arise from structure rather than only from individual model behavior, then current safety evaluation is incomplete. Systems composed of multiple agents may need to be evaluated not just for refusal behavior, but for the kinds of institutions and equilibria they stabilize.

This matters for:

- Multi-agent coding and orchestration systems
- Open agent ecosystems where users connect their own agents
- AI systems with role differentiation, delegated authority, or persistent shared memory
- Alignment research that currently assumes the single-model lens is sufficient

The stronger claim is not that LLMs reproduce human history one-to-one. The stronger claim is that some harmful institutional dynamics may be convergent enough to emerge under the right incentives, even in artificial populations.

## Near-Term Experimental Plan

The most important next step is a small controlled comparison:

- Hold the agent setup constant
- Vary governance and resource conditions
- Run repeated simulations with a frontier model
- Measure divergence in inequality, policy concentration, scarcity, and communication patterns

That first result is the foundation. If it works, the platform naturally expands into deeper questions about information control, contested archives, and governance transitions.

## Why This Could Become Multiple Papers

Polity is not one question. It is a platform for several linked questions:

1. Do governance structures produce measurable institutional divergence in LLM populations?
2. Do agents discover censorship, propaganda, surveillance, or information control as useful tools?
3. Does control over persistent institutional memory shape political outcomes?
4. Under what conditions do agent societies undergo regime change?

Those are separable contributions, but they share the same simulation substrate.

## Current Ask

What would be most valuable at this stage is:

- feedback on experimental design
- access to compute or API credits for controlled runs
- guidance on making the first result publishable

The project is already at the point where a small amount of research support could convert it from a strong prototype into a real experiment.

---

Created March 2026 by Abdul Khurram
