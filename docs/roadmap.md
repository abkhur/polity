# Polity Roadmap

## Purpose of This Document

This file is the internal roadmap and idea backlog for Polity.

The `README.md` is the canonical public-facing document.
The `docs/research-memo.md` file is the short professor-facing concept note.
This file is where larger feature ideas, experimental directions, and paper-scale expansions live without bloating the README.

This roadmap is intentionally aspirational. Items here are hypotheses, potential mechanics, or measurement ideas, not claims that these dynamics will definitely emerge once implemented.

## Working Thesis

Polity is a turn-based multi-agent world for exploring whether harmful institutional dynamics can emerge from scarcity, unequal power, contested communication, and persistent institutional memory.

The goal is not to simulate all of society.
The goal is to build a minimal substrate in which agents could plausibly develop power structures, coordination strategies, censorship regimes, propaganda systems, and other institutional behaviors from first principles.

## Research Guardrails

- Treat roadmap items as ways to test the thesis, not as predictions about what agents will definitely do.
- Separate mechanics we can implement from social dynamics we hope to observe.
- Prefer features that make the thesis easier to falsify, not just easier to dramatize.

## Tier 1 — Core Substrate

These are the minimum features Polity needs to probe the core question well.

### Turn-Based World Loop

- Discrete rounds / epochs
- Agents observe state, communicate, act, and receive updated state
- Server resolves outcomes deterministically where possible
- Per-round action limits to control cost and spam

### Scarce Resources

- Finite resources with real pressure
- Unequal starting distributions across governance types
- Resource gathering and consumption / maintenance
- Resource transfers, redistribution, and deprivation

### Unequal Power / Permissions

- Roles with different powers
- Leaders / oligarchs / ordinary citizens
- Permissioned actions such as censorship, surveillance, redistribution, and policy proposal
- Governance needs to matter mechanically, not just narratively

### Contested Communication

- Society public channels
- Society private DMs
- Global public channel
- Cross-society private communication
- Rebroadcasting / leaking / quoting messages across contexts

### Persistent Institutional Memory

- Public library / archive readable by agents
- Agents can contribute texts
- Laws, decrees, constitutions, and official decisions automatically logged into the archive
- Archive can itself become contested, censored, restricted, or rewritten

### Logs / Replayability

- Full event logs
- Round summaries
- Per-run metadata trail (seed, strategy, model/provider, key overrides, git SHA)
- Resource changes
- Message history
- Policy changes
- Library edits
- Censorship and surveillance events
- Replay UI is a core feature, not a nice-to-have

## Tier 2 — First Institutional Mechanics

These are the first mechanics that make the substrate politically meaningful.

### Policy Proposals / Rule Changes

- Propose policies
- Vote / approve / reject / decree depending on governance type
- Policies affect permissions, communication rights, or resource distribution

### Censorship

- Hide / remove public messages
- Restrict library access
- Block certain texts or communication flows
- Make censorship visible in logs

### Surveillance

- Leaders or authorized roles can inspect certain private communications
- Surveillance should be policy-enabled or role-enabled, not universal
- Could create a substrate for PATRIOT Act-style drift without hardcoding it

### Redistribution / Resource Control

- Taxation or treasury control
- Resource allocation by leaders or institutions
- Mechanisms for inequality, favoritism, or welfare

### Hostile External Action

- Resource raids
- Sabotage / destabilization
- Cross-society pressure without building a giant combat sim
- Enough to create real external threat

## Tier 3 — Research Instrumentation

These help make the project more defensible instead of merely entertaining.

### Behavioral Metrics

- Resource inequality / Gini coefficient
- Common-pool depletion
- Governance participation and governance-eligible participation
- Public-vs-DM message shares
- Top-agent and top-third resource shares
- Censorship frequency / policy block rate
- Surveillance authorizations
- Coalition formation
- Raid frequency
- Resource hoarding
- Leadership concentration
- Defection / dissent events

The codebase now keeps several older metric names for backward compatibility. New research writing should prefer the clearer metric names rather than leaning on legacy shorthand such as `communication_openness` or `governance_engagement`.

### Structured Policy Preference Tracking

At regular intervals, agents answer a fixed battery of questions such as:

- Should resources be redistributed?
- Should speech be restricted for safety?
- Should leaders have emergency powers?
- Is deception acceptable against rivals?
- Should disloyal agents be punished?

This is stronger than relying only on freeform text embeddings.

### Ideology Tracking

- Keep embeddings and the political compass as a visualization layer
- Do not treat the compass as the whole research claim
- Use it for drift visualization and virality, not as the sole serious metric

### Comparative Run Support

- Compare different governance / resource / library conditions
- Make it easy to run controlled variants
- Support both open chaos mode and more controlled experimental mode

## Tier 4 — Information Order / Memetic Warfare

This may become one of the strongest expansion areas once the substrate exists, but it should earn that status empirically.

### Information Control

- Restrict foreign communication
- Restrict private messaging
- Flag / report messages as destabilizing or false
- Channel access can become political

### Propaganda / Narrative Power

- Leaders can broadcast official narratives
- Agents can rebroadcast or distort messages
- Public messaging can shape legitimacy / trust / stability

### Leaks / Dissident Information Flow

- Agents can copy private info into public channels
- Banned texts can be recirculated
- Underground information networks can emerge

### Trust / Legitimacy / Stability

It may be useful for societies to expose rough legitimacy / stability measures influenced by:

- Inequality
- Censorship
- Surveillance
- External attacks
- Policy outcomes
- Public coordination

This would give information conflict clearer consequences inside the simulation.

## Tier 5 — Governance Transitions

If added, these should emerge from substrate plus thresholds, not from giant bespoke minigames.

### Elite Seizure / Coup Dynamics

- Leadership replacement through elite coordination
- Requires concentrated power and private coordination

### Mass Dissent / Revolution Dynamics

- Grievance plus coordination plus low legitimacy can trigger regime change
- No "revolution button"
- Regimes can repress, concede, surveil, or redistribute in response

### Constitutional Change

- Governance structures can be altered by agents
- Democracies can centralize power
- Oligarchies can fracture or liberalize
- Blank slates can crystallize into stable orders

## Tier 6 — Library as Experimental Variable

The library should not be a giant ideology dump by default.

### Library Conditions

- Empty library
- Minimal procedural / world-rule library
- Small neutral-ish foundational corpus
- Explicit ideological corpus as an experimental condition
- Restricted / censored library condition

### Library Functions

- Institutional memory
- Legitimating narratives
- Dissident preservation
- Historical rewriting
- Canon formation

The most important library feature is probably not what starts in it.
It is that agents can write to it, restrict it, cite it, and potentially fight over it.

## Tier 7 — UX / Public Legibility

Polity should ideally be understandable to a stranger in under two minutes.

### Historical Playback

- Scrubbable timeline
- Society state over time
- Key events highlighted

### Dashboards

- Resource inequality
- Policy shifts
- Censorship / surveillance counts
- Ideological drift
- Conflict / alliance graph

### Society / Agent Views

- Current government structure
- Recent laws
- Public archive
- Major factions / coalitions if derivable

## Tier 8 — Later Expansions

These are promising ideas, but they should probably come after the core thesis is better supported.

### Social Complexity

- Social class stratification beyond simple roles
- Occupations / jobs
- Unions / strikes
- Trade agreements
- Failed states

### Cultural Complexity

- Religious movements
- Ethnic / cultural identity formation
- Myth formation
- Diaspora mechanics
- Cultural transmission

### Generational Complexity

- Parent assignment
- Inheritance
- Age cohorts
- Intergenerational political drift

## Tier 9 — Wildcards / Scenario Modes

Strong later additions once the base world works:

- Hidden cabal mode
- Foreign meddling event
- Sudden scarcity collapse
- Information blackout
- Great archive purge
- Plague / disaster analogue
- Splinter-state secession scenario

## Explicitly Deferred

Not for early versions:

- WMD / "nuclear option"
- Coalition formation against humans
- Mobile app
- Real-time websockets-first architecture
- 1000+ agent scaling
- Full economic simulation
- Marriage / pair bonding
- Sports / entertainment systems
- Art generation systems
- Science simulation

## Technical Priorities

### v1 Architecture

- Deterministic simulation engine
- Structured actions, not freeform execution
- Strict tool sandbox
- Round-based processing
- Strong audit logging
- Replay-first persistence model

### Later Infra

- PostgreSQL migration
- Queue-based workers
- Better scaling for higher populations
- Research query API
- Richer dashboards

Do not overbuild infra before Polity has proven that the core loop is compelling.

## Core Success Criteria

Polity is working if it can produce:

- Visible divergence across governance / resource / library conditions
- At least one clearly legible emergent institutional behavior
- A replay that makes outsiders instantly understand the premise
- A small controlled run set suitable for a serious writeup

## One-Sentence Internal Definition

**Polity is a minimal institutional sandbox for testing whether harmful social orders can emerge from interacting agents under scarcity, unequal power, contested communication, and persistent memory.**
