---
slug: test-distill-coverage-gaps
status: awaiting-review
author: claude
reviewer: codex
branch: test-distill-coverage-gaps
diff: git diff main...HEAD
created: 2026-07-16
---

# Review: distill render/redaction coverage gaps

## Brief  _(Author — Claude)_

**Intent.** Close four coverage gaps in the Tier-2 transcript distill
(`curator/distill.py`), all in the transcript **render** path. That path is a
redaction surface, but it was previously exercised only through a fake brain that
returns a scripted digest and *ignores* the rendered `user` text — so most render
branches were never asserted. These are **test-only** additions; no source change.

**Scope.** Branch `test-distill-coverage-gaps`, `git diff main...HEAD`. Key files:
- `tests/test_distill.py` — four new tests (+162 lines), plus two `list[dict]`
  annotations to keep mypy from widening heterogeneous event literals to
  `list[object]`.

The four tests, mapping to the gaps:
1. `test_secret_in_tool_use_command_never_reaches_the_brain` — a planted
   env-assignment secret in an assistant `tool_use` **command** field is scrubbed
   via `redact_command` (a *different* redactor than the `tool_result` path) before
   the render reaches the distiller. Drives `_tool_use_line`
   (`distill.py:137-151`).
2. `test_tool_result_block_list_is_joined_and_scrubbed` — a `tool_result` whose
   `content` is a *list* of blocks (text blocks + a bare string) is joined by
   `_result_text` and scrubbed as one value. Drives `distill.py:126-134`.
3. `test_oversize_transcript_drops_middle_chunks_and_marks_it` — via injected
   `chunk_chars=50`, a ~1000-char render splits past `MAX_DISTILL_CHUNKS`, dropping
   the middle chunks (head+tail kept) and prefixing the digest with a visible
   `middle chunk(s) dropped for size` marker. Drives `_chunk` + the `dropped` path
   (`distill.py:201-213`, `340`).
4. `test_summary_and_sidechain_events_are_rendered` — compact-summary events and
   subagent sidechain turns both reach the render (`[compact summary]` line +
   `(subagent)` markers). Drives `distill.py:164-166`.

**Focus areas.**
- Are the redaction assertions **real**, not self-satisfying? I probed
  `redact_command` first and confirmed it scrubs the env-assignment form
  (`API_TOKEN=… → [REDACTED:env-secret]`) but deliberately leaves a `Bearer` token
  inside a `curl -H` flag alone (the lexical-not-semantic boundary, held over a
  12-round relay). So the command test asserts the case that genuinely redacts and
  does **not** assert the Bearer case — please confirm that framing is honest and
  the test would actually fail if the command-field scrub were removed.
- Test #3 asserts the drop *marker*, not head/tail identity. Is that a meaningful
  enough guard, or should it also assert the head and tail chunks survive?

**Known risks / tradeoffs.**
- All four route through the real `distill_docs`/`_distill_one` with the existing
  `DistillBrain` fake (scripted `_GOOD_DIGEST`), so they assert redaction/render
  behavior, not model output. Intentional — the distill is networkless by design.
- #3 uses the injectable `chunk_chars` param to force the middle-drop with a small
  fixture instead of a multi-hundred-KB transcript.

**How to verify.**
```
git diff main...HEAD
uv run pytest tests/test_distill.py -q                       # 21 passed
uv run --with pytest-cov pytest tests/test_distill.py \
  --cov=neurobase.curator.distill --cov-report=term-missing  # distill.py 92%
make ci                                                      # full gate, 805 passed
```

**Out of scope.**
- The remaining uncovered distill lines (unparseable-JSONL skip, `_blocks`
  fallthrough, the `file_path/pattern/query/url` alternates in `_tool_use_line`) —
  defensive branches, deliberately not chased here.
- The broader redact.py / mcp/server.py coverage gaps identified in the same audit
  but not addressed on this branch.
- Any change to `curator/distill.py` source — this branch is tests only.

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

_(none yet)_

**Verdict:** _pending._
