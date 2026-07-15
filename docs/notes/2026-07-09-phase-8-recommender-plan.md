# Phase 8 — Recommender v1: execution plan

_2026-07-09 — working plan for build-plan Phase 8. This is a plan, not a
contract. The contract lands in `docs/neurobase-spec-appendix.md` before code._

> **STATUS (2026-07-12): Phase 8 complete.** Workstreams A–D merged earlier
> (#5, #6, #7, #9). E+F+G landed together as one branch/PR (ranker, proposal
> store, `recommend` CLI, emitters — `main`@`1b00b44`) after a 5-round
> Claude↔Codex relay (13 findings, all resolved; see
> `docs/reviews/2026-07-10-phase-8-workstream-efg-recommender.md`). H
> (metrics) landed separately (`main`@`be21e2f`) after a 3-round relay (3
> findings, all resolved; see
> `docs/reviews/2026-07-10-phase-8-workstream-h-metrics.md`) — notably
> hardened `decided`/`precision`/`edited_rate` to require the ledger, not a
> proposal file's own `status` field, to confirm a decision (ADR-0011 also
> added, documenting the new `installed_hash` ledger field survival needs).
> Every "Done when" bullet below was then verified **live** against this
> repo's real dogfood store (`~/neurobase`), using a scripted fake `Brain`
> for mining (no LLM backend was configured in that session's shell) feeding
> genuinely-evidenced candidates from the real corpus — see the "Done when"
> section for the checked-off results. No further Phase 8 work is planned;
> `wip-vscode-extension-docs` (pulled out of the E/F/G branch, unrelated) is
> the only loose thread left from that arc.

## Goal

Turn Neurobase's cross-agent memory corpus into human-reviewed proposals for
durable agent behavior: SKILL.md folders and fenced AGENTS.md / CLAUDE.md rule
blocks. The recommender must propose, evidence, and learn from accept/reject
signals; it must never auto-install anything.

Phase 8 is the product's novel contribution, so the bar is not "generate some
text." The bar is: a proposal is explainable from evidence, safe to reject, and
installed only through the same consent / diff / backup discipline as hooks.

## Current gaps

- `src/neurobase/recommender/` is a docstring-only stub.
- `neurobase recommend` and `neurobase seed` are still planned stubs.
- The spec appendix intentionally jumps from §11 to §13; §12 is reserved for
  the recommender and must be written before implementation.
- MCP `recommendations_list` already reads `<root>/proposals/*.md`; Phase 8
  owns that format and must keep it compatible.
- The real dogfood corpus is thinner than ideal: MCP `memory_remember` has one
  pinned fact, and prior build history exists in raws/curated only where hooks
  actually captured it. Seeding is therefore part of the demo path, not polish.

## Decisions to lock

### D14 — Proposal identity and lifecycle

Proposal files live at `<root>/proposals/<slug>.md`; slugs match the existing
`^[a-z0-9-]+$` rule. Frontmatter owns machine-readable state:

- `name`
- `status`: `proposed | accepted | rejected | superseded`
- `type`: `skill | rule`
- `target`: path or target family (`user-skill`, `project-skill`,
  `AGENTS.md`, `CLAUDE.md`)
- `project`: optional source project
- `candidate_type`: `repeated-correction | repeated-workflow |
  repeated-instruction | cross-project-convention`
- `scores`: recurrence, breadth, recency, total
- `evidence`: structured references, e.g.
  `{"kind":"curated","project":"...","slug":"..."}`,
  `{"kind":"raw","project":"...","file":"..."}`, or
  `{"kind":"proposal","slug":"..."}`
- `created_at`, `updated_at`

The markdown body is the human proposal: title, rationale, evidence summary,
draft artifact body, and caveats.

### D15 — Review is consent-first

`recommend accept` shows the exact artifact diff and writes only after consent.
It backs up touched files under `<root>/backups/<ts>/` with a manifest, matching
the installer discipline. `reject` writes only the proposal + ledger state. No
proposal ever auto-installs.

### D16 — Learning is local ledger, not hidden model state

Feedback lives at `<root>/recommender/ledger.jsonl`; miner prompts receive a
compact rejection/acceptance summary. V1 learning is deterministic around that
ledger: deprioritize rejected candidate types and near-duplicate rejected
proposal bodies; surface accepted survival/reduction metrics.

These decisions should be recorded in ADR-0007 before implementation.

## Workstreams

### A. Contract first

Add spec §12, plus ADR-0007 and ADR index entry.

Spec §12 should define:

- on-disk proposal format
- ledger format
- seed import contract
- miner input/output JSON
- ranking defaults
- accept/reject side effects
- fenced rule ownership markers
- fail-soft behavior
- consent/diff/backup requirements
- metrics semantics

Done when: every MUST in §12 has a named test target in the plan.

### B. Seed importer

Implement `neurobase seed --from-dir <path>` first; `--from-claude-memory` can
land in the same slice if the local layout is discoverable, otherwise it gets a
documented seam and tests with fixtures.

Behavior:

- recursively import markdown-ish files as curated facts with provenance
  `seed:<source>`
- preserve source path in frontmatter/evidence metadata
- redact before writing
- skip empty/unreadable files
- dedupe by slug and source digest so repeated seed runs are idempotent
- require explicit path/flag; no automatic crawling of personal folders

Tests:

- idempotent import
- redaction before curated write
- bad directory / unreadable file fail-soft
- provenance and source metadata
- directory recursion imports a nested file (e.g. `notes/sub/file.md`)
- `seed` requires an explicit `--from-dir` or `--from-claude-memory`;
  omitting both is a CLI error
- `--from-claude-memory` with neither `--project` nor `--all-projects`
  imports exactly the single project resolved from launch cwd; an
  unresolvable cwd is a CLI error

### C. Corpus loader and evidence model

Build a reusable loader over all registered projects:

- active curated facts
- recent raw captures, capped by age/count
- accepted/rejected ledger summaries

Evidence references should be structured, not stringly:

- `{"kind":"curated","project":"...","slug":"..."}`
- `{"kind":"raw","project":"...","file":"..."}`
- `{"kind":"proposal","slug":"..."}`

Tests:

- all-project registry traversal
- missing/bad project tree skips
- raw cap enforced
- evidence references serialize into proposal frontmatter

### D. Miner

Use injectable `Brain` exactly like the curator. The miner asks for candidates
only; it does not write proposals.

Candidate JSON:

- `slug`
- `type`: `skill | rule`
- `candidate_type`
- `title`
- `rationale`
- `draft`
- `target`
- `evidence`
- `occurrences`
- `projects`
- `agents`
- `supersedes`: optional proposal slugs

Prompt constraints:

- optimize for repeated durable behavior, not one-off facts
- include only candidates evidenced at least `K` times unless explicitly seeded
  as high-confidence
- do not propose secrets, credentials, or private personal content
- honor rejected near-duplicates from the ledger

Tests use fake brains:

- unparseable miner JSON leaves proposals unchanged
- invalid candidates skipped with warnings
- rejected near-duplicate summary reaches prompt

### E. Ranker and proposal store

Ranking defaults from the build plan:

- recurrence threshold: at least 3 occurrences
- breadth threshold: at least 2 sessions, any agent mix
- total score = recurrence x breadth x recency-weight

Proposal write behavior:

- upsert same slug when candidate meaning is the same
- supersede proposals only by explicit candidate `supersedes`
- never overwrite accepted/rejected proposals with a fresh proposed body
- MCP `recommendations_list` can read summaries without understanding the full
  body

Tests:

- threshold enforcement
- stable ordering
- rejected/accepted proposals are not silently reset to proposed
- malformed proposal files skipped
- a secret-shaped string in a miner candidate's draft is redacted before the
  proposal file is ever written
- ranker recomputes occurrences/breadth/sessions from evidence, ignoring a
  miner's inflated self-reported counts
- a proposal edited by the user is not silently overwritten by a subsequent
  `recommend run`

### F. `neurobase recommend`

Subcommands:

- `recommend list [--project] [--status]`
- `recommend show <slug>`
- `recommend run [--dry-run]`
- `recommend edit <slug>` (opens/prints an editable draft, then records an
  edited proposal body or draft artifact without installing it)
- `recommend accept <slug> [--target user|project] [--yes]`
- `recommend reject <slug> [--reason TEXT]`

CLI output should be terse and diff-oriented, consistent with `init`.

Tests:

- list/show on empty proposals
- dry-run prints candidates without writes
- edit updates the proposal body/draft and appends an `edited` ledger event
- `recommend edit`'s saved draft is redacted before it replaces the
  proposal's stored body
- accept requires consent unless `--yes`
- reject updates proposal + ledger

### G. Artifact emitters

Skill emitter:

- creates `SKILL.md` under user or project skill scope
- validates required headings/frontmatter according to the local skill format
- never edits existing unrelated skill files without diff/consent

Rule emitter:

- writes fenced Neurobase-owned blocks into AGENTS.md / CLAUDE.md
- preserves unrelated prose
- removes/replaces only Neurobase-owned blocks for that proposal slug

Tests:

- diff/backup/consent
- idempotent accept
- rollback-safe backup manifest
- unrelated content preserved byte-for-byte outside the owned block
- skill emitter treats a target SKILL.md as owned only via
  `neurobase_managed`+`neurobase_slug`, never silently overwriting a foreign
  file
- accept's rendered artifact is redacted before the diff is shown or the
  artifact file is written (belt-and-suspenders on top of the draft already
  being redacted at write time)

### H. Metrics

Add `status --recommender` or `neurobase recommend metrics`:

- precision: accepted / reviewed, where reviewed includes accepted, rejected,
  and edited proposal events
- survival: accepted artifact still present and unmodified after 30 days
- recurrence reduction: candidate pattern appears less often after acceptance
- edited rate: edited / reviewed, so the recommender can distinguish useful
  but not-quite-right proposals from outright misses

V1 can compute survival/reduction opportunistically and report "insufficient
data" when history is too thin.

Tests:

- metrics on empty ledger
- accepted/rejected/edited counts
- missing artifact marks survival false only after the configured window
- a malformed line in `recommender/ledger.jsonl` is skipped, not fatal, by
  metrics computation

## Review slices

Keep Phase 8 out of one mega-review:

1. **Plan/spec review** — this note, spec §12, ADR-0007.
2. **Seed + corpus loader** — no LLM writes yet.
3. **Miner + ranker + proposal store** — fake-brain tests, no artifact writes.
4. **Recommend CLI + edit/accept/reject + emitters** — consent/diff/backup heavy.
5. **Metrics + dogfood demo** — real corpus run, follow-up docs.

## Done when

All verified live on 2026-07-12 against the real dogfood store (`~/neurobase`),
mining via a scripted fake `Brain` whose candidates were hand-authored from
genuine corpus patterns (real curated facts + raw captures, real evidence
refs) rather than a live LLM call:

- [x] Seeded corpus yields at least 3 sensible proposals on this machine. —
  3 candidates, each backed by real multi-session/multi-agent evidence,
  cleared the ranker's threshold gate and were written as real proposals.
- [x] At least 1 proposal is worth accepting.
- [x] Accept produces a valid SKILL.md that Claude Code loads. — confirmed:
  the installed skill appeared in that same session's own available-skills
  list. (The demo skill was later deliberately removed — it duplicated the
  repo's existing `xcode-review` skill almost entirely — but the accept path
  itself, including the live load, was genuinely exercised and verified.)
- [x] Edit records an `edited` ledger event and preserves the user's revised
  draft. — one event; only the managed draft region changed, review prose
  untouched.
- [x] Reject suppresses similar candidates on the next run. — confirmed at
  both layers: the rejected snippet reached the miner's prompt, and the
  ranker's own independent near-dup re-check separately declined a fresh
  near-duplicate candidate.
- [x] Ledger metrics render without crashing and show meaningful reviewed
  counts. — `status --recommender` on the real store: correct decided/
  precision/edited_rate/reviewed_events/survival, no crash.
- [x] `recommendations_list` over MCP shows proposal summaries. — called live
  over the connected MCP server; returned all 3 proposals with correct
  status/type/target.
- [x] `make ci` / `uv run python scripts/ci.py` is green locally and in CI. —
  green on `main` after both the E+F+G and H merges, all matrix jobs.

## Out of scope for Phase 8

- Hosted sync, telemetry, or any model/server owned by Neurobase.
- Vector search / FTS index.
- Team/shared proposal workflows.
- Automatic installation of skills/rules.
- Perfect Windows UX beyond CI-green behavior.
