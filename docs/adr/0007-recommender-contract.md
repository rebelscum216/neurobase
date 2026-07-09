# ADR-0007: Phase 8 recommender — proposal/ledger shape, ranking, and consent-first review

- **Status:** Accepted
- **Date:** 2026-07-09
- **Resolves:** Phase 8 plan decisions D14–D16 (build plan §6; execution plan
  `docs/notes/2026-07-09-phase-8-recommender-plan.md`; review
  `docs/reviews/2026-07-09-phase-8-recommender-plan.md`, approved), plus five
  implementation-concreteness gaps recorded here as D17–D21 so the spec
  appendix §12 has one settled number/algorithm/marker for each, not an
  ambiguity implementers would each resolve differently
- **Supersedes:** none

## Context

Phase 8 is the recommender: mine the cross-agent, cross-project corpus for
recurring durable behavior, rank and evidence it, and let the user review,
edit, accept, or reject a proposal — accept renders a SKILL.md folder or a
fenced AGENTS.md/CLAUDE.md rule block through the same consent → diff → backup
discipline the `init` installers already use. It is the product's novel
contribution and the largest phase (2–3 sessions), and the build plan
deliberately reserved spec §12 (jumping straight from §11 to §13) and this ADR
number for it.

The Phase 8 execution plan locked three decisions — D14 (proposal identity and
lifecycle), D15 (consent-first review), D16 (ledger-based learning) — and its
review round (`docs/reviews/2026-07-09-phase-8-recommender-plan.md`) went
through two rounds: round 1 flagged that workstream F silently dropped the
build plan's `recommend edit` deliverable and that the `evidence` frontmatter
field was specified two contradictory ways (bare slugs vs. structured refs);
both were fixed and confirmed resolved in round 2, which approved the plan but
flagged one remaining nit as deliberately deferred to this contract: whether
an edited-then-accepted proposal counts once or twice against the `precision`
denominator (folded in below as D19).

Turning that approved plan into an implementable spec (§12) surfaced five
places where "the plan says what, not exactly how much/which algorithm/which
marker" would leave real ambiguity for an implementer: the corpus loader's
"capped by age/count" (no numbers); the miner/ledger's "near-duplicate"
suppression (D16 promises it, no algorithm — and it must be deterministic,
since workstream D's fake-brain tests require the *code*, not the model, to
decide what counts as a duplicate); the skill emitter's "never edits existing
unrelated skill files" (no mechanism to tell owned from foreign); what happens
when a written evidence reference later points at nothing (a tombstoned/pruned
curated fact, a hand-deleted raw file); and the plan review's own deferred
precision-denominator nit just described. All five are resolved below as
D17–D21.

A spec-drafting review pass over §12 also surfaced two safety-shaped gaps that
this ADR closes alongside D17–D21, since they change what D15/D16 actually
guarantee in practice rather than adding new decisions of their own: (1)
`neurobase seed --from-claude-memory` must not, absent an explicit
multi-project opt-in, loop over every registered project's auto-memory
directory in one invocation — doing so would violate D15's own "never touch a
directory the user didn't name" framing; §12.3 now defaults it to the single
project resolved from the CLI's launch cwd. (2) A proposal's rendered artifact
draft — not just a seeded curated fact — must be redacted before it is shown
or written, since AGENTS.md/CLAUDE.md/SKILL.md are durable, often
git-committed artifacts where a redaction miss is worse than a miss in the
local-only store; §12.8 promotes this from an implicit assumption to an
explicit deterministic pass. Both are captured in the Decision section below
under D15's scope rather than as new lettered decisions, since they refine an
already-locked decision rather than introducing a new one.

## Decision

- **D14 — Proposal identity and lifecycle.** Files at
  `<root>/proposals/<slug>.md`, the store's frontmatter+body pattern reused
  verbatim; `status: proposed|accepted|rejected|superseded`;
  `type: skill|rule`; `target` names the artifact family
  (`user-skill`/`project-skill`/`AGENTS.md`/`CLAUDE.md`). For a `rule`
  proposal `target` is fixed at mining time and never changes; for a `skill`
  proposal the miner's `target` is only an advisory default scope that
  `recommend accept --target user|project` can confirm or override, and a
  successful accept updates `target` to record the scope actually used.
  `evidence` uses the **structured** reference shape
  (`{"kind":"curated"|"raw"|"proposal", ...}`) — the plan review's round-1 F2
  fix, confirmed in round 2, is the final word; the bare-slug alternative in
  the plan's first draft is dead. Two frontmatter fields are added beyond the
  plan's original D14 field list: `supersedes` (list of prior slugs,
  mirroring `curated/`'s own `supersedes` key, so a superseding write has
  somewhere to record what it replaced) and `installed_path` (str, null until
  `accept` sets it to the concrete absolute path actually written) — kept as
  its own field, distinct from `target`'s categorical family/scope, so a
  reader always has both "which family/scope" and "which literal file"
  without either field having to carry both meanings. `recommend reject` is a
  hard CLI error on a proposal that is already `accepted` (as well as already
  `rejected`/`superseded`) — v1 has no command-based uninstall, so `reject`
  must not be a backdoor that flips an installed proposal's status while
  leaving the real artifact in place; see D15.
- **D15 — Review is consent-first.** `recommend accept` always shows the
  exact artifact diff and writes only after consent; `--yes` skips the
  confirmation prompt, never the diff. It backs up every touched file under
  `<root>/backups/<ts>/` with a manifest via the existing
  `core/backups.py:backup_files` — no parallel backup mechanism. `reject`
  writes only proposal + ledger state, and is blocked entirely on an
  already-`accepted` proposal (D14) — no proposal ever auto-installs, and no
  installed proposal is silently un-installed by metadata alone. Two scope
  refinements to this decision, surfaced during spec drafting rather than in
  the original plan: **(a)** `neurobase seed --from-claude-memory`, absent an
  explicit `--project` or the new explicitly-named `--all-projects` opt-in,
  resolves and imports exactly the single project implied by the CLI's launch
  cwd (`projects.resolve_project(root, cwd)`) — never a silent loop over
  every registered project's auto-memory directory, which would otherwise
  import personal notes from projects the user never named in that
  invocation; **(b)** a proposal's rendered artifact body MUST be passed
  through `core/redact.py:redact` before `recommend show` displays it and
  again before `accept` writes it — the miner prompt's "never propose
  secrets" instruction (§12.5) is real but advisory-only, so the one durable,
  often git-committed write surface in this contract gets the same
  deterministic backstop every other write path (`seed`, the scribes) already
  has.
- **D16 — Learning is a local ledger, not hidden model state.** Feedback lives
  at `<root>/recommender/ledger.jsonl`, append-only; the miner prompt receives
  a compact, code-computed summary of rejected candidate-types and
  near-duplicate rejected bodies (D18 makes "near-duplicate" concrete).
  Deprioritization is enforced by instructing the miner *and* by the ranker
  independently declining to re-surface a fresh candidate that collides with
  a still-rejected near-duplicate — belt and suspenders, since the miner is
  advisory-only for anything the code can check deterministically (see D18).
  The plan's original D16 also named a second half — "surface accepted
  survival/reduction metrics" — which is not dropped here but given its full
  semantics separately under D19, alongside the metrics contract's other
  denominator rules; D16 and D19 together are the complete restatement of the
  plan's original D16 scope. One more ledger-consistency rule belongs here: a
  fresh candidate whose slug matches a `proposed` proposal that the user has
  since `recommend edit`-ed MUST NOT be silently upserted over that edit — the
  ranker either skips the refresh for that slug or preserves the edited
  body/draft while refreshing only scores/evidence, so `recommend edit`'s own
  guarantee (the user's revision persists until a new decision) isn't
  quietly undone by the next `recommend run`.
- **D17 — Corpus loader scope and caps.** The plan's "recent raw captures,
  capped by age/count" gets concrete tuned defaults (config-overridable, spec
  §12.11): `raw_lookback_days = 30`, `raw_cap_per_project = 200`, whichever is
  fewer, per project, across every registered project (not one arbitrary
  global cutoff). Curated facts are never capped — the curator already keeps
  that set small by design (spec §2), so it's cheap to include in full.
- **D18 — Near-duplicate detection is deterministic, not LLM-judged.**
  Normalized token-overlap similarity (lower-cased word tokens, the same
  tokenization shape `core/search.py` already uses) between a rejected
  proposal's body and a fresh candidate's draft; `threshold = 0.6` (Default,
  config-overridable). This is computed in plain code specifically so
  workstream D's fake-brain tests ("rejected near-duplicate summary reaches
  prompt") don't need a fake brain that can also judge similarity — the code
  decides what's a near-duplicate and hands the model a summary, not a
  judgment call to make itself.
- **D19 — Precision/edited-rate/survival semantics.** Resolves the plan
  review's deferred round-2 nit. `precision` and `edited_rate` are computed
  over **decided proposals** (current `status` = `accepted`/`rejected`, one
  proposal = one unit), not raw ledger event counts — an edited-then-accepted
  proposal contributes exactly 1 to `decided`, so intermediate edits cannot
  dilute `precision`. A separate, explicitly **event-counted**
  `reviewed_events` metric (the raw count of `accepted`+`rejected`+`edited`
  ledger lines) is reported alongside for the plan's original "reviewed"
  language, but is never the denominator of `precision` or `edited_rate` —
  that distinction is the entire point of this decision and must not be
  allowed to blur back together. `survival` reports "insufficient data" (not
  `false`) until `survival_window_days` (Default `30`) have elapsed since
  acceptance. Recurrence reduction remains best-effort/advisory, matching the
  execution plan's own "opportunistic v1" framing (the second half of the
  plan's original D16, given full semantics here) — it is not promoted to a
  MUST by this ADR. **Test-coverage gap, disclosed rather than papered over:**
  no workstream H test currently names the specific "edited-then-accepted
  counts once in `decided`, not once per ledger line" behavior that is this
  decision's entire point; spec §12.9 flags this and recommends the test be
  added before the metrics contract is treated as fully gated.
- **D20 — Skill artifact ownership marker.** A SKILL.md the emitter writes
  carries two Neurobase-internal frontmatter keys — `neurobase_managed: true`,
  `neurobase_slug: <slug>` — invisible to the skill's own contract (which only
  needs `name`/`description` + an H1 body). A target path is "owned" iff both
  match (a target whose frontmatter fails to parse at all is treated the same
  as "not owned," never as a propagated parse error); re-accepting an owned
  file is an idempotent diff-and-overwrite. A target that exists but isn't
  owned is still written only through the single existing diff → consent →
  backup gate (no second confirmation mechanism), with the diff view calling
  out explicitly that this replaces non-Neurobase content — the always-taken
  backup is what makes this reversible. The ownership-*detection* mechanism
  itself (matching on the two `neurobase_*` keys) is a gated MUST, closed by a
  named workstream G test (spec §12, Invariants and §12.8). **One
  test-coverage gap remains, disclosed rather than settled:** the emitter's
  required-shape validation (frontmatter `name`/`description` + H1) is still
  stated as design intent, not a gated MUST — no workstream G test names it
  yet; spec §12.8 names the recommended test addition.
- **D21 — Evidence resolution is fail-soft, and evidence is never pruned.**
  A proposal's `evidence` list is an append-only historical record. If a
  referenced curated fact is later tombstoned/pruned or a raw file is
  hand-deleted, readers (the corpus loader, `recommend show`) mark that item
  unresolved rather than raising or silently dropping it from the frontmatter.

## Consequences

- Spec appendix gains **§12 (Recommender contract)**, filling the §11→§13 gap
  the build plan reserved; `core/config.py` gains `[recommend]`
  (`min_occurrences`, `min_breadth_sessions`, `recency_halflife_days`,
  `raw_lookback_days`, `raw_cap_per_project`, `near_duplicate_threshold`,
  `survival_window_days`) — same dataclass pattern as `McpConfig`.
- **`--from-claude-memory`'s discovery path is no longer an open spike** — it
  is live-verified on a real machine at
  `~/.claude/projects/<cwd-with-'/'→'-'>/memory/` (confirmed contents:
  `MEMORY.md` as the index plus topic files with `name`/`description`/
  `metadata.type` frontmatter, exactly §10's existing "Seeder mapping"
  section). This closes the plan's own flagged risk without a separate spike;
  `--from-dir` remains the fallback if Claude Code ever changes the
  convention. Its *scope*, not just its discovery path, is now also settled
  (D15(a)): one project per invocation by default, cwd-resolved, with
  `--project`/`--all-projects` as explicit widenings.
- Both artifact emitters resolve `<project-root>` from the proposal's
  `project` field via `registry.toml` (the first registered root), never from
  `recommend accept`'s launch cwd — the same "don't trust a single session
  cwd" principle ADR-0008's D-c already established for MCP reads, applied
  here to writes. A cross-project proposal (no `project`) can only be
  accepted `--target user`. Because `load_registry` returns a plain dict, a
  stale `proposal.project` (deregistered or renamed after the proposal was
  written) is resolved via an explicit membership check and surfaces as a
  named hard CLI error, never a bare `KeyError`.
- The `target`/`installed_path` split means every reader (including MCP
  `recommendations_list`, which already surfaces `target` verbatim) can treat
  `target` as "family/scope" and `installed_path` as "the literal file" —
  neither field ever has to change what kind of information it carries across
  the proposal's lifecycle. `recommendations_list`'s own alphabetical-by-
  filename ordering is intentionally independent of `recommend list`'s
  score-ranked sort contract; unifying them, if ever wanted, is a follow-up to
  the Phase-7 MCP tool with its own test.
- The build plan's global decision table (§3, D1–D13) and Phase 8's own D14–16
  should eventually list D17–D21 too, for the same reason ADR-0008's D-a…D-e
  live only in that ADR rather than the global table — a follow-up doc pass,
  not a blocker to implementation.
- The rule emitter reuses `core/linkify.py`'s marker style and
  replace-wholesale-on-rerun idea — HTML-comment-delimited, rewritten in full
  on every accept — extended to be slug-scoped and to allow several such
  blocks to co-exist in one file; `linkify.py`'s own single, unparameterized
  `lineage:auto` block does neither of those two things today, so this is a
  reuse of the convention, not of the code itself. The skill emitter's
  ownership marker is new but additive-only (two frontmatter keys no other
  consumer reads).
- The miner reuses `Brain.plan_json` and `brain/base.py:parse_plan_json`
  unchanged, which is why its response envelope is a JSON object
  (`{"candidates": [...]}`) rather than a bare array — `parse_plan_json`
  requires a top-level mapping. `mine()`/`recommend run` catch `BrainError`
  broadly (the same `except BrainError` shape `curator/engine.py:curate`
  already uses), not only the malformed-JSON case the plan named explicitly —
  a plain timeout or exhausted-retry failure is at least as likely in
  practice and must not propagate past the CLI either.
- Ranking is deliberately **not** trusting the miner's self-reported
  `occurrences`/`projects`/`agents` — the ranker recomputes all three from the
  `evidence` list and the corpus loader's per-file metadata, which is both
  more robust to an LLM miscounting and easier to test with a fake brain that
  only needs to emit a correct evidence list. This determinism guarantee is
  now a gated MUST, closed by a named workstream E test (spec §12.5/§12.6).
  The supersede-only-retires-an-undecided-proposal rule in D14 remains a
  design decision stated plainly in spec §12 without a named workstream test
  behind it yet; §12.6 recommends the specific test to add.
- Still open, deliberately out of scope for v1 and left for a later ADR if it
  becomes a real ask: removing/reverting an already-accepted rule block via a
  command (today, a user edits the file by hand, and `reject` is explicitly
  blocked from serving as a substitute — D14); hosted sync, vector/BM25
  indexing, and team/shared proposal workflows (all Backlog per the execution
  plan).
- A prior, unmerged WIP on branch `phase-8-recommender-scope` (commit
  `a202ed6`, never merged to `main`) already used the number ADR-0007 for a
  materially different, since-abandoned design (`SKILL.md` enable/disable via
  a `.neurobase-disabled/` directory move, `emitted_path`/`disabled_path`
  fields). None of that design survives into this ADR — this document's
  `Alternatives considered` section records it as a considered-and-dropped
  option for the record. The stale file on that orphan branch should be
  deleted or marked abandoned before/if that branch is ever revisited, so the
  ADR-0007 number stays unambiguous; that branch cleanup is tracked as a
  follow-up outside this document's own scope, not performed by it.

## Alternatives considered

- **Keep `evidence` as bare slugs/filenames (D14's first draft)** — simpler
  strings, but loses the `project` qualifier the all-project corpus loader
  needs to resolve a slug back to its source, and was already rejected by the
  plan review's F2 finding before this ADR was written.
- **Let the miner judge near-duplicates itself (no D18)** — one less
  algorithm to write, but makes "rejected near-duplicate summary reaches
  prompt" untestable without a fake brain that also has to fake good
  similarity judgment, and makes suppression non-deterministic across runs.
- **Trust the miner's self-reported `occurrences`/`projects`/`agents`
  (no ranker-side derivation)** — less code, but an LLM miscount would
  silently mis-rank or wrongly gate a candidate past/under the threshold with
  no way to catch it in a fake-brain test.
- **A second, stronger confirmation gate for a foreign-file skill collision
  (beyond D20's single diff/consent/backup flow)** — considered, rejected as
  overbuilt for v1: the existing backup-before-first-modification rule already
  makes any foreign-file overwrite reversible, and a second gate would be a
  second thing to keep in sync with the first.
- **Raw ledger-event counts as the `precision` denominator (reviewed = literal
  accepted+rejected+edited line count)** — the plan's original wording, but
  the round-2 review nit correctly identified that it dilutes precision for
  any proposal edited more than once before its decision; D19's
  decided-proposal denominator fixes that without discarding the plan's
  "reviewed" language (kept as a separate, non-denominator, event-counted
  metric instead).
- **Skill enable/disable via a `.neurobase-disabled/` directory move, with
  `emitted_path`/`disabled_path` frontmatter fields (the abandoned
  `phase-8-recommender-scope` branch's original ADR-0007 draft)** — a fuller
  activation model with a real uninstall/disable command. Dropped for v1: it
  adds a second on-disk state machine (installed vs. disabled) on top of the
  proposal lifecycle's own `proposed`/`accepted`/`rejected`/`superseded`
  states, and the execution plan explicitly puts automatic
  installation/uninstallation out of scope for this phase. Revisit only if a
  later ADR is opened specifically for that ask.
- **Let `recommend reject` on an `accepted` proposal actually revert the
  installed artifact** — considered as the alternative to blocking it
  outright. Rejected for v1: it would require the same
  diff/consent/backup-restore machinery `accept` uses, run in reverse, which
  is exactly the "removing/reverting an already-accepted rule block via a
  command" work the execution plan already named out of scope; blocking
  `reject` on `accepted` instead keeps the proposal's status and the
  filesystem's actual state from ever silently diverging.
- **Lower-bound the draft-artifact redaction requirement to Advisory/SHOULD
  (leave the miner prompt as the only defense)** — the original spec draft's
  position. Rejected on review: AGENTS.md/CLAUDE.md/SKILL.md are the most
  exposed write surface in this contract (durable, often git-committed), so
  the one place redaction should be a deterministic MUST, not a prompt-only
  hope, is exactly here.
