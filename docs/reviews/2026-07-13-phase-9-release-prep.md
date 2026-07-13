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

**Verdict:** _pending_
