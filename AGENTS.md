# AGENTS.md — operating guide for Neurobase

> This is the entry point for any coding agent (or human) working in this repo.
> Read it first. It is intentionally short and high-signal; it points to the
> authoritative documents rather than duplicating them.
>
> Neurobase's own headline feature *emits* AGENTS.md files — so treat this one as
> a worked example of the format: a tight map, not a manual.

## What this project is

Neurobase is an **open-source, local-first memory layer that follows a developer
across their coding agents** (Claude Code + Codex CLI in v1). It captures sessions
deterministically (hooks, no LLM at capture time), folds them on a schedule into a
*small* curated fact set via an LLM curator whose mandate is deletion and
supersession, synthesizes a wikilinked markdown wiki (Obsidian-readable,
git-friendly), and injects that memory back into future sessions scoped by project.
On top of the loop sits the novel piece: a **recommender** that mines the
cross-agent corpus for recurring patterns and proposes — never auto-installs —
promotions into standard **SKILL.md** and **AGENTS.md/CLAUDE.md** formats.

Everything runs on the user's machine, on their existing agent subscriptions, with
**zero cloud dependency and zero telemetry — permanently.**

## The three canonical documents (read in this order)

| Document | Role | How to treat it |
|---|---|---|
| [docs/neurobase-build-plan.md](docs/neurobase-build-plan.md) | The phased plan | **Follow it.** Work phase by phase; each phase's "done when" gates the next. |
| [docs/neurobase-spec-appendix.md](docs/neurobase-spec-appendix.md) | Behavioral contracts | **Law.** `MUST` = a contract tests enforce. Implement *from this spec*. |
| [docs/neurobase-architecture-options.md](docs/neurobase-architecture-options.md) | Researched rationale | Consult when a decision needs its "why." Discovery input, not a build order. |

The [docs/ index](docs/README.md) maps everything, including ADRs and working notes.

## Non-negotiable build principles

1. **Spec is law; tests enforce it.** Every `MUST` in the spec appendix gets a
   test. Tuned defaults come from spec §8, on-disk formats from §10, and §11's
   captured fixtures are the ground truth for parsers — write fixture tests from
   them on day one.
2. **Provenance discipline (critical).** The contracts were extracted from a prior
   private implementation that is **not in this repo and must never be consulted or
   copied.** Build *only* from the spec. If a contract gap surfaces mid-build,
   refine the spec appendix and continue from spec — source code never crosses the
   boundary.
3. **Fail-safe by default.** Hooks are deterministic, take stdin JSON, run no LLM,
   and **always exit 0** — never wedge an agent's session teardown or startup. On
   any error, capture nothing / inject nothing rather than crash.
4. **Optimize for deletion.** The curator's job is a *small, current, non-redundant*
   fact set — merging and supersession over accumulation. This is the load-bearing
   quality bar; the markdown substrate itself is a commodity.
5. **No telemetry. Ever.** Local stats only. It is the point of the product.
6. **Consent-first for anything outside our own files.** Installing hooks or writing
   agent config shows the exact diff, asks consent, backs up originals, and is
   idempotent + reversible. (clig.dev's rule; spec §7.)

## Repo layout (target — see build-plan §4 for the full tree)

```
neurobase/
├── AGENTS.md                 ← you are here
├── README.md                 ← project front door
├── LICENSE                   ← Apache-2.0 (decision D1)
├── pyproject.toml            ← [not yet] package "neurobase-cli", command "neurobase"
├── src/neurobase/            ← [not yet] core/ brain/ curator/ adapters/ recommender/ mcp/ cli/
├── tests/                    ← [not yet] round-trip + per-module; fixtures from spec §11
├── docs/                     ← canonical docs, ADRs, notes, code-review relay + reviews
├── .claude/skills/           ← project skills (e.g. code-review-relay, the Author role)
└── .github/workflows/ci.yml  ← [not yet] 3-OS matrix
```

Sections marked `[not yet]` are Phase 0 scaffolding not yet created. Update this
list — and remove the markers — as they land.

## Current state

- **Phase:** 0 (repo bootstrap). The founding docs and this operating guide exist;
  the Python package, tests, and CI do **not** yet.
- **Naming (decision D2):** PyPI package = `neurobase-cli`, command = `neurobase`
  (`neurobase` is taken on PyPI). The npm `neurobase` name is a *defensive
  reservation only* — this is a **Python** project; `package.json`/`index.js` are a
  placeholder holding that name, not part of the build.
- **License (D1):** Apache-2.0.

## Dev workflow (fill in as Phase 0 lands)

Once `pyproject.toml` exists, the intended commands (build-plan §9) are:

```bash
uv sync                # install deps into a managed venv
uv run pytest          # run the suite — the contract enforcer
uv run ruff check .    # lint
uv run ruff format .   # format
uv run mypy src        # types (lenient to start)
```

Until then there is no build. Do not invent commands that don't resolve — if a
tool isn't wired up yet, say so.

## Where to put things

- **A new design decision or spike outcome** → an ADR in
  [docs/adr/](docs/adr/README.md) (copy `0000-template.md`). The plan mandates ADRs
  for every spike (S1–S6) and every change to the decision table (D1–D13).
- **Scratch thinking, investigation logs, a running scratchpad** →
  [docs/notes/](docs/notes/README.md). Not a contract; date your notes.
- **A change to a behavioral contract** → edit the spec appendix (it's the living
  spec) *and* note the change in an ADR. Never let code and spec diverge silently.
- **Real transcript/rollout fixtures** → `tests/fixtures/` (once tests exist),
  shaped from spec §11. Sanitize every captured value.
- **A code-review handoff** → follow the [code-review relay](docs/code-review-relay.md);
  the baton is a file in [docs/reviews/](docs/reviews/README.md).

## Code review relay (Claude ⇄ Codex)

This repo has a **defined cross-agent review process**: Claude authors, Codex
reviews independently, findings come back, Claude resolves. The full protocol,
roles, and reviewer checklist are in
[docs/code-review-relay.md](docs/code-review-relay.md) — that file is the single
source of truth; this section and the Claude `code-review-relay` skill are pointers.

- **If you are Codex, you are the Reviewer.** When the user points you at a review
  file under `docs/reviews/`: read the brief, then **run the diff and review the
  actual code**, verifying the brief's claims rather than trusting them. Assess
  against the checklist in the protocol doc (correctness · spec adherence — a
  `MUST` violation is a **blocker** · tests · security · simplicity · provenance).
  Append findings (severity · `file:line` · issue · suggested direction) and end
  with a verdict (`approve` | `changes-requested`). **Do not fix** — that's the
  Author's job.
- **If you are Claude, you are the Author** — the `code-review-relay` skill
  (`.claude/skills/code-review-relay/`) drives your half.
- **Keep Author and Reviewer as separate sessions.** The independent perspective is
  the entire point of the relay.

## Conventions

- **Commits:** imperative, scoped ("curator: enforce unconsumed-on-parse-failure").
  Reference the phase or decision when relevant.
- **Slugs** (projects, facts, nodes) match `^[a-z0-9-]+$` — enforced in code (spec §1).
- **Markdown docs:** wrap prose ~80 cols to stay git-diff-friendly, matching the
  existing docs.
- **Secrets never land in the repo or the raw store.** Redaction runs before any
  `raw/` write (spec §10 table); the store is gitignored by default.

---

*If something here conflicts with the spec appendix, the spec wins — and fix this
file.*
