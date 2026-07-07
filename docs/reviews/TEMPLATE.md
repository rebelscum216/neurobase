---
slug: <short-kebab-slug>
status: draft            # draft | awaiting-review | changes-requested | approved
author: claude
reviewer: codex
branch: <feature-branch>
diff: git diff main...HEAD
created: <YYYY-MM-DD>
---

# Review: <one-line title>

## Brief  _(Author — Claude)_

**Intent.** What this change does and why.

**Scope.** Branch `<feature-branch>`, `<diff command>`. Key files:
- `path/to/file` — what changed.

**Focus areas.** Where you most want the reviewer's eyes.

**Known risks / tradeoffs.** Anything you're unsure about or deliberately chose.

**How to verify.** Commands or steps to exercise the change.

**Out of scope.** What this review should *not* flag.

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

### F1 — <title>
- **severity:** blocker | major | minor | nit
- **location:** `file:line`
- **issue:** what's wrong.
- **suggested direction:** how to address it (not a patch).
- **resolution:** _(Author fills)_ resolved | wontfix | deferred — note.

### F2 — <title>
- …

**Verdict:** approve | changes-requested — _one-line rationale._
