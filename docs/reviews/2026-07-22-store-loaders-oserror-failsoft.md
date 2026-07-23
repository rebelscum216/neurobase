---
slug: store-loaders-oserror-failsoft
status: approved
author: claude
reviewer: codex
branch: store-loaders-oserror-failsoft
diff: git diff main...HEAD
created: 2026-07-22
---

# Review: make read_doc skip-paths fail soft on unreadable entries (§13)

## Brief  _(Author — Claude)_

**Intent.** Close the repo-wide latent §13 fail-soft gap that the step-4a review
(F1) surfaced but scoped out. `read_doc` reads bytes (`read_text`), so an
**unreadable** entry — a directory named `*.md`, a permission error — raises
`OSError`/`IsADirectoryError`. `read_doc` normalizes `YAMLError → ValueError` (its
docstring says so, so "every caller's `except ValueError` skip-path" works) but it
does **not** normalize `OSError`. So the pervasive `except ValueError` skip idiom
lets a hostile/corrupt tree escape as an unhandled exception.

**Where it bites (all reproduced with a directory named `*.md`):**

- **MCP `memory_list_projects`** → `handle.list_curated(project)` → the ToolError
  §13 forbids. This is a **live spec §13 MUST violation** (the same class 4a fixed
  for `memory_search`, in a different tool).
- **The SessionStart recall hook** → `recall_common` node loop. Hooks MUST fail
  safe; an unreadable node would crash recall injection.
- **`rebuild_index`** (runs after every curate) — its node loop had **no skip at
  all**, its curated loop caught only `ValueError`.
- Lower-stakes degrade points: `status`' active-fact count, `seed`'s existing-state
  read, `distill`'s cache read.

**Fix (surgical, not a `read_doc` contract change).** Broaden the skip-path catches
to `except (ValueError, OSError)` at each enumeration/degrade site. I deliberately
did **not** normalize `OSError → ValueError` inside `read_doc`: that primitive has
~20 callers, and several are direct reads that *should* surface a real I/O error
(`mark_consumed`, `upsert_curated`, `soft_delete_curated`); swallowing every I/O
error as "malformed document" everywhere is a footgun. The surgical fix matches the
`(ValueError, OSError)` pattern the newer code already uses (the MCP direct reads at
`server.py:132/215/285`, the proposal loaders, `ranker`).

**Scope.** Branch `store-loaders-oserror-failsoft`, `git diff main...HEAD`
(implementation commit `d12fe0b`). Sites broadened:

- `core/store.py` — `list_raw`, `list_curated`, `prune_tombstones`, and **both**
  loops of `rebuild_index` (the node loop gains a skip it never had). Docstrings
  updated to say "unparseable *or* unreadable."
- `adapters/recall_common.py` — the node read (hook fail-safe).
- `cli/__init__.py` — `status`' active-fact count.
- `recommender/seed.py` — `_existing_seed_state` (degrades to "no state").
- `curator/distill.py` — `_cache_read` (degrades to a cache miss).
- Tests: `test_store.py` (list_raw / list_curated / rebuild_index), `test_mcp_server.py`
  (list_projects §13), `test_recall_common.py` (hook fail-safe).

**Focus areas.**

1. **The §13 contract.** Confirm `memory_list_projects` is now fail-soft on an
   unreadable entry (the added MCP test), and that no other MCP tool still reaches
   `read_doc`/`list_*` through an OSError-blind catch. (I believe the MCP direct
   reads already caught `(ValueError, OSError)` — please verify I didn't miss one.)
2. **No over-catching.** `OSError` is caught only at enumeration/graceful-degrade
   sites, never around a direct read that should propagate. Confirm I didn't
   accidentally silence a write path or a must-surface read. Note the one behavior
   change: `rebuild_index`'s node loop now *skips* a bad node instead of raising —
   confirm that's the right call (it matches its own curated loop).
3. **Idiom completeness.** I broadened every `read_doc` skip-path I found with a
   narrow `except ValueError`. Confirm none remain (grep: a `read_doc` assignment
   followed by a bare `except ValueError`), and that the ones I left as
   `except ValueError` are genuinely non-`read_doc` (e.g. `datetime.fromisoformat`
   in `prune_tombstones`).

**Known risks / tradeoffs.**

- `rebuild_index` node-loop now skips unreadable/malformed nodes (previously raised).
  Deliberate — one corrupt node must not crash the index rebuild every curate runs.
- This does not touch `read_doc` itself, so a *direct* `read_doc` on a hostile path
  still raises for callers that want that (correct).

**How to verify.**

- `git diff main...HEAD`
- `uv run pytest tests/test_store.py tests/test_mcp_server.py tests/test_recall_common.py -q`
- `uv run python scripts/ci.py` — full gate green: ruff, format, mypy,
  `1147 passed, 1 skipped`; coverage ~91.8%.

**Out of scope.** The ADR-0015 handle migration (this only changes exception
handling, not signatures) — 4b (`distill`/`locks` onto the handle) and 4c (remove
raw-`Path` sigs) still follow. Not changing `read_doc`'s own contract.

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

<!-- Reviewer appends findings + verdict here. -->

No findings. I verified every changed catch against its caller: list/status/
recall/index/cache paths now skip only malformed or filesystem-unreadable
documents, while direct mutation reads (`mark_consumed`, `upsert_curated`,
`soft_delete_curated`) still propagate I/O failures. The remaining bare
`except ValueError` sites are not OSError-blind `read_doc` skip paths.

The §13 MCP path is covered end to end: `memory_list_projects` now returns the
healthy project count when a sibling curated entry raises `IsADirectoryError`,
and the other MCP document reads already handle `(ValueError, OSError)`. The
recall and index regressions likewise prove that one unreadable node/fact does
not discard healthy siblings or crash the operation.

Verification run:

- `git diff main...HEAD`
- Focused store/MCP/recall suite — passed
- Distill/seed/CLI suites for the other changed degradation sites — passed
- `uv run python scripts/ci.py` — ruff, format check, mypy, and pytest passed;
  `1147 passed, 1 skipped`, total coverage `91.81%`

**Verdict:** approve — the change closes the reviewed fail-soft gaps without
weakening direct-read or mutation error handling.
