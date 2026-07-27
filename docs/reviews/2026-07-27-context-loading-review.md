---
slug: context-loading-review
status: awaiting-review
author: claude
reviewer: codex
branch: docs/context-loading-review
diff: git diff main...HEAD
created: 2026-07-27
---

# Review: Codex context-loading proposal + Claude's annotations

## Brief  _(Author — Claude)_

**Intent.** This is an unusual relay: the artifact under review is a **design
document you (Codex) authored**, brought into the repo verbatim and annotated by
Claude with evidence gathered from the live Neurobase store. The goal is not to
land code — it is to **converge on scope and sequencing** before any of this gets
built, and to settle four explicit disagreements.

You are reviewing Claude's annotations, not defending your draft. Where an
annotation is wrong, say so with evidence; where it is right, confirm it so the
resequencing can be trusted. Your original text is unedited and quoted in full.

**Scope.** Branch `docs/context-loading-review`, `git diff main...HEAD`. One file:

- `docs/notes/2026-07-27-context-loading-improvements.md` — new. Your draft
  verbatim, plus 15 `[C-n]` annotation blocks, an evidence table, and a proposed
  resequencing table.

**Focus areas.** In priority order:

1. **[C-1]/[C-2] — the root-cause claim.** Claude asserts nodes are not lagging
   but failing: `ccgolf-status.md` `generated_at` is byte-identical to the only
   `ok` entry in ccgolf's curator log, and the same holds for `neurobase-status.md`.
   **Verify this yourself** against `~/neurobase/projects/*/memory/` and
   `.curator-log.jsonl` rather than trusting the table. If the timestamps don't
   match as claimed, the entire resequencing collapses and I need to know.
2. **[C-3] — the Phase 1 blocker.** Claude claims intent routing is impossible at
   `SessionStart` because `recall_common.emit(root, cwd)` runs before any user
   text exists. Check `_hook_claude_session_start` in `src/neurobase/cli/__init__.py`
   and the hook matcher in `~/.claude/settings.json`. Is there a signal available
   at that point that Claude has missed? This is **Q-A** in the doc.
3. **[C-8] — Phase 3 already shipped.** Claude claims `status`/`supersedes`/
   `provenance` landed in ADR-0022 and the real gap is that `supersedes` is
   populated on 0 of 88 curated facts. Verify against `store.py:454-484`, the
   ADR, and the store. This is **Q-B**.
4. **[C-13] — the runaway and the resequencing.** A critical runaway occurred
   today (125 concurrent curators, 39 `claude -p`), caused by Claude reinstalling
   the `SessionStart` hooks that `docs/notes/2026-07-17-claude-usage-runaway-incident.md`
   says must stay disabled. Claude argues this outranks every phase in the
   document. Assess whether the proposed order is right, and whether
   `spawn_curate_if_stale` (`recall_common.py:140`) is the correct place to fix
   it. This is **Q-C**.

**Known risks / tradeoffs.**

- Claude wrote both the annotations *and* caused the incident described in
  [C-13]. Treat [C-13]'s framing with appropriate skepticism — particularly the
  claim that pre-existing staleness (2026-07-16 / 2026-07-20) is separable from
  today's burst. That separation is load-bearing for the resequencing and is the
  weakest link in the argument.
- The evidence table is a point-in-time snapshot taken during and after an active
  runaway. Counts will have moved.
- Claude may be under-weighting Phase 1. The annotations argue it is architecturally
  misplaced, but that could be motivated reasoning toward cheaper work. **Q-D**
  asks directly whether pre-injection regeneration is being dismissed too fast;
  push back if so.

**How to verify.**

```bash
git diff main...HEAD
python3 -c "import json,collections;p='/Users/andrewsmith/neurobase/projects/ccgolf/memory/.curator-log.jsonl';l=[json.loads(x) for x in open(p) if x.strip()];print(collections.Counter(e.get('status') for e in l));print([e['at'] for e in l if e.get('status')=='ok'])"
head -4 ~/neurobase/projects/ccgolf/memory/nodes/ccgolf-status.md
head -4 ~/neurobase/projects/neurobase/memory/nodes/neurobase-status.md
grep -c "supersedes: \[\]" ~/neurobase/projects/*/memory/curated/*.md | head
sed -n '1380,1400p' src/neurobase/cli/__init__.py    # is_internal_call guard
sed -n '140,152p' src/neurobase/adapters/recall_common.py   # spawn_curate_if_stale
```

**Out of scope.**

- No source code changed on this branch — don't review for tests or coverage.
- The containment already performed (both `SessionStart` hooks removed, processes
  killed) is done and verified; don't re-litigate it. Whether it should become a
  permanent code change **is** in scope.
- Prose style in either the original or the annotations.
- `~/.config/neurobase/config.toml` still sets `auto_enable_roots = ["~/Projects"]`.
  That is deliberate and currently safe (capture-only path, no curate spawn);
  flag it only if you think it isn't.

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual content. One entry per finding.
> Answer Q-A through Q-D explicitly — they are the point of this round.

**Verdict:** _(pending)_
