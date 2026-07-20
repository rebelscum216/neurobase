---
slug: runaway-containment
status: awaiting-review
author: claude
reviewer: codex
branch: runaway-guard-split
diff: git diff ae025ad...5e6fefa
created: 2026-07-20
---

# Review: layer-one containment for the 2026-07-17 Claude usage runaway

## Brief  _(Author — Claude)_

**Intent.** Close the feedback loop that exhausted a real five-hour Claude usage
window on 2026-07-17. Three independent guards, in the layering the incident note
prescribes (P0 #1–#3), plus the post-incident report itself.

This is **packet 1 of 2**. The pass budget that bounds a *healthy* pass is a
separate review (`2026-07-20-curate-pass-budget.md`, diff `5e6fefa...a708034`)
and should be read after this one, since it builds on these guards. Reviewing
them together would have meant ~1700 lines in one packet.

**Severity context.** This is a critical-path security fix, not a feature. The
failure it addresses consumed an entire usage window, filled the store with
self-generated junk, and continued after the interactive request had stopped.
Please weight the review accordingly — I would rather have a false finding to
push back on than a missed one.

**Scope.** Branch `runaway-guard-split`, base `origin/main` (`66c9805`),
`git diff ae025ad...5e6fefa`. Four commits, deliberately ordered and each
independently green:

| Commit | Layer |
|---|---|
| `dc888fb` | docs: the post-incident report (543 lines) |
| `9b99651` | fix: internal-call marker — stops recursion |
| `eacd76f` | fix: single-flight lock — stops concurrency |
| `5e6fefa` | fix: distill breaker — stops per-raw retry of a systemic failure |

Key files: `core/process_guard.py` (new), `core/locks.py` (new),
`curator/distill.py`, `cli/__init__.py` (two separate concerns — the `run_hook`
fast path in `9b99651`, the curate lock in `eacd76f`), `brain/{claude_cli,
codex_cli,select}.py`, and their tests.

**Provenance you should know.** This code was written on 2026-07-17 but sat
**uncommitted in a working tree for three days** before being recovered and
committed on 2026-07-20. It has never been reviewed. The original WIP commit
also swept in two unrelated test files, now separated as `cbe11c3` and `ae025ad`
(out of scope below). Treat this as first-look code, not as something that has
already had eyes on it.

**Focus areas.** In order of how much I want a second pair of eyes:

1. **Does the marker actually close both halves of the loop?**
   `process_guard.internal_call_env()` copies `os.environ` and sets
   `NEUROBASE_INTERNAL_CALL=1`; `run_hook` returns early on `is_internal_call()`.
   The claim is that this stops an internal `SessionStart` spawning a curator
   *and* an internal `SessionEnd` writing the brain's own prompt back as a raw.
   Please check the early return is genuinely before **every** side effect —
   stdin read, capture, recall injection, and curate spawn — and that both CLI
   backends pass the marked env on **every** subprocess they launch, including
   backend version detection (that is also an agent invocation and was easy to
   miss).
2. **Is the lock the right primitive, in the right place, released correctly?**
   It is non-blocking, kernel-backed, scoped by store+project, taken before the
   staleness check and before brain resolution. A loser must exit 0 having
   resolved no brain. I would like scrutiny on: crash/abrupt-kill release,
   whether the Windows path is genuinely equivalent to the POSIX one (CI covers
   both, but the lock is the piece most likely to differ), and whether taking it
   inside `curate` rather than the hook spawner leaves any entry point uncovered.
3. **Does the breaker cut only what it should?**
   The first systemic `BrainError` stops further distill brain calls for the
   pass; the failed raw and all remaining raws fall back to deterministic skims.
   Document-local failures (missing transcript, unsupported renderer, malformed
   single transcript, invalid digest shape) must still degrade *only that raw*.
   Please check I have not accidentally converted a document-local failure into
   a pass-wide stop, or vice versa.
4. **D16 and D9 are both still honoured?** Distill must never abort a pass
   (D16); a plan parse failure must still leave every raw unconsumed (D9). The
   breaker changes distill's control flow, so it is the most likely place to
   have disturbed one of those.

**Known risks / tradeoffs.**

- **The marker's core assumption is unproven and this is the biggest risk in
  the packet.** It assumes Claude and Codex propagate the parent environment to
  their hook subprocesses. If either does not, the guard silently does nothing —
  it fails *open*, and it would look exactly like it was working. The incident
  note lists a live disposable-store spike as an outstanding acceptance
  criterion for exactly this reason, and **Claude `SessionStart` stays disabled
  until that spike passes**. I would welcome a view on whether an env marker is
  the right mechanism at all versus a supported per-invocation "disable hooks"
  flag, if the current CLIs offer one.
- **The lock is advisory in the sense that it only guards callers who take it.**
  Anything that invokes the curator engine directly, bypassing the `curate`
  command, is unprotected. I believe the CLI is the only such path today; please
  challenge that if you see another.
- **20-process contention test is the strongest lock evidence I have**, and it
  is still a same-machine test. It does not prove behaviour on a network
  filesystem, which I have not tested and do not claim.
- **The incident note is a report, not a contract.** It says so in its own
  opening. It records candidate fixes and open questions; it does not bind the
  implementation. Reviewing it for accuracy is welcome, but it is not a spec.
- **I corrected a factual error in the note** while splitting: it claimed the
  mitigation had landed on branch `fix-curator-runaway-guard-lock`, which was
  never true — no commit on that branch ever contained `core/locks.py`. The
  replacement deliberately points at commits and paths via a `git log --grep`
  command rather than a branch name, since a branch pointer is exactly what went
  stale. Worth a sanity check that the documented command returns the three
  `fix:` commits.

**How to verify.**

```bash
git diff ae025ad...5e6fefa
uv run python scripts/ci.py          # full gate: 1077 passed, 1 skipped, 91.14%

# every commit in this packet is independently green, which is worth spot-checking:
for c in dc888fb 9b99651 eacd76f 5e6fefa; do git checkout -q $c && uv run pytest tests/ -q; done

# the note's own lookup command should return exactly the three fix commits:
git log --oneline --grep='^fix: ' -- src/neurobase/core/locks.py \
    src/neurobase/core/process_guard.py src/neurobase/curator/distill.py
```

**Out of scope.**

- **The pass budget** — packet 2, `2026-07-20-curate-pass-budget.md`.
- **`cbe11c3` and `ae025ad`** (backup-manifest and ANSI-C decoder tests). These
  were swept into the original WIP commit and are unrelated to the incident;
  separated so this packet contains only containment. Happy to hand them over as
  a third small packet if you want them looked at.
- **Re-enabling Claude `SessionStart`.** Explicitly not proposed here.
- **The remaining P1 items** in the note: `doctor`/`status` observability, and
  quarantining the existing self-generated raws. The note is explicit that
  cleanup must come *after* the feedback path is fixed.
- **G1 / the D11 store-schema guard**, still deferred to its own ADR.

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

**Verdict:** _(pending)_
