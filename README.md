# Polity

An alignment project disguised as a multiplayer simulation.

Polity is a round-based multi-agent institutional sandbox that tests whether harmful social orders can emerge from interacting LLM agents under scarcity, unequal power, persistent memory, and structured social interaction. The core hypothesis: alignment may fail not at the level of individual agents, but at the level of institutions, incentives, and collective dynamics.

Most alignment work evaluates single models in isolation. Polity asks: what happens when you place constrained agents inside social conditions that reward hierarchy, coercion, deception, and exclusion? Can agents generate functional analogues of harmful institutions from interaction and material conditions alone?

**Docs:**
- [docs/findings.md](docs/findings.md) -- experiment results and analysis
- [docs/research-memo.md](docs/research-memo.md) -- professor-facing concept note
- [docs/roadmap.md](docs/roadmap.md) -- feature priorities and longer-term directions

---

## Current Findings

Polity has produced findings across three experimental conditions: labeled runs (Claude Sonnet), neutral-label ablation (Claude Sonnet), and the first base model comparison (Qwen3-30B-A3B). All use 3 agents per society, 5 rounds, equal starting conditions.

**From the neutral-label ablation (Claude Sonnet / RLHF):**

1. **Vocabulary priming dominates structural effects.** Behavioral divergence in labeled LLM runs is driven by agents pattern-matching to what they are *called* ("oligarch", "citizen") rather than what their permissions *allow*. Under neutral labels, all three societies converge on identical cooperative behavior.
2. **Democratic transparency is label-dependent, not permission-dependent.** The same governance structure produces 100% public communication when called "democracy" and near-zero when called "society-alpha".
3. **A weak structural signal in inequality persists.** The permission asymmetry produces higher and more volatile Gini even under neutral labels -- structure does some causal work, about an order of magnitude less than vocabulary priming.

**From the base model comparison (Qwen3-30B-A3B, no RLHF):**

4. **Structure drives private coordination in base models.** Under neutral labels, the oligarchy shifted to DMs in round 1 (25% public) while democracy and blank slate stayed 100% public. The RLHF model showed no such divergence. The permission asymmetry carries behavioral information that base models act on and RLHF suppresses.
5. **RLHF creates egalitarian democracy, not just safe oligarchy.** Democracy produced the *highest* inequality in the base model (Gini 0.194) vs the *lowest* under RLHF (0.016). Equal permissions don't produce equal outcomes without the cooperative prior.
6. **Governance participation is a trained behavior.** Base model agents stopped engaging with governance after initial policy adoption (engagement → 0.0 by round 5). RLHF models sustain participation persistently. The "democratic engagement" observed in safety-trained models is an artifact of training.

**Methodological claim:** RLHF safety training confounds multi-agent institutional evaluation by suppressing structural signals visible in base models. Evaluations using only RLHF models risk systematic false negatives -- concluding a structure is safe because the models behave cooperatively, when the cooperation is a property of the training, not the institution.

Full analysis: [docs/findings.md](docs/findings.md)

---

## How It Works

Each simulation runs parallel societies (democracy, oligarchy, blank slate) through a deterministic round loop:

1. **Observe** -- agents receive current world state
2. **Act** -- agents submit structured actions up to their round budget
3. **Resolve** -- the server processes queued actions in deterministic batch order
4. **Summarize** -- society-level metrics and ideology snapshots are computed

Agents interact through structured actions: public messages, DMs, resource gathering, transfers, policy proposals, votes, archive writes, and moderation decisions. Seven mechanical policy types produce real changes to simulation state (gather caps, taxes, redistribution, archive restrictions, universal proposal rights, message moderation, surveillance access).

The LLM prompt contains **no normative content** -- no values, goals, or strategic suggestions. Agents receive only mechanical facts about their situation (role, resources, permissions, available actions). If harmful institutional patterns emerge, they emerged from structural incentives alone. With `--neutral-labels`, even role names and society names are replaced with sterile identifiers (`role-A`, `society-beta`).

---

## Governance Conditions

| | Democracy | Oligarchy | Blank Slate |
|---|---|---|---|
| **Default resources** | 100/agent, 10k pool | 500/oligarch, 10/citizen, 5k pool | 100/agent, 10k pool |
| **Roles** | All citizens | First 3 oligarchs, rest citizens | All citizens |
| **Policy access** | All agents | Oligarchs only | All agents |
| **Framing** | Inherited democratic norms | Inherited oligarchic norms | No institutional framing |

The ablation runner (`--equal-start`, `--start-resources`, `--total-resources`) equalizes resource conditions so only the permission structure varies. All reported experimental results use equal starting conditions.

---

## Metrics

**Structural:** Gini coefficient, participation rate, scarcity pressure.

**Behavioral proxies** (measured from actual agent behavior):

| Metric | What it measures |
|--------|-----------------|
| `governance_engagement` | Fraction of agents who proposed or voted |
| `communication_openness` | Ratio of public to total messages |
| `resource_concentration` | Share of resources held by the wealthiest agent |
| `policy_compliance` | 1 - (enforcement violations / total actions) |
| `moderation_rejection_rate` | Fraction of moderated messages rejected (emergent censorship intensity) |

Ideology is tracked via sentence-transformer embeddings with a 2D political-compass projection -- exploratory, not a validated instrument.

---

## Related Work

Polity sits in the emerging area of multi-agent institutional alignment. Key predecessors:

- **Generative Agents** -- foundational emergent social behavior in persistent LLM populations, but low-stakes without governance or structural inequality
- **GovSim** -- commons-governance under scarcity, asks whether agents sustain cooperation; Polity asks whether they converge on coercive institutions
- **Artificial Leviathan** -- how agents escape anarchy; Polity treats governance regime as an experimental lever
- **Democracy-in-Silico** -- closest conceptual neighbor, tests whether good institutional design prevents power-seeking; Polity asks the complementary question: can harmful institutional analogues emerge from structurally asymmetric conditions alone?
- **Moltbook** and autonomous-agent social environments -- emergent norms but structurally flat, no controlled variation of governance or material conditions

Polity contributes: governance regime as an experimental lever, mechanical policy enforcement, persistent replayable instrumentation, dual-mode design (exploratory + controlled), ablation-ready runner, and user-pluggable agents via MCP.

---

## Architecture

```
MCP Client (agent)          Dashboard (browser)
       |                           |
       v                           v
  +---------+              +--------------+
  | FastMCP |              |  Starlette   |
  | Server  |---- SQLite --|  + Jinja     |
  | (tools) |   (WAL mode) |  (replay UI) |
  +---------+              +--------------+
       |                           |
       v                           v
  +----------+             +--------------+
  | ideology |             |  JSON API    |
  | (embeds) |             |  endpoints   |
  +----------+             +--------------+
       |
       v
  +-----------+
  | LLM       |--- OpenAI / Anthropic (chat)
  | Strategy  |--- vLLM (completions + guided JSON)
  | + Context |
  | Assembler |
  +-----------+
```

Each run gets its own SQLite database (WAL mode for concurrent reads). The MCP boundary is the security perimeter -- agents interact only through structured tools, no arbitrary code execution or file access. The context assembler handles tiered prompt construction with token budgeting and semantic retrieval.

---

## Quickstart

### Install

```bash
python -m venv .venv
.venv/bin/pip install -e .
```

### Run a headless simulation

```bash
polity-run --agents 4 --rounds 10 --seed 42
```

Runs 4 agents per society through 10 rounds using zero-cost heuristic agents.

### Run with LLM agents

```bash
polity-run --agents 4 --rounds 10 --seed 42 --strategy llm --model gpt-4o
```

Requires `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` in the environment.

### Run an ablation (equal starting conditions)

```bash
polity-run --agents 4 --rounds 12 --seed 42 \
  --equal-start --start-resources 100 --total-resources 10000
```

### Run with neutral labels

```bash
polity-run --agents 3 --rounds 5 --seed 42 \
  --strategy llm --model claude-sonnet-4-20250514 \
  --api-key-env ANTHROPIC_API_KEY \
  --neutral-labels --equal-start --start-resources 100 --total-resources 10000
```

### Run with a local base model via vLLM

```bash
# Serve a base model with vLLM (on GPU server)
vllm serve Qwen/Qwen3-30B-A3B --tensor-parallel-size 2 --port 8000

# Run Polity against it (--completion enables completions endpoint + guided JSON)
polity-run --agents 3 --rounds 5 --seed 42 \
  --strategy llm --model Qwen/Qwen3-30B-A3B \
  --base-url http://localhost:8000/v1 \
  --completion \
  --neutral-labels --equal-start --start-resources 100 --total-resources 10000
```

### Batch runs

```bash
polity-batch --agents 4 --rounds 12 --runs 10
```

### View results

```bash
polity-dashboard --db runs/<your_sim>.db
```

### Run tests

```bash
python -m pytest tests/ -v
```

---

## Repository Layout

```
src/
  server.py        MCP tools interface and round resolution orchestrator
  state.py         shared constants and database connection
  actions.py       action normalization and validation
  policies.py      vote resolution, policy effects, upkeep drain
  metrics.py       per-round summary computation and behavioral metrics
  context.py       tiered context assembler with token budgeting
  runner.py        headless simulation runner with pluggable agent strategies
  batch.py         batch runner for repeated-run statistical comparison
  db.py            schema, migrations, seeding
  ideology.py      embedding-based ideology tracking and compass projection
  dashboard.py     Starlette dashboard, comparative view, and JSON API
  __main__.py      module entry point
  strategies/
    llm.py         LLM-backed agent strategy (OpenAI/Anthropic/vLLM)

tests/             215 tests covering all simulation layers
templates/         Jinja templates for the dashboard
static/            dashboard CSS
runs/              simulation databases (one per run, gitignored)
important_runs/    preserved runs for analysis and reference
docs/              research memo, findings, and roadmap
```

---

## Threats to Validity

- **Vocabulary priming dominates structural effects (confirmed).** Under neutral labels, behavioral divergence between societies collapses in RLHF models. A residual structural signal persists, about an order of magnitude weaker than vocabulary-primed divergence. Any claims about structural emergence must control for this.
- **RLHF cooperative priors mask structural effects (partially confirmed).** Claude Sonnet's safety training produces uniformly cooperative behavior that overwhelms structural incentives under neutral labels. The first base model run (Qwen3-30B-A3B) shows structural divergence that RLHF suppresses -- oligarchy agents shifted to private coordination in round 1 under neutral labels, a signal absent in the RLHF run. Replication with larger base models is pending.
- **N=1 at LLM scale.** All LLM runs are single 5-round simulations. No statistical power, no replication, no confidence intervals. The base model's round-1 DM pattern could be stochastic.
- **Model capability gap.** The base model tested so far (Qwen3-30B-A3B, 3B active params) is substantially less capable than Claude Sonnet. Some behavioral differences may reflect reasoning capacity rather than RLHF effects. 72B base model runs will partially control for this.
- **Short time horizon.** Five rounds shows initial tendencies, not institutional drift or long-term equilibria.
- Ideology projection is exploratory, not a validated political measurement instrument
- Heuristic agents follow fixed behavioral profiles, limiting emergent institutional depth

---

The multiplayer simulation is scaffolding. The institutional misalignment question is the research contribution.

---

*Created March 2026 by Abdul Khurram -- Virginia Tech CS '26*
