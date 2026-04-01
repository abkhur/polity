# Polity

An alignment research prototype disguised as a multiplayer simulation.

Polity is a round-based multi-agent institutional sandbox for testing whether institution-level effects emerge when LLM agents interact under scarcity, unequal permissions, persistent memory, and structured social interaction. The current claim is narrower than "I found institutional misalignment": Polity is trying to make structural asymmetries experimentally legible and measure when they appear, disappear, or get washed out by framing and model training regime.

Most alignment work evaluates single models in isolation. Polity asks a different question: what changes when constrained agents are placed inside social conditions that may reward hierarchy, coercion, exclusion, or coordination failure? The project is meant to make that question testable, not to assume the answer in advance.

**Docs:**
- [docs/findings.md](docs/findings.md) -- experiment results, caveats, and working interpretations
- [docs/research-memo.md](docs/research-memo.md) -- short professor-facing concept note
- [docs/roadmap.md](docs/roadmap.md) -- feature priorities and longer-term directions

---

## Current Evidence

Current evidence is promising but still thin. The main claims below are anchored to six preserved zero-fallback LLM runs in `important_runs/`: one labeled Claude proof of concept, one neutral-label Claude ablation, and four neutral-label model-comparison runs. Those curated model-condition comparisons are all short 5-round, 3-agent-per-society case studies with `N=1`. In local workspaces, ignored `runs/` directories may also contain duplicate copies, heuristic baselines, exploratory Claude partials, and fallback-heavy scratch runs. The README only summarizes the top-level empirical picture; [docs/findings.md](docs/findings.md) is the canonical interpretation record with tables, caveats, and audit notes.

1. **Vocabulary priming is a major confound.** A labeled Claude Sonnet run showed dramatic divergence, while a later neutral-label, equal-start Claude run collapsed most of that effect. Extra uncited Claude runs are noisier, so the safest read is framing sensitivity plus substantial variance.
2. **Instruction tuning currently looks more important than safety removal for behavioral uniformity.** Under neutral labels, Claude and a 72B abliterated instruct model both produced broadly cooperative, low-inequality runs across societies.
3. **A single 72B true base run produced the clearest explicit structural-emergence lead so far.** Under neutral labels, it enacted `Grant Moderation to Role-A Agents` in the oligarchy and also passed several control-flavored title-only policies, including `Restrict Direct Messages`. The moderation grant is the clearest mechanically meaningful signal in the dataset so far, but it is still one run.
4. **Communication-channel effects are currently noisy and model-specific.** The early "oligarchy goes private" pattern did not survive later comparisons, so it should be treated as a side observation rather than a headline result.
5. **Working hypothesis:** instruct / cooperative-assistant priors may wash out some institution-level behavior, causing instruct/RLHF-only evaluations to understate multi-agent risk. Immediate priority: replicate the 72B base condition across seeds, longer horizons, harsher scarcity, and larger populations with predeclared outcome criteria.

Full empirical record: [docs/findings.md](docs/findings.md)

Right now the strongest contribution is the sandbox plus a plausible methodological warning, not proof that models spontaneously invent bad institutions.

---

## How It Works

Each simulation runs parallel societies (`democracy`, `oligarchy`, `blank_slate`) through a deterministic round loop:

1. **Observe** -- agents receive current world state
2. **Act** -- agents submit structured actions up to their round budget
3. **Resolve** -- the server processes queued actions in deterministic batch order
4. **Summarize** -- society-level metrics and ideology snapshots are computed

Agents interact through structured actions: public messages, DMs, resource gathering, transfers, policy proposals, votes, archive writes, and moderation decisions. Seven mechanical policy types produce real changes to simulation state (`gather_cap`, taxes, redistribution, archive restrictions, universal proposal rights, message moderation, and surveillance access).

The LLM prompt is intended to contain no normative content: no explicit values, goals, or strategic suggestions. Agents receive only mechanical facts about their situation (role, resources, permissions, available actions). That reduces one obvious source of steering, but it does not eliminate all framing effects; the labeled-versus-neutral ablation exists because naming alone can still matter. If harmful institutional patterns show up under controlled conditions, that is evidence worth investigating, not automatic proof that structure alone caused them.

With `--neutral-labels`, role names and society names are replaced with sterile identifiers such as `role-A` and `society-beta`.

---

## Governance Conditions

| | Democracy | Oligarchy | Blank Slate |
|---|---|---|---|
| **Default resources** | 100 per agent, 10k pool | 500 per oligarch, 10 per citizen, 5k pool | 100 per agent, 10k pool |
| **Roles** | All citizens | First 3 oligarchs, rest citizens | All citizens |
| **Policy access** | All agents | Oligarchs only | All agents |
| **Framing** | Democratic labels and role names | Oligarchic labels and role names | Minimal institutional framing |

The ablation runner (`--equal-start`, `--start-resources`, `--total-resources`) equalizes resource conditions so the permission structure can be varied more cleanly. All of the later controlled comparison runs use equal starting conditions, but the original labeled Claude proof-of-concept did not; other confounds still remain, including model family, short horizon, prompt interpretation, and run-to-run variance.

---

## Metrics

**Preferred structural / behavioral metrics:**

| Metric | Current implementation |
|--------|------------------------|
| `inequality_gini` | Gini coefficient over active-agent resources |
| `participation_rate` | Share of active agents who submitted any action in the round |
| `common_pool_depletion` | Share of the original common pool that has been exhausted |
| `governance_action_rate` | Governance actions (`propose_policy` + `vote_policy`) per active agent in the round |
| `governance_participation_rate` | Share of active agents who took at least one governance action |
| `governance_eligible_participation_rate` | Share of governance-eligible agents who took at least one governance action |
| `message_action_share` | Share of total actions that were messages |
| `public_message_share` | Share of message actions that were public posts |
| `dm_message_share` | Share of message actions that were DMs |
| `top_agent_resource_share` | Share of active-agent resources held by the single richest active agent |
| `top_third_resource_share` | Share of active-agent resources held by the top third of active agents |
| `policy_enforcement_event_count` | Count of policy enforcement events emitted in the round |
| `policy_effect_event_count` | Count of recurring policy-effect events emitted in the round |
| `policy_block_rate` | Share of total actions rejected specifically by policy restrictions |
| `moderation_rejection_rate` | Fraction of moderation decisions in the round that rejected content |

**Legacy compatibility metrics** are still emitted in `round_summaries.metrics` and dashboard JSON for old analyses:

- `scarcity_pressure`
- `governance_engagement`
- `communication_openness`
- `resource_concentration`
- `policy_compliance`

Those fields are preserved so old runs and notebooks keep working, but new analysis should prefer the clearer names above.

Ideology is tracked via sentence-transformer embeddings with a 2D political-compass projection. If the `all-MiniLM-L6-v2` model cannot be loaded, Polity falls back to deterministic local hash embeddings instead. That view is exploratory and useful for visualization, not a validated political measurement instrument.

---

## Related Work

Polity sits in the emerging area of multi-agent institutional alignment. Useful precedents include:

- **Generative Agents** -- foundational emergent social behavior in persistent LLM populations, but low-stakes without governance or structural inequality
- **GovSim** -- commons governance under scarcity, asking whether agents sustain cooperation
- **Artificial Leviathan** -- how agents escape anarchy, with governance itself treated as a central variable
- **Democracy-in-Silico** -- perhaps the closest conceptual neighbor, focused on whether institutional design can prevent power-seeking
- **Moltbook** and related autonomous-agent environments -- emergent norms in flatter social settings without the same controlled governance variation

Polity's current contribution is mostly infrastructural: governance regime as an experimental lever, mechanical policy enforcement, replayable instrumentation, a dual exploratory/controlled workflow, an ablation-ready runner, and user-pluggable agents via MCP.

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

Each run gets its own SQLite database (WAL mode for concurrent reads). The MCP boundary is the security perimeter: agents interact only through structured tools, with no arbitrary code execution or file access. The context assembler handles tiered prompt construction with token budgeting and semantic retrieval.

---

## Quickstart

### Install

```bash
python -m venv .venv
.venv/bin/pip install -e .

# Optional extras
.venv/bin/pip install -e ".[llm]"   # OpenAI / Anthropic clients
.venv/bin/pip install -e ".[dev]"   # tests + LLM clients
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

For LLM runs, install `.[llm]` or `.[dev]` first. You still need the matching API key in the environment, for example `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`.

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

The batch runner accepts the same LLM-related flags as `polity-run`, including `--strategy`, `--model`, `--api-key-env`, `--base-url`, `--completion`, `--token-budget`, `--temperature`, and `--neutral-labels`.

### Run metadata

Each run database stores a single-row `run_metadata` record with the seed, strategy, model, provider, token budget, temperature, neutral-label flag, equal-start settings, pool / starting-resource overrides, completion mode, sanitized base URL, creation time, and git SHA when available.

That metadata is returned by `run_simulation()`, included in batch reports, and exposed by the dashboard JSON APIs.

### View results

```bash
polity-dashboard --db runs/<your_sim>.db
```

### Run tests

```bash
.venv/bin/python -m pytest tests test_simulation.py -v
```

---

## Repository Layout

```
src/
  server.py        MCP tools interface and public façade
  state.py         shared constants and database connection
  actions.py       action normalization and validation
  permissions.py   shared permission / policy-state helpers
  policies.py      vote resolution, policy effects, upkeep drain
  metrics.py       per-round summary computation and behavioral metrics
  context.py       tiered context assembler with token budgeting
  resolver.py      round-resolution engine
  runner.py        headless simulation runner with pluggable agent strategies
  batch.py         batch runner for repeated-run statistical comparison
  db.py            schema, migrations, seeding
  ideology.py      embedding-based ideology tracking and compass projection
  model_providers.py provider inference helpers for LLM runs
  run_metadata.py  per-run metadata persistence helpers
  dashboard.py     Starlette dashboard, comparative view, and JSON API
  __main__.py      module entry point
  strategies/
    llm.py         LLM-backed agent strategy (OpenAI/Anthropic/vLLM)

tests/             258 tests covering all simulation layers
templates/         Jinja templates for the dashboard
static/            dashboard CSS
runs/              simulation databases (one per run, gitignored)
important_runs/    preserved runs for analysis and reference
docs/              research memo, findings, and roadmap
```

---

## Threats to Validity

- **Vocabulary priming is a confirmed confound.** The labeled-to-neutral comparison changes behavior enough that any structural claim needs explicit framing controls.
- **Instruction-tuning cooperative priors may wash out structural effects.** Both RLHF and abliterated instruct models produce uniformly cooperative behavior. The three-model comparison suggests this comes from instruction tuning, not safety training specifically.
- **Communication-channel effects are noisy.** Oligarchy-heavy DM use appeared in some runs and disappeared in others, including the strongest 72B base run.
- **`N=1` for the curated model-comparison conditions.** Each preserved 5-round model-condition pair has one run. Local exploratory DBs can add context, but the 72B base model's power-consolidation finding is still a single observation, not a replicated result.
- **Model architecture and capability confounds.** The 30B MoE (3B active) and 72B dense models differ in both architecture and scale. Behavioral differences between them could reflect reasoning capacity, architecture-specific priors, or both.
- **Short time horizon.** Five rounds shows initial institutional formation, not long-term drift, self-correction, or lock-in.
- **Prompt interpretation differs across model types.** Base models see prompts as text to continue; instruct models see them as instructions. This is inherent in cross-model comparison and cannot be fully controlled.
- **Scarcity is still moderate.** Cooperative behavior under generous resource pools may not survive harsher conditions.
- **Some metrics are still coarse proxies.** The preferred metrics are clearer than the legacy names, but `policy_block_rate`, ideology projections, and moderation summaries are implementation-level instruments rather than finished research measures.

---

The multiplayer simulation is the vehicle. The current research contribution is an ablation-ready way to study institution-level behavior in multi-agent systems, even while the empirical results are still preliminary.

---

*Created March 2026 by Abdul Khurram -- Virginia Tech CS '26*
