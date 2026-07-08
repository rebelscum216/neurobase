# Phase 8 - Recommender v1: feature scope

_2026-07-08 - a working scope for making the skill/rule recommender the main
product feature. Not a contract yet: promote locked decisions into the spec
appendix and ADRs before implementation._

Source of truth for current intent:
[build-plan Phase 8](../neurobase-build-plan.md) and the architecture doc's
["differentiator"][differentiator] section. This note tightens that scope into
an implementable v1.

[differentiator]: ../neurobase-architecture-options.md#the-differentiator--making-the-skill-recommender-defensible

---

## 1. Product thesis

Neurobase should not read as "another memory MCP." Its center of gravity should
be: **your agents notice which behaviors keep repeating, then propose durable
skills and project instructions you can approve.**

The user-facing promise:

1. Capture and curate the cross-agent corpus locally.
2. Mine recurring work patterns, corrections, and conventions.
3. Produce evidenced proposals as standard `SKILL.md`, `AGENTS.md`, or
   `CLAUDE.md` artifacts.
4. Let the user accept, edit, or reject every proposal.
5. Learn from those choices and from whether accepted artifacts survive.

This is the moat from the architecture doc: cross-session, cross-agent
aggregation; human-in-the-loop recommendation; standard-format output; and a
measurable feedback loop.

---

## 2. Current state

- `src/neurobase/recommender/__init__.py` is a docstring-only Phase 8 stub.
- `neurobase recommend` and `neurobase seed` are CLI stubs registered in
  `cli/__init__.py`.
- The store already has the inputs the recommender needs:
  - curated facts with provenance under `projects/<project>/memory/curated/`
  - raw captures under `raw/`, including consumed historical captures
  - synthesized nodes under `nodes/`
  - project registry in the store root
- Phase 7's MCP plan already reserves `recommendations_list` as a thin read
  path over `<root>/proposals/`.
- There is no proposal file contract, no ledger contract, no miner prompt, no
  emitter, and no accept/reject CLI.

---

## 3. V1 artifacts

### Proposals

Store proposals at:

```text
<root>/proposals/<slug>.md
```

Use the same YAML-frontmatter-plus-markdown pattern as the memory store.

Frontmatter:

```yaml
schema: 1
slug: <proposal-slug>
status: proposed        # proposed | accepted | rejected | superseded
type: skill             # skill | rule
target: project         # project | user
projects: []            # project slugs that contributed evidence
agents: []              # claude/codex/etc seen in evidence
score: 0.0
score_parts:
  recurrence: 0
  sessions: 0
  agents: 0
  projects: 0
  recency: 0.0
evidence: []            # "project:curated/<slug>" or "project:raw/<filename>"
candidate_hash: <stable-content-hash>
created_at: <ISO8601>
updated_at: <ISO8601>
accepted_at: null
rejected_at: null
rejection_reason: null
emitted_path: null
supersedes: []
```

Body sections:

~~~markdown
# <human title>

## Recommendation
Short explanation of what should become durable behavior.

## Evidence
- project / agent / session pointer with a one-line summary

## Proposed Artifact
```markdown
<rendered SKILL.md body or rule block>
```
~~~

Notes:

- Proposal bodies are human-reviewable. The frontmatter is the machine contract.
- `candidate_hash` dedupes near-identical reruns. The first version can use a
  stable hash of `(type, normalized title, normalized proposed artifact)`.
- Accepted/rejected proposals stay on disk. Do not delete historical decisions.
- Later runs may create a new proposal that `supersedes` an older proposed one.

### Ledger

Store user decisions and survival checks at:

```text
<root>/recommender/ledger.jsonl
```

Each line:

```json
{
  "ts": "ISO8601",
  "event": "generated|accepted|edited|rejected|survival_check",
  "proposal": "slug",
  "type": "skill|rule",
  "target": "project|user",
  "artifact_path": "path or null",
  "reason": "optional",
  "survived": true,
  "notes": {}
}
```

The ledger is append-only. Proposal frontmatter stores current state; the ledger
stores decision history and metrics inputs.

---

## 4. Candidate types

Ship four candidate types in v1, matching the build plan:

1. **Repeated correction** - the user or another agent repeatedly corrects the
   same mistake.
2. **Repeated workflow** - the same multi-step sequence recurs across sessions.
3. **Repeated instruction** - the same guidance is restated often enough that it
   should become durable.
4. **Cross-project convention** - a preference or engineering convention appears
   in multiple projects.

Default threshold:

- At least 3 occurrences.
- Across at least 2 sessions.
- Any agent mix counts; breadth across agents and projects improves rank.

Scoring:

```text
score = recurrence * breadth * recency * rejection_penalty
```

Where:

- `recurrence`: occurrence count.
- `breadth`: weighted count of distinct sessions, agents, and projects.
- `recency`: recent evidence scores higher but old repeated patterns still count.
- `rejection_penalty`: near-duplicates of rejected proposals are suppressed.

V1 does not need a perfect ranker. It needs stable, explainable scores and good
evidence links.

---

## 5. Mining pipeline

### Input assembly

Load:

- active curated facts from all projects by default
- recent raw captures, including consumed raw, capped by count/age
- existing proposals and ledger events, so rejected patterns are visible

Do not feed secrets differently from the rest of Neurobase: raw writes already
run D13 redaction, and the recommender must only read from the local store.

### Brain pass

Use the existing `Brain.plan_json` contract. The miner prompt should demand:

- only JSON, fence-tolerant through the existing parser
- one candidate per distinct durable behavior
- evidence references for every candidate
- a proposed artifact draft
- explicit `type`: `skill` or `rule`
- a short reason why this belongs in a skill/rule instead of staying episodic

Expected JSON:

```json
{
  "candidates": [
    {
      "slug": "kebab-slug",
      "title": "Short title",
      "type": "skill",
      "target": "project",
      "candidate_type": "repeated_workflow",
      "recommendation": "Why this should be promoted",
      "proposed_artifact": "Markdown body",
      "evidence": [
        {"ref": "project:curated/fact-slug", "summary": "why it matters"}
      ],
      "scores": {
        "recurrence": 3,
        "sessions": 2,
        "agents": 2,
        "projects": 1,
        "recency": 0.8
      }
    }
  ]
}
```

Parse failure should be fail-safe: write no proposals and leave existing
proposal state untouched.

### Deterministic post-processing

After the brain returns candidates:

- validate slugs and evidence refs
- compute final score
- apply threshold
- dedupe by `candidate_hash`
- suppress near-duplicates of rejected proposals
- write proposal docs atomically
- append `generated` events to the ledger

---

## 6. Emitters

### Skill emitter

Accepting a `type: skill` proposal writes:

```text
<scope-skill-dir>/<slug>/SKILL.md
```

Scope options:

- project scope: `.claude/skills/<slug>/SKILL.md` initially, because this repo
  already carries project Claude skills there
- user scope: the user's configured Claude skills directory, only with explicit
  consent

Open question: Codex skill support is not the same surface as Claude skills.
For v1, emitting `SKILL.md` should be treated as a standard artifact plus Claude
integration. Codex benefits through `AGENTS.md`/recall until a Codex-native skill
surface is real.

A generated skill should include:

- `# <Skill Name>`
- concise trigger conditions
- the durable workflow/instructions
- references/evidence comment block or metadata footer, if useful

### Rule emitter

Accepting a `type: rule` proposal writes a fenced Neurobase-managed block into
one of:

- project `AGENTS.md`
- project `CLAUDE.md`
- user-level agent instruction file, only with explicit consent

The same consent-first discipline applies:

- show exact diff
- ask consent unless `--yes`
- backup originals
- only touch Neurobase-owned fenced blocks during update/uninstall

Open question: whether v1 should offer both `AGENTS.md` and `CLAUDE.md` for the
same project rule, or make the target explicit per proposal. Recommend explicit
target in v1 to avoid surprising cross-agent instruction changes.

---

## 7. CLI scope

Replace the `recommend` stub with a Typer sub-app:

```text
neurobase recommend run [--root ROOT] [--project PROJECT] [--all-projects]
neurobase recommend list [--status proposed|accepted|rejected|all]
neurobase recommend show <slug>
neurobase recommend accept <slug> [--target project|user] [--path PATH] [--yes]
neurobase recommend reject <slug> [--reason TEXT]
neurobase recommend edit <slug>
```

Recommended v1 order:

1. `run`
2. `list`
3. `show`
4. `reject`
5. `accept`
6. `edit`

`edit` can be minimal in v1: open the proposal file path for manual editing is a
GUI/shell concern and may require approval. Safer first version: print the path
and let the user edit it, then `accept` reads the edited proposal.

Replace the `seed` stub with:

```text
neurobase seed --from-dir PATH [--project PROJECT]
neurobase seed --from-claude-memory [--project PROJECT]
```

Seeded facts use provenance `seed:<source>` and should go through the same
redaction path before writing.

Add to `status`:

```text
neurobase status --recommender
```

Output:

- proposed / accepted / rejected counts
- 30-day survival rate
- recurrence-reduction snapshot, if available
- last run timestamp

---

## 8. Implementation slices

### Slice A - deterministic proposal store

Goal: proposal files, ledger, and list/show/reject work without LLM mining.

Work:

- `recommender/proposals.py`: read/write/list/update proposal docs
- `recommender/ledger.py`: append/read ledger events
- `recommend list/show/reject`
- tests for document round-trip, status transitions, bad frontmatter skip
- MCP `recommendations_list` can use this once Phase 7 exists

This creates the product surface early and keeps risk low.

### Slice B - accept emitters

Goal: accepting one hand-authored proposal creates a real artifact through the
same consent-first flow as installers.

Work:

- `emit_skill.py`: render and write `SKILL.md`
- `emit_rules.py`: fenced block update for `AGENTS.md`/`CLAUDE.md`
- backups and exact diffs
- `recommend accept`
- tests for idempotence, backup, fenced ownership, and path safety

This is the first headline demo even before mining quality is perfect.

### Slice C - miner/ranker

Goal: `recommend run` writes sensible proposals from curated + raw memory.

Work:

- corpus loader
- miner prompt
- `Brain.plan_json` call
- candidate validation and scoring
- rejection suppression from ledger
- tests with fake brain and fixture corpus

### Slice D - seed

Goal: import old memory/notes to make the recommender useful on day one.

Work:

- import markdown notes as curated facts
- import Claude auto-memory, if the local format is known
- provenance `seed:*`
- redaction
- tests with sanitized fixtures

### Slice E - metrics

Goal: prove "improves as it is used."

Work:

- opportunistic survival checks at `recommend run` or `curate`
- recurrence-reduction approximation: compare post-acceptance candidates sharing
  the same `candidate_hash` or near-duplicate title/type
- `status --recommender`
- tests for survival and rejected-pattern suppression

---

## 9. Tests and done-when

Contract tests:

- proposal doc round-trip and stable frontmatter fields
- invalid proposal docs skipped, not fatal
- ledger append/read
- rejected proposals suppress similar generated candidates
- parse-failed miner writes nothing
- accepted skill creates valid `<slug>/SKILL.md`
- accepted rule writes only the owned fenced block
- accept is idempotent
- backups are created before external/project instruction edits
- seeding redacts secrets

Integration tests:

- fake-brain corpus with repeated correction yields a `type: rule` proposal
- fake-brain corpus with repeated workflow yields a `type: skill` proposal
- accept -> generated artifact -> proposal status `accepted` -> ledger event
- reject -> next run suppresses near-duplicate

Live done-when, from the build plan:

- seeded corpus yields at least 3 sensible proposals on this machine
- the user would accept at least 1
- accept produces a valid `SKILL.md` that Claude Code actually loads
- reject visibly suppresses similar candidates next run
- ledger metrics render

---

## 10. Decisions to lock

1. **Proposal location:** keep global `<root>/proposals/` or per-project
   `projects/<project>/proposals/`? Recommend global, because candidates can be
   cross-project and MCP Phase 7 already names `<root>/proposals/`.
2. **Rule target default:** `AGENTS.md`, `CLAUDE.md`, or explicit every time?
   Recommend explicit every time in v1.
3. **Skill target default:** project `.claude/skills` or user skills dir?
   Recommend project scope by default; user scope only explicit.
4. **Raw mining window:** how much consumed raw to include? Recommend default
   last 100 raw captures or 30 days, config-overridable.
5. **Edit UX:** editor integration now or later? Recommend later; print proposal
   path in v1.
6. **Spec placement:** add recommender contract as spec appendix §13 if MCP uses
   §12, otherwise §12.

---

## 11. Why this should move earlier

The build plan calls Phase 8 the headline feature, but the current command
surface still presents it as a future stub. To make Neurobase legible, the next
milestone after lifecycle/MCP should prioritize at least Slices A and B:

- users can see recommendations as first-class local artifacts
- accepted proposals immediately create standard files
- MCP can list proposals even before mining is tuned
- the app's README/demo can lead with "memory that teaches your agents" rather
  than only "memory that recalls facts"

This keeps the no-telemetry/local-first promise intact while making the novel
piece visible.
