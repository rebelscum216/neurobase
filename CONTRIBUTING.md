# Contributing

Thanks for considering it. This file is a short pointer, not a rulebook — the
real operating guide is **[AGENTS.md](AGENTS.md)**, written for both human and
agent contributors. Read that first; it covers what the project is, the
non-negotiable build principles (spec-is-law, fail-safe hooks, consent-first
config changes, zero telemetry), and the repo layout.

## Before you open a PR

1. **Read the canonical docs, in order:** the phased
   [build plan](docs/neurobase-build-plan.md), the authoritative
   [behavioral spec](docs/neurobase-spec-appendix.md), and, if a decision
   needs its rationale, the
   [architecture options](docs/neurobase-architecture-options.md) writeup.
   Any behavior described with `MUST` in the spec is a contract; changing it
   needs an [ADR](docs/adr/README.md), not just a code change.
2. **Run the full local gate before pushing** — not just the tests:
   ```bash
   make ci                       # ruff check + ruff format --check + mypy + pytest (w/ coverage floor)
   # or, without make (e.g. on Windows):
   uv run python scripts/ci.py
   ```
   `scripts/ci.py` is the single source of truth for those four checks; CI
   runs the identical script across the OS/Python matrix, so local and CI
   can't drift. Opt into the committed pre-push hook once per clone so a red
   gate can't reach `git push` by accident:
   ```bash
   git config core.hooksPath .githooks
   ```
3. **For anything non-trivial, use the review relay.** This project reviews
   real changes through a defined Claude ⇄ Codex handoff — see
   [docs/code-review-relay.md](docs/code-review-relay.md) for the protocol
   and [docs/reviews/](docs/reviews/README.md) for the trail of past
   reviews. If you're an external contributor without that tooling, a normal
   GitHub PR review is fine — just expect scrutiny at the same bar the
   relay holds the maintainer to.

## Filing issues

Use the issue templates (bug report / feature request) — they ask for the
information that's actually needed to act on a report. Before filing, check
[docs/known-gaps.md](docs/known-gaps.md) (known defects already tracked) and
the build-plan's
[backlog](docs/neurobase-build-plan.md#backlog-post-010-in-rough-order)
(known future work) — your issue may already be recorded there with more
context than a fresh report would have.

## Reporting a security issue

Don't open a public issue — see [SECURITY.md](SECURITY.md).

## Scope discipline

This is a solo-maintained project with an explicit anti-scope-creep stance
(see the build plan's risk register). A PR that's tightly scoped to a real
spec gap or a documented known-gap will move faster than one that bundles in
unrelated refactoring or new abstractions "while I was in there."
