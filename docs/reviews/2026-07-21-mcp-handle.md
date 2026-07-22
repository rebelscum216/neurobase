---
slug: mcp-handle
status: approved
author: codex
reviewer: claude
branch: codex/adr-0015-mcp-handle
diff: git diff main...HEAD
created: 2026-07-21
---

# Review: convert MCP server to the store handle — ADR-0015 step 3.4

## Brief  _(Author — Codex)_

**Intent.** Continue ADR-0015 step 3 by putting the Phase-7 MCP server behind
the validated `StoreHandle`, including D24's special boundary: incompatible
`store.toml` metadata must produce structured tool errors without preventing
server startup or breaking the `resources/list` probe.

**Scope.** Branch `codex/adr-0015-mcp-handle`, `git diff main...HEAD` (implementation
commit `3337101`). Key files:

- `src/neurobase/mcp/server.py` — opens one READ handle in `build_server`, captures
  `UnsupportedSchemaError`, routes direct store/registry access through the handle,
  and returns an MCP-native structured refusal from all five tools.
- `tests/test_mcp_server.py` — covers too-new and unreadable store metadata,
  startup/resources survival, and the structured error from every tool.
- `tests/test_mcp_stdio.py` — proves the D24 result survives the real stdio
  protocol as `isError: true` plus `structuredContent`.
- `docs/neurobase-spec-appendix.md` — folds D24's exact MCP contract into spec §13.

**Focus areas.**

1. **D24 startup behavior.** `build_server` catches only
   `store.UnsupportedSchemaError`, captures the message, and constructs the full
   tool surface. With resources enabled it registers zero node resources; the
   stdio client still initializes and receives `resources: []`.
2. **Structured error shape.** `_unsupported_schema_result` returns
   `CallToolResult(isError=True)` with a common top-level
   `error.code = "unsupported_store_schema"`. List-valued tools also carry
   `result: []`: FastMCP 1.28.1 validates an existing `{"result": [...]}` output
   schema even for an explicit error result. Confirm this preserves the normal
   tool schemas and gives every client one stable `error` location.
3. **One captured verdict.** All tools branch on the handle/error captured at
   construction rather than reopening the store. This follows ADR-0015 D24, but
   it means a server process does not notice a later `store.toml` change until the
   MCP client reconnects.
4. **Registry behavior stays separate.** `_safe_registry` and current-project
   resolution now take the handle but keep their broad fail-soft wrappers. A bad
   `registry.toml` still yields empty search/project results and does not become an
   `unsupported_store_schema` error.
5. **Write through the captured READ handle.** `memory_remember` uses
   `handle.ensure_tree` / `handle.upsert_curated` because D24 says the server opens
   one READ handle and captures its outcome. Per-method mode enforcement is
   explicitly deferred by ADR-0015; flag this if the intended boundary instead
   requires a second WRITE open at call time despite D24's one-open wording.

**Known risks / tradeoffs.**

- `_unsupported_schema_result` is annotated `Any` because FastMCP rejects tool
  return annotations that union `CallToolResult` with normal success types. The
  helper's concrete value is always a `CallToolResult`, and both in-process and
  stdio tests assert it.
- `search.search(handle.root, ...)` and `recall_common.build_context(handle.root,
  ...)` still receive the validated root because `core/search.py` is a separate
  step-3 conversion and recall owns its own public entry boundary. Generic
  `store.read_doc(path)` remains a format primitive, matching the prior curator
  review's explicit boundary.
- The schema verdict is process-lifetime state by design. Replacing or migrating
  `store.toml` while an MCP server is already connected requires reconnecting.

**How to verify.**

- `git diff main...HEAD`
- `uv run pytest tests/test_mcp_server.py tests/test_mcp_stdio.py -q` — 40 passed.
- `uv run python scripts/ci.py` — full gate green: ruff, format, mypy, and
  `1131 passed, 1 skipped`; combined coverage 91.58%.

**Out of scope.** `core/search.py`, recommender, CLI/doctor D26, distill, and
linkify conversions; removing root-taking APIs (step 4); the AST enforcement
check (step 5); deferred per-method mode enforcement; changing Phase-7 tool
success payloads or recommender proposal formats.

---

## Reviewer findings  _(Reviewer — Claude)_

> Run the diff and review the actual code. One entry per finding.

**Attribution note.** The baton opened as `reviewer: codex` on a Codex-authored
change (`3337101`) — a self-review, against the relay's independence rule. This
review was done independently by Claude; frontmatter corrected to
`reviewer: claude`. At the maintainer's direction Claude also applied the fixes
(commit `1215bed`) — role-inverted from the usual Author-resolves flow, recorded
here for transparency.

I read `git diff main...HEAD`, the `open_store`/`StoreHandle` implementation,
the `store.py` write path, and the two cross-boundary callees, and independently
re-ran `tests/test_mcp_server.py tests/test_mcp_stdio.py` (40 passed pre-fix).
**No correctness bugs.** D24 holds: every corrupt-store case (too-new schema,
unreadable/invalid TOML, missing/non-int `schema`) funnels into
`UnsupportedSchemaError`, which `build_server` catches (and *only* that — the
catch is neither too broad nor too narrow); startup survives, `resources/list`
returns `[]`, and all five tools return the structured refusal. `registry.toml`
corruption correctly stays fail-soft. `search.search(handle.root, …)` and
`recall_common.build_context(handle.root, …)` both still take a root `Path` and
open/guard their own handle, so passing `handle.root` is correct. Three
non-blocking findings, all resolved in `1215bed`:

### F1 — `memory_remember` writes through a READ-mode handle
- **severity:** minor (design / latent-coupling; not a live defect)
- **location:** `src/neurobase/mcp/server.py:249` (`handle.ensure_tree` /
  `handle.upsert_curated`)
- **issue:** the write tool uses the READ handle captured at startup. It is safe
  today only because `ensure_tree → ensure_store_metadata` (`store.py:118`)
  independently re-creates `store.toml` and re-runs the schema guard, so a write
  never lands unguarded and an uninitialized store still gets initialized. But it
  is coupled to per-method mode enforcement being deferred (ADR-0015): once a
  WRITE method may not run on a READ handle, this tool breaks.
- **suggested direction:** either document the deliberate READ-handle write, or
  have `memory_remember` open its own `WRITE` handle at call time
  (`open_store(handle.root, StoreMode.WRITE)`) — the cleaner long-term boundary,
  though it deviates from D24's "one READ handle" wording, so it's a maintainer
  call.
- **resolution:** _resolved_ (`1215bed`) — applied the ADR-faithful option: an
  explanatory comment naming the safety argument and the exact latent break.
  WRITE-open at call time left as a noted follow-up for the maintainer to decide
  against D24; not taken unilaterally inside a change under review.

### F2 — repeated `str | None → str` coercion / magic string
- **severity:** nit
- **location:** `src/neurobase/mcp/server.py` — the five
  `_unsupported_schema_result(schema_error or "unsupported store", …)` call sites
- **issue:** the `or "unsupported store"` is load-bearing (mypy can't link
  `handle is None` to `schema_error is not None`, so it narrows `str | None` to
  `str`), but it is repeated five times and reads as a reachable fallback when it
  isn't.
- **suggested direction:** accept `message: str | None` in the helper and default
  once inside it.
- **resolution:** _resolved_ (`1215bed`) — helper now takes `str | None` and
  defaults internally; the five coercions and the duplicated string are gone.

### F3 — test coverage gaps
- **severity:** nit
- **location:** `tests/test_mcp_server.py`
- **issue:** the every-tool structured-error test only exercised the too-new
  schema; the unreadable/invalid-TOML path (a distinct `UnsupportedSchemaError`)
  was asserted only for startup/`resources/list` survival, never at the tool
  level. The recall prompt's `handle is None` branch (`server.py:297`) had no
  direct test.
- **suggested direction:** add both cases.
- **resolution:** _resolved_ (`1215bed`) —
  `test_unreadable_store_metadata_also_returns_structured_error` and
  `test_recall_prompt_reports_unsupported_store_without_erroring` added; suite
  now 42 passed, mypy + ruff green.

**Verdict:** approve — clean, behavior-preserving handle conversion; the three
findings were nits/design and are resolved in `1215bed`. One open follow-up for
the maintainer: whether `memory_remember` should switch to a WRITE-open at call
time (deviates from D24) or keep the documented READ-handle write.
