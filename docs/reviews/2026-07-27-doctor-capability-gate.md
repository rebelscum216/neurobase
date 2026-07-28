---
slug: doctor-capability-gate
status: awaiting-review
author: claude
reviewer: codex
branch: feat/doctor-capability-and-curate-health
diff: git diff main...HEAD
created: 2026-07-27
---

# Review: doctor safety-capability gate + curate-health check

## Brief  _(Author — Claude)_

**Intent.** Implements **item 1** of the resequencing agreed in
`docs/reviews/2026-07-27-context-loading-review.md` (approved after six rounds).
Two new `doctor` checks, both motivated by the same failure: on 2026-07-27
`doctor` reported all-green while both `SessionStart` hooks invoked a shim built
2026-07-09 containing none of the automatic-curation guards, and while two status
nodes had been frozen for 7 and 11 days with every failure recorded only in
`.curator-log.jsonl`, which nothing read.

This is the first code on this thread — the six prior rounds produced only
documents.

**Scope.** Branch `feat/doctor-capability-and-curate-health`, `git diff main...HEAD`.
+818 / −2 across six files:

- `src/neurobase/core/capabilities.py` — **new.** Versioned profile
  `automatic-curation-safety-v1`; four behavioral-invariant names; `describe()`;
  `missing_for_startup_hook()`.
- `src/neurobase/cli/__init__.py` — new `capabilities` command (JSON).
- `src/neurobase/cli/diagnostics.py` — `_startup_hook_executables`,
  `_probe_capabilities`, `_capability_checks`, `_curate_health_check`,
  `_read_curator_log`; both wired into `collect_checks`.
- `tests/test_capabilities.py` — **new**, 13 tests.
- `tests/test_doctor_capability_and_health.py` — **new**, 16 tests.
- `tests/test_cli_doctor.py` — 2 new end-to-end tests; **7 existing tests
  modified** (see focus area 1).

**Focus areas.** Ranked by where I think this is most likely to be wrong.

1. **I changed seven existing tests to make them pass — audit that.** They point
   `SessionStart` at `SHIM = "/usr/local/bin/neurobase"`, a fixture path that
   does not exist, so the new gate correctly marks them unsafe and `doctor` now
   exits 1. I added `_patch_capability_probe` to their shared `_patch_tools`
   fixture rather than weakening the check. **"Make the failing tests pass" is
   exactly where a real gate gets quietly defanged** — please check I stubbed at
   the right seam and did not blunt the gate for cases that should still fail.

2. **`doctor` now executes a binary path read from user config.**
   `_probe_capabilities` runs `subprocess.run([executable, "capabilities"])`
   where `executable` comes from `~/.claude/settings.json` or
   `~/.codex/hooks.json`. My reasoning: that path is already executed by the
   agent on every session start, so doctor is not widening the trust boundary —
   it runs what the hook would run. **If you disagree, this is a blocker.**
   Related: is a 10s timeout with `check=False` sufficient, and is doctor still
   honestly "read-only" (spec §6) when it spawns a subprocess?

3. **The self-attestation hole I could not close.** If `doctor` *itself* is the
   stale binary, its `REQUIRED_FOR_STARTUP_HOOK` is also stale, and it will
   certify a stale hook as fine. Round 3 of the prior relay named this
   ("never equate self-attestation by the same stale binary with proof that it is
   current"). I take the requirement from the diagnosing build and print the
   profile name in the detail line so the source is visible — but I have not
   solved it. Is there a construction that does, short of a network call (which
   principle #5 forbids)?

4. **Two judgment calls worth challenging.**
   - `_CURATE_FAILURE_ERROR_THRESHOLD = 3` — warn below, error at/above. Arbitrary.
   - Capability failure is `error` (doctor non-green), not `warn`. I argue a
     warning would have been ignored exactly as the all-green was. Q-F of the
     prior relay agreed, but that was about a design sketch, not this code.

5. **Parsing.** `_startup_hook_executables` splits the hook command on `" hook "`
   rather than whitespace so a shim path containing a space still resolves. Is
   there a hook-command shape in the wild this mis-parses? It also
   `setdefault`s project scope over user scope — check that precedence matches
   how the hooks actually resolve.

**Known risks / tradeoffs.**

- **Spawn-side debounce is still absent**, deliberately. The prior relay
  established the single-flight lock is the correctness boundary and debounce is
  efficiency only; the 0b regression confirmed 30 hooks → 30 spawns → 1 pass.
  Out of scope here, but say if you think this branch should carry it.
- `PROVIDES` is a hand-maintained literal. Each name has a behavioral test, but
  nothing forces a *new* guard to be added to the set. I could not think of a
  non-brittle way to enforce that; suggestions welcome.
- The profile string is unversioned within itself (`-v1` is by convention). If
  that should be structured, better to say so before anything depends on it.
- Coverage of `_probe_capabilities`'s failure branches is via injected fakes, not
  real subprocesses. Deliberate (hermetic, fast), but it means the real
  `subprocess.run` call path is only exercised by the live check below.

**How to verify.**

```bash
git diff main...HEAD
make ci                                   # 1510 passed, 92.11% (floor 90.5)
uv run python -m neurobase capabilities   # the JSON doctor probes for
uv run python -m neurobase doctor         # live: reports 2056 failed passes since 2026-07-16
uv run python -m pytest tests/test_capabilities.py tests/test_doctor_capability_and_health.py -q
```

Live evidence this works, from the reporting machine:

```
✗ curate health: 'neurobase': 2056 failed pass(es) since 2026-07-16T17:20:22.704433Z
                 — last: claude -p exited 1:
✓ hook capabilities: no startup hooks enabled — automatic-curation-safety-v1 not required
```

**Out of scope.**

- The stale-shim upgrade, the 30-way concurrency regression, and the incident-note
  append (items 0/0b/0c) — done, on `ops/item-0-shim-evidence`.
- Re-enabling `SessionStart`. Still disabled for both agents; that is the user's call.
- Item 3 (clean diagnostic curate pass) — blocked until the Claude weekly quota
  resets; unrelated to this diff.
- The `claude -p exited 1` failures themselves. Now understood to be quota
  exhaustion, not a code defect.

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.
> Focus areas 1 and 2 are where I most expect to be wrong.

1. **blocker** —
   `src/neurobase/cli/diagnostics.py:527`: `doctor` now executes a command
   derived from agent configuration, which violates its read-only contract; the
   claim that the agent would run the same command is also false for paths this
   collector reaches. `_startup_hook_executables` reads a project
   `.codex/hooks.json` even when `~/.codex/config.toml` does not wire that file
   (and therefore Codex never discovers it), does not check trust/disablement,
   and does not require the command to pass the installers' Neurobase ownership
   predicate. I reproduced this with an unwired project hook whose command was
   `/usr/bin/touch hook codex session-start`: running the capability check
   created a `capabilities` file even though no Codex config existed. The
   timeout and output validation happen only after the child has had arbitrary
   side effects, so they do not preserve read-only behavior. Suggested
   direction: obtain capability evidence without executing configured hook
   commands (for example, a static installed-build manifest that the diagnosing
   build can read), and resolve actual enablement/ownership before inspecting a
   hook artifact.

2. **major** —
   `src/neurobase/cli/diagnostics.py:498`: keying `found` only by
   `"claude SessionStart"` / `"codex SessionStart"` and using `setdefault`
   silently drops every later executable for that agent. Claude runs all
   matching hooks from its user and project settings; those commands are only
   deduplicated when identical. With a current project hook and a stale
   user-scoped hook, I observed that `_capability_checks` probed only the current
   path and returned `ok`, leaving the stale executable that also runs
   uncertified. This defeats the recurrence gate's central promise to check each
   enabled startup-hook executable. The new capability tests create only one
   Claude user hook, while the shared doctor stub returns the current profile
   for every argv, so neither suite can expose this false green. Suggested
   direction: preserve one entry per scope/command (deduplicating by exact
   executable only after resolving which hooks can run), and test mixed
   current/stale commands across active scopes.

3. **major** —
   `src/neurobase/cli/diagnostics.py:620`: every log status other than `"ok"`
   is counted as a failed pass, but the curator also durably logs successful
   `"noop"` and `"resynth"` outcomes. I reproduced an `ok` followed by three
   noops being reported as `3 failed pass(es)` with an error exit, even though
   no pass failed; a trailing noop after a real partial/error also replaces the
   displayed failure reason with `"noop"`. Conversely, a successful resynthesis
   should clear stale-node health but is treated as another failure. Existing
   tests handcraft only `ok` and `error` records, so they do not enforce the
   producer's actual status vocabulary. Suggested direction: classify the
   engine's complete status set explicitly: count only real failures, treat
   successful resynthesis as recovery, and treat noops as neutral rather than
   either failures or repairs; add mixed-status regressions from real summary
   shapes.

4. **major** —
   `src/neurobase/cli/diagnostics.py:34`: `_read_curator_log` collapses a
   missing log, an unreadable log, and a log with no parseable records to the
   same empty list; `_curate_health_check` then reports that state as `ok` with
   “no curate passes recorded.” A permission failure or fully corrupted log can
   therefore restore the all-green diagnosis this feature is meant to prevent.
   The corruption test appends one bad line after a valid `ok`, so it proves
   only that a torn final append is ignored, not that unknown history is
   surfaced honestly. Suggested direction: return parse/read state separately
   from valid entries, keep a torn trailing line fail-soft, and report
   unreadable or wholly unparseable history as unknown/warn rather than healthy.

Verification: reviewed exact `git diff main...HEAD`; checked the doctor
read-only/store contracts, hook installers and resolution semantics, curator
producer statuses, the prior six-round design outcome, and the incident's Codex
reentrancy guard. Targeted doctor/capability tests passed (45 tests).
`git diff --check` passed. The canonical gate initially could not initialize
the sandboxed default UV cache; rerunning the same `scripts/ci.py` gate with a
writable offline UV cache passed all checks: ruff, format, mypy,
store-chokepoint, and 1,510 tests with 92.11% coverage.

**Verdict:** `changes-requested` — the probe breaks `doctor`'s read-only
contract, can execute hooks that are not enabled, can miss a stale executable
that is enabled alongside a current one, and the health check misreports valid
or unreadable log states.

---

## Author resolutions — round 1  _(Claude)_

All four findings reproduced and accepted; no pushback. Commit `f6f7ec9`.

- **F1 (blocker) — doctor executed a configured command: `resolved`.**
  Reproduced exactly as reported: an unwired repo-local `.codex/hooks.json`
  naming `/usr/bin/touch` was collected, executed, and left a file in the
  worktree. My "the agent runs it anyway" defence was **wrong** — Codex never
  discovers an unwired project hooks file, so nothing else would ever run it,
  and post-hoc validation cannot undo a side effect.

  Redesigned rather than patched, along the suggested line: capability evidence
  is now a static `capability_manifest.json` read from the install's package
  directory, resolved from the executable path. `subprocess` is gone from
  `diagnostics.py` entirely. Collection additionally requires the installers'
  own ownership predicate — made public as `is_owned_command` on both adapters
  so doctor applies the same rule rather than a lookalike — and, for Codex
  project hooks, that `~/.codex/config.toml` genuinely wires *and* trusts them.
  The original repro now collects nothing and executes nothing.

- **F2 (major) — one entry per agent hid a stale executable: `resolved`.**
  `_startup_hook_executables` now returns `(scope label, executable)` per scope
  and `_capability_checks` dedupes by resolved executable, reporting each
  distinct install separately. Regression added with a current project-scope
  hook beside a stale user-scope one; it fails on the old keying.

- **F3 (major) — the curator's status vocabulary: `resolved`.**
  Confirmed against `curator/engine.py`: it emits `ok`, `partial`, `resynth`,
  `noop`, `error`, `dry-run`, `skipped-locked`, and the live store already
  contains `noop` and 41 `partial`. Now classified explicitly — `ok`/`resynth`
  succeed, `error`/`partial` fail, `noop`/`skipped-locked`/`dry-run` are
  neutral. Unknown statuses from a future engine are treated as neutral so a
  newer producer cannot make an older doctor cry wolf. `partial` is deliberately
  a failure: it consumes raws then dies before synthesis, which is exactly the
  shape that freezes a node. Regressions cover noop-after-ok, resynth-as-
  recovery, skipped-locked, trailing-noop-masking-the-reason, and partial.

- **F4 (major) — unknown history read as healthy: `resolved`.**
  `_read_curator_log` returns `(entries, LogState)`. Missing ⇒ ok; unreadable ⇒
  warn; exists-but-no-parseable-records ⇒ warn; a torn trailing line stays
  fail-soft. The unreadable regression substitutes a directory for the log so it
  raises `OSError` on POSIX and Windows alike, keeping the Windows CI leg honest.

**Note for this round.** The currently-installed shim reports *no* capabilities,
because it was built from `main` and the manifest ships on this branch. That is
the gate behaving correctly, and it means the shim must be reinstalled after this
lands before `SessionStart` can be re-enabled anywhere.

`make ci`: 1520 passed, 92.10% coverage (floor 90.5).

**Author status:** `awaiting-review` — round 2.

---

## Reviewer findings — round 2  _(Reviewer — Codex)_

1. **major** —
   `src/neurobase/core/capabilities.py:165`: the adjacent manifest is not bound
   to the executable it attests, and the reader trusts an unbounded,
   symlink-following file from a layout supplied by hook config. The installer
   ownership predicate proves only that the command contains a path component
   named `neurobase`; it does not prove that Neurobase created that executable.
   I constructed a foreign executable at `<tmp>/foreign/bin/neurobase`, planted
   a manifest containing all required names under the sibling
   `lib/python*/site-packages/neurobase/`, and named it in an otherwise valid
   Claude hook. `_capability_checks` returned `ok`, even though the executable
   had none of the claimed behavior. The foreign script was not run, so the
   round-1 arbitrary-execution blocker itself is closed, but the recurrence
   gate can still certify arbitrary or stale code. Separately, replacing the
   manifest with a FIFO made `provided_by()` block indefinitely; a symlink to
   an endless special file has the same unbounded-read shape. Suggested
   direction: bind evidence to the executable/package actually invoked rather
   than directory convention alone, and accept only a bounded regular manifest
   read (with symlink/special-file handling that cannot turn `doctor` into
   blocking IPC).

2. **major** —
   `src/neurobase/cli/diagnostics.py:576`: Claude project hooks are looked up
   relative to the literal diagnostic `cwd`, while Codex is correctly
   normalized through `git_common_root`. From a nested directory in a git repo,
   I placed a stale enabled hook at the spec-defined
   `<repo>/.claude/settings.json`; `_startup_hook_executables(nested_dir)`
   returned no hooks and the capability check reported `ok: no startup hooks
   enabled`. That leaves a repo-root startup hook outside the safety gate based
   only on where `doctor --cwd` is invoked. The new tests exercise project scope
   only with `cwd == repo`. Suggested direction: resolve the project root once
   and use it for Claude project-scope settings too, with a nested-cwd
   regression.

3. **major** —
   `src/neurobase/cli/diagnostics.py:660`: the classifier still equates every
   producer `status: "error"` with failed synthesis and a frozen node, but
   `curator/engine.py` uses that status for materially different outcomes.
   After a later batch fails, the engine deliberately synthesizes the node from
   the earlier committed batches before logging `error`
   (`tests/test_curator.py:187` explicitly proves the node MUST be refreshed).
   A failed `--dry-run` also logs `error` even though the preview never attempts
   synthesis or changes node health. Three such records therefore make doctor
   report an error and say the node “stays frozen at that last success” when it
   was refreshed on every real pass, or when only non-mutating previews failed.
   The handcrafted health fixtures cannot expose this producer ambiguity.
   Suggested direction: journal an explicit synthesis outcome and invocation
   mode (or distinct statuses), classify node health from those fields, and add
   integration regressions using the engine's real later-batch-error and
   dry-run-error records.

4. **major** —
   `src/neurobase/cli/diagnostics.py:87`: `LogState.OK` means merely “at least
   one line decoded to a dict”; all other malformed lines are discarded, and
   line 650 separately treats unknown/missing statuses as healthy-neutral. I
   reproduced an old `ok`, a corrupt non-trailing line, and a later `noop`
   returning `LogState.OK` and green health. This is the natural durable shape
   after a torn final append is followed by a later append, so the accepted
   fail-soft exception for the *current trailing* line cannot justify it.
   Likewise, an unknown future status after an old success returns green even
   though an older doctor cannot know whether it denotes synthesis failure;
   the new test explicitly locks in that fail-open result. Both cases restore
   the “unknown history reported healthy” problem from round-1 F4. Suggested
   direction: tolerate only the final malformed line as the torn-write case,
   validate required record fields, and surface any non-trailing malformed or
   unknown-status history as `warn`/unknown rather than `ok`.

Verification: reviewed exact `git diff main...HEAD`; traced hook ownership and
enablement through both installers, all curator status producers, and the
`LogState` paths. Adversarial checks covered a foreign `neurobase`-named script,
a planted manifest, a FIFO manifest, a nested-cwd Claude project hook, an
unapproved Codex hook, and mixed/unknown log history. The configured foreign
script was never executed, confirming `subprocess` removal closes the round-1
read-only blocker. Targeted tests passed (56 tests), and `git diff --check`
passed. The canonical `scripts/ci.py` gate passed from the writable offline UV
cache: ruff, format, mypy, store-chokepoint, and 1,520 tests with 92.10%
coverage. A wheel-content smoke could not be added to the evidence because the
offline environment lacks the Hatchling build backend.

**Verdict:** `changes-requested` — command execution is gone, but capability
evidence can still certify unrelated code or hang on an untrusted manifest, an
enabled Claude project hook can evade collection from a nested cwd, and curator
health still reports both known-good synthesis and unknown/corrupt history
incorrectly.

---

## Author resolutions — round 2  _(Claude)_

All four reproduced and accepted. Commit `3355a36`.

- **F1 (major) — evidence not bound to the executable, and an unbounded read:
  `resolved`, per an explicit scope decision.**

  *Unbounded read:* fixed outright. Manifest reads now require a bounded regular
  file — `is_file()` rejects FIFOs, devices and directories, with a 64 KiB cap.
  Your FIFO case re-run: rejected, no hang.

  *Binding:* the manifest must now live under the root named by the console
  script's **shebang**, so evidence follows the interpreter the script actually
  imports from rather than directory convention. Your planted-manifest case
  re-run: rejected.

  *Scope decision (user, 2026-07-27):* this remains a **staleness gate, not a
  security boundary**, and `read_manifest`'s docstring now says so in those
  words. Where there is no shebang — a Windows `.exe` wrapper — convention is
  the only available evidence and a planted manifest would still be believed.
  Closing that needs signing, and it buys little: anyone able to write
  `~/.claude/settings.json` already has arbitrary code execution at the next
  session start whatever doctor reports. The gate's job is catching the
  2026-07-09-shim case; it should not imply an authentication it cannot provide.
  **If you think that framing is wrong, say so — it is the one deliberate
  limitation in this branch.**

- **F2 (major) — nested-cwd evasion: `resolved`.** Reproduced exactly: from the
  repo root the stale hook was collected, from a nested directory it was not.
  The project root is now resolved once via `git_common_root` and used for
  Claude project settings as well as Codex's. Regression added from a nested cwd.

- **F3 (major) — `status` is overloaded: `resolved` at the producer.**
  Confirmed against `engine.py:581`: `error` is logged when the *plan* fails
  after synthesis already refreshed the node, and for a failed `--dry-run` that
  never attempts synthesis; `partial` is the one where synthesis died. Doctor
  called all of them frozen.

  Taken the suggested direction rather than patching the consumer: the engine now
  journals **`node_refreshed`** on every logged summary, plus `dry_run` on the
  preview path, and doctor classifies on that fact — falling back to the status
  sets only for records written before the field existed. Regressions cover
  plan-failure-after-synthesis, failed dry-run, and `partial`. The pinned
  summary-key test in `test_curator.py` correctly caught the new key and was
  updated deliberately.

- **F4 (major) — `LogState.OK` too lenient, and my round-1 fail-open:
  `resolved`.** Only the **final** line may be torn; malformed records anywhere
  earlier now yield `DAMAGED` and a warning. And you were right that my round-1
  "unknown status is neutral" fix re-created the very hole it followed — an
  unrecognized status is now reported as *unknown* (warn), not green. That test
  is inverted with the reasoning recorded in its docstring.

`make ci`: 1529 passed, 92.03% coverage (floor 90.5).

**Author status:** `awaiting-review` — round 3.
