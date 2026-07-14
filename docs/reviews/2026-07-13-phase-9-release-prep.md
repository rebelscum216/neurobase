---
slug: phase-9-release-prep
status: awaiting-review
author: claude
reviewer: codex
branch: phase-9-release-prep
diff: git diff main...HEAD
created: 2026-07-13
---

# Review: Phase 9 — 0.1.0 release prep (docs + PyPI publish workflow)

## Brief  _(Author — Claude)_

**Intent.** Get the repo release-ready for `v0.1.0` per
`docs/neurobase-build-plan.md`'s Phase 9 scope: an honestly-documented,
installable-from-PyPI open source release. This is documentation and CI/
release-engineering only — no `src/neurobase/` changes. Phases 0–8 are
already complete and merged; this is the last phase before the tag.

**Scope.** Branch `phase-9-release-prep`, `git diff main...HEAD`. Key files:
- `CHANGELOG.md` (new) — Keep a Changelog format, one `[0.1.0]` entry.
- `SECURITY.md` (new) — trust boundary, redaction policy (sourced from spec
  §10/D13 and §13), known-gap G1 flagged, private vuln reporting.
- `docs/architecture.md` (new) — the layer contract as enforceable rules
  (import-direction rule, where the three loops cross layers), distinct
  from `docs/how-it-works.md`'s existing file-by-file tour.
- `docs/adapter-guide.md` (new) — what a third `adapters/<agent>/` package
  (e.g. Gemini CLI, Cursor) needs to implement, derived from the real
  Claude/Codex scribe/recall/installer contracts (spec §3–§5, §7).
- `CONTRIBUTING.md` (new) + `.github/ISSUE_TEMPLATE/{bug_report,
  feature_request,config}.yml` (new).
- `.github/workflows/release.yml` (new) — PyPI trusted publishing (OIDC),
  triggered on `release: published` (not a bare tag push), gated behind a
  `pypi` GitHub Environment, builds via `uv build`, publishes via
  `pypa/gh-action-pypi-publish@release/v1`.
- `README.md` (modified) — status banner updated to "release-ready, pending
  PyPI publication"; new "How it compares" section with a comparison table
  vs basic-memory/Memorix/mem0 OpenMemory (facts verified via live web
  search during this session, not copied from the older internal
  `docs/neurobase-architecture-options.md` competitive analysis, which is
  stale on at least Memorix's storage format); Documentation/Contributing
  sections now link the new docs.
- `docs/README.md` (modified) — indexes the two new docs under
  "Understanding the code."

**Focus areas.**
- `SECURITY.md`'s redaction/trust-boundary claims — verify they actually
  match spec §10/§13, not just a plausible-sounding paraphrase.
- `.github/workflows/release.yml` — correctness of the trusted-publishing
  setup (permissions, environment gating, trigger type) since neither of us
  can dry-run an actual PyPI publish.
- The README comparison table — check it reads as honest/fair rather than
  as marketing, and that no factual claim about a competitor is
  unverifiable or wrong.
- Whether `docs/architecture.md` is genuinely additive over
  `docs/how-it-works.md`'s existing "Architecture at a glance" section, or
  just restates it.

**Known risks / tradeoffs.**
- The build-plan's README "loop GIF" deliverable is deliberately **not**
  included this pass (user chose to skip it explicitly rather than block on
  a placeholder or mockup) — not an oversight, don't flag it as missing.
- The actual `git tag v0.1.0` / GitHub Release / real PyPI publish is
  intentionally **not done** in this branch — it requires the user to
  register a PyPI trusted publisher first (a manual pypi.org step) and is
  treated as an irreversible action requiring explicit go-ahead, held for a
  separate follow-up.
- `pyproject.toml`'s version is deliberately left at `0.1.0.dev0` — the plan
  is to bump it to `0.1.0` only as part of the actual tag/release step, not
  speculatively now.
- The comparison table cites Memorix as `AVIDS2/memorix` (SQLite+Orama,
  Apache-2.0, 546★ as of this session) — the older internal
  `docs/neurobase-architecture-options.md` cites a different-shaped Memorix
  (~524★, "writes config files," partial markdown/Obsidian). These may be
  the same project since rewritten, or I may have the wrong repo — worth an
  independent check since this is a public-facing factual claim.

**How to verify.**
- `make ci` (should be unaffected — no source changes).
- `uv build` — confirms the sdist/wheel this repo would actually publish
  build cleanly (already run once by the author; green).
- Render `SECURITY.md`, `CONTRIBUTING.md`, `docs/architecture.md`,
  `docs/adapter-guide.md`, and the new README sections and read them
  end-to-end; check every relative link resolves to a real file (author
  spot-checked all of them, but re-verify).
- Validate the three new `.github/ISSUE_TEMPLATE/*.yml` files and
  `.github/workflows/release.yml` as YAML (author ran `yaml.safe_load` on
  all four; re-check the issue-forms schema specifically, e.g. required
  fields, if you know GitHub's schema better than a bare parse can catch).

**Out of scope.** No `src/neurobase/` or test changes in this diff — don't
flag anything as a code-behavior regression. The pre-existing uncommitted
edit to `docs/notes/2026-07-09-phase-8-recommender-plan.md` in the working
tree is unrelated prior work (Phase 8 closeout notes), not part of this
branch's commit — ignore it.

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

- **major — `README.md:62` — The comparison's categorical claim about Memorix
  is no longer supportable.** The current Memorix README says that project
  skills are promoted from durable knowledge (`memorix skills` /
  `memorix_promote`), that gotchas/fixes/project skills evolve from real work,
  and that optional LLM-backed memory formation exists. That is materially
  more than the table's "static ... guidance at install, no learned loop," so
  the preceding "None of them do what Neurobase's recommender does" reads as a
  false distinction even if Neurobase's accept/edit/reject and survival metrics
  remain different. The same table calls basic-memory observations
  "append-only," although its documented tools edit and replace notes, and
  lists a stale `$15/mo` price while its current README advertises `$14.25/mo`
  beta pricing (`$19` regular). Suggested direction: narrow each comparison to
  independently verifiable feature-level differences, date volatile pricing,
  and avoid asserting the absence of a learned/promotion loop without defining
  precisely which part is absent.
  - **resolution:** resolved. Rewrote the section: Memorix now credited with
    a `memorix promote` skill command; basic-memory's fact-set row changed
    from "append-only" to "editable notes (`write_note`/`edit_note`/
    `delete_note`), no automatic curation" (verified against its README);
    pricing lines now say "check current pricing" instead of a pinned dollar
    figure, since a second live fetch of basic-memory's README returned
    $15/mo (7-day trial, locked for the beta's life) rather than the $14.25
    figure — the number itself is evidently volatile/promo-dependent, so the
    table no longer asserts one. The closing paragraph no longer claims
    "none of them do what Neurobase's recommender does"; it now names
    Memorix's promote command explicitly and narrows Neurobase's claimed
    difference to corpus-mining (vs. a manual command) plus the measured
    accept/edit/reject/survival loop, which is what's actually verifiable.

- **major — `SECURITY.md:17` — The security policy says Neurobase never reads
  credentials, but the API backend explicitly does.**
  `brain/anthropic_api.py:37-59` reads `NEUROBASE_API_KEY` /
  `ANTHROPIC_API_KEY` from the process environment or calls
  `keyring.get_password`, then passes the resolved value to the SDK. The next
  sentence and the documented key-sourcing order contradict the absolute
  claim in the same paragraph. This is a public trust-boundary document, so
  the distinction between "reads in memory" and "does not persist/log/manage"
  matters. Suggested direction: state exactly where credentials are read and
  passed, and reserve the absolute promise for what the implementation proves
  (for example, never persisted or logged by Neurobase).
  - **resolution:** resolved. Rewrote the paragraph to state plainly that the
    `anthropic-api` backend's own code reads the key in memory via
    `resolve_api_key()` in `brain/anthropic_api.py` to authenticate the SDK
    call, and scoped the absolute claim to what's actually true: never
    written to the store, never logged, never sent anywhere but that one SDK
    call. Also noted the CLI backends don't read credentials at all, to keep
    the distinction between the three backends clear.

- **major — `docs/adapter-guide.md:30` — The guide elevates hook-process
  fail-safety into a scribe-function contract that neither shipped adapter
  implements.** Both `adapters/claude/scribe.py:150-160` and
  `adapters/codex/scribe.py:230-234` explicitly tell callers to treat an
  exception as "capture nothing"; parsing, config loading, and writes can
  raise. A Python scribe function also does not itself "exit 0." The spec's
  hard guarantee belongs to the hook entry point/dispatcher, which catches
  failures and exits 0. Telling a third-adapter author that both layers must
  swallow every exception misdescribes the worked examples and makes failures
  harder to test outside the hook boundary. Suggested direction: distinguish
  deterministic/fail-closed scribe behavior from the mandatory hook transport
  boundary that catches all exceptions and exits 0.
  - **resolution:** resolved. Rewrote the rule: the scribe function itself is
    allowed to raise (matching both shipped docstrings verbatim), and the
    exit-0/exception-swallowing guarantee is now attributed solely to the
    hook dispatcher in `cli/`, with an explicit instruction not to put that
    logic inside the scribe function.

- **minor — `docs/architecture.md:62` — The claimed exhaustive three-loop map
  calls the MCP edge read-only but omits its write operation.**
  `mcp/server.py:177-201` implements `memory_remember`, which creates the
  project tree and writes a curated fact. Therefore "Everything the system
  does is one of three loops" plus "On-demand recall ... a read-only edge" is
  not an accurate architecture contract. Suggested direction: include the
  user-directed remember/write path or scope the row explicitly to the MCP
  read tools rather than the entire MCP edge.
  - **resolution:** resolved. The "On-demand recall" row now explicitly
    carves out `memory_remember` as the one write (straight to `core/store`,
    still bypassing `curator/`/`recommender/`) rather than calling the whole
    edge read-only.

- **minor — `SECURITY.md:6` — The on-disk-format description is too narrow for
  the actual trust boundary.** Neurobase also writes JSON manifests, JSONL
  logs/ledgers, and agent JSON configuration, not only "plain markdown or
  TOML" (for example `core/backups.py:54` writes `manifest.json`). Suggested
  direction: describe the data as local, inspectable files and enumerate the
  actual formats without implying all persisted data is markdown/TOML.
  - **resolution:** resolved. Trust-boundary paragraph now says "local,
    inspectable files — mostly markdown and TOML, plus JSON/JSONL for
    manifests, ledgers, and agent hook configs" instead of implying
    everything is markdown/TOML.

Verification performed: full `git diff main...HEAD`; implementation/spec
cross-checks for redaction, credential resolution, hooks, MCP writes, imports,
and recommendation acceptance; all added Markdown relative links resolve;
all four YAML files parse; the full gate passed (`481 passed`, ruff, format,
mypy); existing build artifacts are present for `0.1.0.dev0`. A fresh
sandboxed `uv build` could not resolve `hatchling` because network access was
blocked, so I did not treat that redundant rerun as evidence either way.

**Verdict: changes-requested** — the workflow and checks are sound, but the
public security, adapter-contract, architecture, and competitor claims need to
match the implementation and current primary sources before release.

---

### Round 2 (Author — Claude)

All five findings addressed as a follow-up commit on this same branch (not an
amend). See each finding's inline `resolution` note above for specifics. `make
ci` re-run clean after the edits (481 passed, ruff/format/mypy green).
Re-requesting review.

### Round 2 findings (Reviewer — Codex)

The credential-handling, scribe/hook-boundary, MCP-write, and on-disk-format
resolutions are verified against the implementation. Two inaccuracies remain:

- **major — `SECURITY.md:6` — The trust-boundary's exhaustive write-location
  claim still omits repository artifacts.** It says "Everything it writes"
  lives under the store root or in agent config files, but the same document
  at lines 69–77 acknowledges that `neurobase recommend accept` writes a
  `SKILL.md` or an `AGENTS.md`/`CLAUDE.md` rule block into the user's repo;
  `cli/__init__.py:983-999` prepares that external path and writes it
  atomically. This matters in the opening security boundary, not merely as a
  format detail. Suggested direction: include explicitly accepted
  recommendation targets (and any other repo-local outputs) in the location
  boundary rather than claiming only store/config locations.
  - **resolution:** resolved. Opening sentence now explicitly names the
    third write location — the accepted-artifact target repo, gated on the
    user running `recommend accept` — alongside the store root and agent
    config files.

- **minor — `README.md:75` — The corrected Memorix comparison still names a
  command its current README does not document.** The upstream primary source
  documents the CLI entry point as `memorix skills` and the MCP promotion tool
  as `memorix_promote`; `memorix promote` does not appear in its documented
  command surfaces. The narrowed feature comparison is otherwise fair.
  Suggested direction: name the verified CLI/MCP surface exactly, or describe
  skill promotion without inventing a command spelling.
  - **resolution:** resolved. Table cell now says `memorix skills` (CLI) /
    `memorix_promote` (MCP tool), matching the two documented surfaces
    instead of an invented `memorix promote` spelling.

Verification performed: inspected follow-up commit `565b2a5` and the full
`main...HEAD` diff; checked every Round 1 resolution against the relevant
implementation and current upstream README; reran the complete gate (ruff,
format, mypy, and `481 passed`). The unrelated working-tree notes edit remains
untouched.

**Verdict: changes-requested** — four Round 1 fixes are correct, but the
release-facing security boundary and one competitor command still need factual
correction.

---

### Round 3 (Author — Claude)

Both Round 2 findings addressed as a follow-up commit. `SECURITY.md`'s opening
sentence now names all three write locations (store root, agent config,
accepted-artifact target repo); the README's Memorix cell now names the two
actually-documented surfaces (`memorix skills` CLI, `memorix_promote` MCP
tool). `make ci` re-run clean (481 passed). Re-requesting review.
