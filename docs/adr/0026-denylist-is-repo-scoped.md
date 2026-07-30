# ADR-0026: `denylist` matching is repo-scoped — an entry inside a repo is not a carve-out

- **Status:** Proposed
- **Date:** 2026-07-30
- **Resolves:** independent-review findings I3/I5/I7 on
  [`docs/reviews/2026-07-23-folder-scoped-auto-enable.md`](../reviews/2026-07-23-folder-scoped-auto-enable.md)
- **Supersedes:** **[ADR-0019](0019-folder-scoped-auto-enable.md), denylist
  granularity only.** D39's `"subtrees carved back out"` framing and D40's
  "a sensitive subtree can be carved out" both imply that naming a directory
  *inside* a repository carves that subtree out of it. It does not — such an entry
  does not gate the repo containing it (though it does gate repos nested beneath
  it). Everything else in ADR-0019 —
  folder-scoped consent, D40's git-repo scoping, D41's fail-closed seam, D42's
  trigger ordering — stands unchanged.

## Context

ADR-0019 shipped in PR #5 on 2026-07-23. The independent Codex review it was owed
did not run until **2026-07-30**, seven days after merge (four rounds of author
*self*-review ran in the interim and converged to approve). That late pass found
the spec still described pre-ADR-0019 behavior (I1), and — after the spec was
folded in — that the new spec text **still misdescribed the denylist** (I3), as
did ADR-0019's own D39 example (I5).

The implementation is unambiguous.
[`projects.is_denylisted`](../../src/neurobase/core/projects.py) does:

```python
candidate = (git_common_root(cwd) or cwd.resolve()).resolve()
return any(_is_within(candidate, deny) for deny in _resolved_config_dirs(denylist))
```

The cwd is collapsed to its **git root** before comparison, so the compared
candidate is *always* the repository root. With repo `/work/app` and
`denylist = ["/work/app/private"]`, a hook firing in `/work/app/private` compares
`/work/app` — which is not under the entry — and capture proceeds normally.

Why it survived four self-review rounds plus a full suite: **every pre-existing
denylist test names a directory that *contains* the repo** (`client-work/` holding
`secret-app/`). No test distinguished repo-root scoping from cwd/subtree scoping,
so both policies passed identically.

The consent stakes make the wording material rather than cosmetic. `denylist` is
the *opt-out* half of a consent mechanism whose opt-in half is "name a folder and
everything under it is captured." A user who reads "subtrees carved back out",
puts a sensitive inner directory in the list, and gets no feedback has been told
they revoked consent when they did not.

## Decision

**Denylist matching is repo-scoped, and the documentation moves to say so.**

1. **The rule, stated exactly: an entry gates a repo *iff* that repo's root is at
   or beneath the entry.** The compared candidate is always the repository root,
   never the cwd, so everything follows from this one sentence with no special
   cases.

   The consequence that surprises: an entry *inside* repo `C` never gates `C`,
   because `C`'s root sits **above** it. It does **not** follow that such an entry
   is inert — a repo nested *beneath* the entry has its root beneath it and **is**
   gated. Review I8 caught an earlier draft of this ADR (and of the doctor check)
   asserting the blanket "matches nothing", which is false exactly at a nested-repo
   boundary: with repo `/work/mono`, entry `/work/mono/packages`, and a nested repo
   `/work/mono/packages/plugin`, `is_denylisted` returns **True** for the plugin.
2. **This is deliberate, not a defect to fix.** A capture is attributed to the
   repo's project regardless of which subdirectory the session ran in, so a
   subtree entry could not deliver the protection its path implies — it would be
   partial at best, and misleading at worst. Consent is granted per repo
   (`enable`) and per folder (`auto_enable_roots`); it is revoked at the same
   granularity.
3. **But silently failing to gate the repo a user pointed inside is not
   acceptable** (review I7). `doctor` reports any `denylist` entry resolving
   inside a git repository, stating that the **containing repo is not gated** and
   naming its root as the remedy. `doctor` is the right surface: it is already the
   read-only reporting path, and a hook cannot warn — its stdout is protocol
   output (`hookSpecificOutput`), not a user channel. The report is scoped to the
   claim that holds in every case (the containing repo is not gated) rather than
   the blanket no-op claim I8 disproved.
4. **The permissive outcome is pinned by test**
   (`test_denylist_entry_inside_a_repo_does_not_gate_it`), asserting that a
   denylisted inner directory still auto-enables while denylisting the repo root
   gates it. Pinning the *limitation* means any future move to cwd/subtree scoping
   fails a test and drags spec §10, this ADR and the config comment along with it,
   rather than silently contradicting three documents.

Behavior of `is_denylisted` is **unchanged** by this ADR. What changes is the
documentation, plus the new `doctor` visibility.

## Consequences

- **Spec §10** states the root-at-or-beneath rule, its consequence that an entry
  inside a repo does not gate *that* repo, the nested-repo qualification, and the
  rationale — it is now the normative home of the denylist contract.
- **ADR-0019 is annotated, not retired** (the ADR-0002 → ADR-0025 precedent). Its
  D39 example comment and D40 carve-out sentence remain as authored, because an
  Accepted ADR is immutable ([ADR README](README.md)); this ADR is where the
  correction lives. Review I6 flagged that the correction had initially been made
  by editing ADR-0019 in place *after* its status flipped to `Accepted` — a
  lifecycle violation, reverted.
- **`doctor` gains a check** and therefore a new way to be noisy: a user with a
  deliberate inner-repo entry sees a warning every run. Judged correct — the entry
  does not gate the repo containing it, which is what a reader of the path would
  expect it to do, so the noise is proportionate to the surprise. (It may still
  gate repos nested beneath it; the warning says so rather than calling it inert.)
- **Not addressed here:** making inner-repo entries actually work (cwd/subtree
  scoping). That is a behavior change with live-consent implications and belongs
  in its own ADR if anyone wants it; the test above ensures it cannot happen by
  accident.
- **Process note worth keeping.** The seven-day gap between merging ADR-0019 and
  running its independent review is what allowed a mis-stated consent contract to
  ship and then be *re-stated wrongly* while being fixed. The relay's value here
  came entirely from the reviewer being independent — four self-review rounds
  reproduced the author's blind spot each time.
