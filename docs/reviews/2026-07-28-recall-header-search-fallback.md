---
slug: recall-header-search-fallback
status: changes-requested
author: claude
reviewer: codex
branch: feat/recall-header-search-fallback
diff: git diff main...HEAD
created: 2026-07-28
---

# Review: instruct search fallback when a fact seems absent from the injected node

## Brief  _(Author — Claude)_

**Intent.** Close the cheapest item from the context-loading reconciliation:
`docs/notes/2026-07-27-context-loading-improvements.md` Phase 5, annotation
**C-11**. The synthesized status node injected at `SessionStart` is a *summary*.
A reading agent that treats it as exhaustive answers "we don't have that in
memory" for a fact that is sitting in curated storage, merely un-synthesized —
which is the failure the whole context-loading thread was opened over. C-11's
argument is that this needs almost no code: the `memory_search` MCP tool already
exists, so the entire mechanism is one clause of instruction text in `HEADER`.
Phase 5's steps 2–4 are *agent behavior*, not Neurobase code, and are
deliberately not implemented here.

**Scope.** Branch `feat/recall-header-search-fallback`, `git diff main...HEAD`
(single commit `f76d6fd`, one commit off `main`@`a20a888`). Key files:

- `src/neurobase/adapters/recall_common.py` — `HEADER` gains one sentence:
  the node is a summary not the whole store; search with `memory_search`
  "(when available)" before concluding a fact is not in memory.
- `docs/neurobase-spec-appendix.md` §3 — the spec quotes `HEADER` **verbatim**
  as the "proven, reuse the spirit" wording, so the quote is updated in step
  with the code, plus a bullet recording what the new clause is *for* and that
  the header counts against the `<content>` cap.
- `docs/how-it-works.md` — same verbatim quote, plus the rationale paragraph.
- `tests/test_claude_recall.py` — new
  `test_header_instructs_search_fallback_when_a_fact_seems_absent`.

**Focus areas.**

1. **Is the wording right for an untrusted-context header?** This header's
   existing job is *defensive*: it frames injected memory as background data,
   explicitly "not as instructions", because a naively-worded injection can be
   read by the model as a directive. I have now added an **imperative** clause to
   that same header. I believe it is safe because it instructs the agent about
   *Neurobase's own tooling* rather than about the untrusted node content, and it
   sits before the `_JOINER` fence — but it is a real tension with the stated
   purpose of the paragraph it lives in, and it is the thing I most want a second
   opinion on.
2. **"(when available)".** Injection runs from the `SessionStart` hook, which is
   independent of whether the reading agent has the Neurobase MCP server wired
   (spec §3 vs §7). An unconditional "call `memory_search`" would instruct agents
   to call a tool that does not exist for them. Is the hedge correctly placed and
   correctly worded, and is it *tested* in the right way?
3. **Spec/doc truthfulness.** Both docs quote `HEADER` verbatim; I updated both.
   Please check I did not miss a third quotation site or a paraphrase that is now
   stale. I grepped for `recalled project memory`, `background context that may
   be stale`, and `Full facts live`, which found
   `docs/adr/0005-codex-injection-confirmed.md` and `src/neurobase/mcp/server.py`
   — I judged **both out of date-scope** (see Out of scope) and would like that
   judgment checked rather than trusted.

**Known risks / tradeoffs.**

- **Budget.** `_assemble` counts the header against the same cap as the node
  bodies (`recall_common.py:57-72`), so this change costs ~130 chars of node
  budget out of a 6000-char default. I judged that acceptable and recorded the
  fact in the spec bullet rather than raising the cap. Pushing back on this is
  legitimate.
- **Pre-existing, not introduced:** if `[inject].max_chars` were configured
  below the header's own length, `_assemble` returns the header unbounded (the
  cap is only applied on the first *body*). This change makes the header longer
  and so widens that window slightly. I did **not** fix it here — flagging it so
  the decision is explicit rather than silent.
- **Unfalsifiable-by-construction.** There is no code path behind this clause; a
  model either heeds it or does not. The test can only pin that the text is
  present and shaped correctly. That is the honest ceiling of C-11's "instruction
  text, not a subsystem" framing, not a gap in the test.

**How to verify.**

```
cd /Users/andrewsmith/Projects/nb-header-fallback
uv run python scripts/ci.py
uv run pytest tests/test_claude_recall.py -q
```

Full gate was green at `f76d6fd`: `1556 passed, 1 skipped`, coverage 92.21%
(floor 90.5%). The new test was **stash-verified**: reverting only
`recall_common.py` and re-running it fails on the
`"summary, not the whole store"` assertion, then passes again once restored.

**Out of scope.**

- `src/neurobase/mcp/server.py`'s `_INSTRUCTIONS` — the MCP *server* description,
  a different surface with a different audience (a client that by definition
  already has the tools). Not touched.
- `docs/adr/0005-codex-injection-confirmed.md` — an **accepted ADR recording a
  historical live verification**. Editing the header text quoted inside it would
  falsify the record of what was actually verified in that experiment.
- Phase 5 steps 2–4 (search the loaded node → curated facts → cross-project, and
  reporting which layer answered). Agent behavior; explicitly not code.
- Phase 2/4 items (stale-node detection, material-fact supplement) and the
  ADR-0025 injection-point decision — separate PICKUP items.
- The `Status: Proposed` staleness on ADRs 0019/0020/0022/0023/0024 — tracked
  hygiene, unrelated to this diff.

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

### F1 — blocker — `docs/neurobase-spec-appendix.md:523`

The new standalone `MUST NOT` constrains the reading model's conclusion, but the
change can only emit instruction text; it has no mechanism that can guarantee
the model follows that instruction. The brief explicitly acknowledges that the
model may or may not heed it, and
`test_header_instructs_search_fallback_when_a_fact_seems_absent` only asserts
three substrings. It therefore cannot fail when the newly specified behavior
fails. That leaves a new §3 `MUST` without an enforcing test, contrary to
AGENTS.md principle 1 ("Every `MUST` ... gets a test"), which makes this a
blocking contract violation even though the intended prompt text is present.
Suggested direction: make the normative subject the behavior Neurobase controls
(for example, the header MUST convey the fallback instruction), and keep
"absence is never treated as proof" as rationale or an evaluation target rather
than a deterministic software contract; then pin the complete directive in the
shared recall tests.

### F2 — major — `src/neurobase/adapters/recall_common.py:31`

The 200-character header expansion causes supported small
`[inject].max_chars` configurations to stop injecting memory at all. There is no
minimum or validation on `InjectConfig.max_chars`, and the existing regression
suite deliberately exercises `cap=300`. With the short memory path `/tmp/n`,
the header grows from 252 to 452 characters; calling `_assemble` with two
250-character nodes and `cap=300` produced 41 characters of the first node on
`main`, but on this branch produced zero node characters and only the first 300
characters of an incomplete header. `emit()` still sends that non-empty fragment
as `additionalContext`, so the result contains neither a status node nor the
complete trusted framing/search instruction. This is materially more than the
brief's claimed "~130 chars of node budget" tradeoff and conflicts with §3's
`<content> = header + node bodies` / "Inject nodes" contract for an accepted
configuration. The green suite misses it because
`test_small_configured_cap_is_honored` checks only `len(ctx) <= 300` and absence
of the trailing node, never that the first node or complete header survives.
Suggested direction: define and enforce a minimum viable cap or otherwise make
assembly fail safe while preserving a complete trusted header plus useful node
content, and add a configured-cap regression that asserts both framing integrity
and node presence (including a realistic variable-length memory path).

### F3 — minor — `docs/how-it-works.md:1065`

The documentation now calls the entire expanded header the exact wording that
is "proven," but ADR-0005 only live-verified the delivery mechanism and the old
defensive framing; the new retrieval-fallback sentence has no behavioral
verification. The brief itself accurately calls the mechanism
"unfalsifiable-by-construction," so labeling the augmented wording as proven
overstates the evidence. Suggested direction: scope the historical "proven"
claim to the defensive framing/delivery that ADR-0005 actually exercised, and
describe the new closing sentence as an unevaluated retrieval instruction.

Verification run:

- `git diff main...HEAD`, repository protocol, §3, shared Claude/Codex recall
  callers, MCP tool registration, and all matching header quotations
- `uv run pytest tests/test_claude_recall.py tests/test_recall_common.py -q` —
  passed (`14 passed`)
- `uv run python scripts/ci.py` — ruff, format check, mypy, store-chokepoint,
  and pytest passed; `1556 passed, 1 skipped`, total coverage `92.21%`
- Direct `_assemble` reproduction at `cap=300` — `main` header: 41 first-node
  characters and a complete header; branch header: zero node characters and a
  truncated header

**Verdict:** changes-requested — the wording is present and the gate is green,
but the new §3 `MUST` is not enforceable as written and the longer header
regresses valid small-cap recall into a header-only fragment.
