# 2026-07-17 - Claude usage runaway from recursive Neurobase hooks

What this is: a post-incident report on the Claude five-hour usage window being
exhausted almost immediately after starting work in the Neurobase repository.
It records the evidence available on 2026-07-17, the causal analysis, current
containment, and candidate fixes. This note is not yet a behavioral contract.

## Executive summary

The available evidence identifies Neurobase as the primary cause of the sudden
Claude usage exhaustion. The user's coverage request was large, but the
interactive session had only begun repository discovery when the limit appeared.
At the same time, Neurobase was launching many background curation processes,
and those processes were invoking the user's Claude subscription through
`claude -p` with large distill and curator prompts.

The incident was a positive feedback loop:

1. The user-scoped Claude `SessionStart` hook launched a detached
   `neurobase curate --if-stale` process.
2. `brain.backend = "auto"` selected `claude-cli`, so curation launched
   `claude -p` for transcript distillation and planning.
3. The user-scoped hooks also applied to those headless Claude sessions. Their
   startup could launch more detached curators, while their teardown captured
   Neurobase's own prompts as new raw sessions.
4. Neurobase had no per-project curation lock, spawn debounce, reentrancy guard,
   or LLM-call budget. Concurrent curators therefore processed the same backlog.
5. Distill treated each failed brain call as a per-document fallback and
   continued to the next raw. A systemic quota failure could consequently cause
   another Claude invocation for every raw in the backlog.
6. Failed planning left the backlog unconsumed. The stale predicate therefore
   remained true, allowing later startup hooks to repeat the cycle.

This was not merely an expensive prompt. It was an unsafe interaction between
global agent hooks, a subscription-backed CLI brain, an unbounded distill pass,
and an incorrect assumption that opportunistic curation would be effectively
serialized.

Severity: **critical** for users who combine user-scoped hooks, an enabled
project, stale raws, and a Claude CLI brain. The failure can consume an entire
usage window, create a large amount of junk memory input, and continue after the
interactive request has stopped.

Confidence assessment:

- **Confirmed:** Claude `SessionStart` unconditionally spawns detached curation;
  auto backend selection chose `claude-cli`; curation invoked headless
  `claude -p`; internal distill/plan prompts were captured as Claude raws; many
  concurrent curators were observed; the curator log recorded a rapid failure
  burst; removing Claude `SessionStart` stopped the observed respawning.
- **High-confidence inference:** Neurobase's headless Claude sessions were
  re-entering the user-scoped `SessionStart` hook. This matches the live
  respawning behavior and its containment, but the process tree was not saved
  before the processes were killed.
- **Unknown:** provider-side token counts, which exact subprocess crossed the
  limit, and whether a contemporaneous Claude service/model availability issue
  changed the amount charged or merely accelerated the failures.

## User-visible impact

- Claude reported that the five-hour usage limit had been reached within seconds
  of the user's request.
- The requested coverage audit never meaningfully started. The captured
  transcript shows root listing and source/test enumeration, followed by a
  session-limit response when Claude attempted its next command.
- Background activity continued independently of the interactive request.
- The local raw store was heavily polluted with Neurobase distill/curator prompts
  and session-limit responses.
- The incident consumed subscription capacity. Neurobase cannot restore that
  capacity; recovery depends on the normal provider reset or provider support.

No incident-related source-code edit was observed; the repository already had
two unrelated modified test files before the investigation. This investigation
did not audit provider traffic or every captured prompt, so it makes no finding
about whether sensitive text was transmitted beyond Neurobase's intended,
redacted brain calls. The known state changes were confined to Claude usage,
local processes, Claude hook config, the local Neurobase store, and the curator
log. The raw-store pollution still needs a separate, cautious cleanup; it must
not be deleted wholesale because legitimate sessions are interleaved with the
noise.

## Containment performed

On 2026-07-17:

- Runaway `neurobase curate --if-stale` and headless `claude -p` processes were
  stopped.
- Only Neurobase's Claude `SessionStart` hook was removed from
  `~/.claude/settings.json`.
- A pre-change backup was retained as
  `~/.claude/settings.json.neurobase-runaway-20260717.bak`.
- The Claude `SessionEnd` capture hook was intentionally left installed.
- A delayed process check found no remaining matching curator or headless
  distill process.
- A later check while preparing this report again found no matching process.

Containment status: **the observed Claude-triggered runaway startup loop is
stopped, and Claude `SessionStart` must remain disabled until the code is
hardened.**

Containment is not a complete fix. With `SessionEnd` still enabled, a manually
started curation that uses `claude-cli` can still capture Neurobase's own
headless Claude prompts into `raw/`. That can pollute the store and leave more
work for a later pass, although it no longer recursively starts curators via the
disabled Claude `SessionStart` hook. Avoid manual `curate` or `recommend run`
with a subscription CLI brain against this contaminated store until an internal
call guard and a call budget exist.

A user-scoped Codex `SessionStart` hook is also present on this machine. Its
effective trust/config state has previously produced diagnostics warnings, and
no matching runaway process was present during the report check, so this report
does not claim that it fired in this incident. If it does fire, it calls the same
unlocked `spawn_curate_if_stale()` path and can select `claude-cli`; disabling
only Claude `SessionStart` does not make that separate entry point safe. Until
the code is fixed, avoid relying on any automatic startup curation against the
contaminated backlog.

## Evidence

### Live process evidence

During investigation, the process table contained a large group of detached,
orphaned processes with the command shape:

```text
neurobase curate --if-stale --root <store> --cwd <repo>
```

It also contained a child command with the shape:

```text
claude -p "You compress ONE AI coding-agent session transcript ..."
```

Killing only the curators did not contain the incident; matching processes
reappeared. Removing the Claude `SessionStart` hook, killing both command groups,
and checking after a delay did contain it. This is direct evidence that the
background curation path, rather than the interactive coverage command, was
actively spending Claude usage.

The exact process snapshot was observed live but was not saved to a file before
containment. The on-disk evidence below independently corroborates it.

### Configuration and implementation path

`neurobase doctor` resolved the active brain as `claude-cli` because the Claude
CLI was present. The relevant code path is:

- `src/neurobase/cli/__init__.py:1081-1089` emits recall context and then calls
  `spawn_curate_if_stale()` for every matching Claude startup.
- `src/neurobase/adapters/recall_common.py:112-122` launches a detached curator
  with null stdio and no lock, debounce, or internal-call marker.
- `src/neurobase/cli/__init__.py:201-226` checks staleness, resolves the brain,
  and starts the full curate pass; it has no single-flight guard.
- `src/neurobase/brain/select.py:21-22,102-110` tries `claude-cli` first in
  automatic mode.
- `src/neurobase/brain/claude_cli.py:53-61` constructs the headless
  `claude -p <prompt> --output-format json --max-turns 1` command.
- `src/neurobase/curator/engine.py:272-315` distills every unconsumed raw before
  planning, and `src/neurobase/curator/distill.py:326-347` catches a failed
  per-raw brain call as fallback.
- `docs/neurobase-build-plan.md:324-331` records the now-disproved assumption
  that curator runs are effectively serialized and do not need locking.

### Curator log

The project curator log contained 1,268 records at the evidence snapshot. In the
interval `2026-07-16T15:14:00Z` through `15:17:00Z`, it recorded **129 curation
errors**, all ending in `claude -p exited 1`. The first and last errors were about
105 seconds apart, with multiple records in some individual seconds. The raw
count reported by those passes rose from 875 to approximately 1,000 during the
burst.

On 2026-07-17, the log recorded a pass failing with 179 raws and
`claude -p exited 1`. A curator writes one log record only when its pass returns;
therefore the log does not count processes killed while blocked inside distill
or a brain call. The observed process pile was larger than the single completed
failure represented in that day's log.

### Raw-store feedback

At the evidence snapshot, the project had 1,649 raw files. Of those:

- 1,317 contained a Claude session-limit response.
- 175 contained the exact Tier-2 prompt prefix
  `You compress ONE AI coding-agent session transcript`.
- 162 raws were timestamped between `2026-07-17T13:18Z` and `13:34Z` alone.

The first 26 of that day's raws arrived within roughly 18 seconds. Samples are
not normal engineering sessions: their user prompt is Neurobase's own distill or
curator system prompt, and their assistant result is commonly a session-limit
message or an empty response. The final raw in that interval contains the
curator plan prompt and the session-limit response.

These counts are a point-in-time diagnostic, not stable product metrics. They
may change as the local store is curated or cleaned.

### Triggering interactive transcript

The user asked Claude for a full repository coverage audit. Claude listed the
repository, read the test configuration, enumerated source and test files, and
then received a session-limit result before it could run coverage. This supports
two conclusions:

- The request was broad and would have been expensive if completed.
- It does not explain an entire five-hour window disappearing before substantive
  work began; the concurrent background `claude -p` calls do.

The exact provider-side token accounting is not available locally, so this
report does not claim how many tokens each process consumed or which individual
call crossed the limit.

The transcript also contains a message that the selected Claude model was
temporarily unavailable when Claude tried to classify a shell command. A
provider availability problem may explain some nonzero exits or why the
interactive request stalled. It cannot explain the local process pile, the
rapidly repeated `claude -p` failures, or the captured copies of Neurobase's own
prompts. It is therefore a possible contributor, not an alternative explanation
for the amplification.

## Causal analysis

### Trigger

A Claude session started or cleared in an enabled project while an old
unconsumed raw existed. The installed matcher covered `startup|clear`, and every
matching event unconditionally called `spawn_curate_if_stale()` after recall.

The parent hook did not check staleness before spawning. The child process did,
but that check occurred independently in every spawned process.

### Primary root cause: recursive agent hooks

Neurobase used the same Claude installation in two roles:

- the interactive agent whose user-scoped hooks Neurobase installed; and
- the background LLM backend invoked by Neurobase through `claude -p`.

There was no marker telling hooks, "this Claude session was started internally
by Neurobase; do not capture, inject, or auto-curate it." The headless backend
sessions consequently entered the same hook lifecycle as user sessions. The raw
files containing Neurobase's own prompts prove that `SessionEnd` capture occurred
for those internal calls; the respawning detached curators and successful
containment after removing `SessionStart` support the corresponding startup
reentrancy path.

### Amplifier 1: no single-flight curation

`spawn_curate_if_stale()` calls `subprocess.Popen(..., start_new_session=True)`
without a lock or debounce. The `curate` command also has no project-level lock.
Every child can therefore pass the same stale check, read the same unconsumed
raws, and invoke a brain concurrently.

The build plan explicitly assumed curator runs were "manual/opportunistic,
effectively serialized" and deferred file locking. User-scoped hooks plus a
CLI-backed brain invalidated that assumption: opportunistic curation became a
reentrant background workload.

### Amplifier 2: unbounded per-pass brain fan-out

Before planning, `distill_docs()` iterates every unconsumed raw. For each
eligible, uncached Claude transcript it can make up to five chunk calls plus one
merge call. Each logical CLI call can itself make a second attempt for retryable
errors. Planning may require multiple batches, followed by a node-synthesis
call.

There is no cap on raws per pass, total brain calls, subprocess attempts, tokens,
or estimated cost. A backlog of 179 eligible raws can therefore fan out into
hundreds of calls in one nominal curate operation even without recursion.

### Amplifier 3: systemic failures are treated as local fallback

`_distill_one()` catches every exception and returns `None`, as required by the
"distill failure degrades to skim" contract. `distill_docs()` then proceeds to
the next raw. This is appropriate for a malformed or missing transcript, but
unsafe for a systemic backend failure such as quota exhaustion, authentication
failure, or a repeatedly unavailable CLI.

In this incident, `claude -p` exited nonzero. Each failure could be swallowed for
one raw and followed by another headless invocation for the next raw. Concurrent
curators multiplied that behavior.

### Amplifier 4: failure preserves the trigger condition

The hard curator contract correctly leaves raws unconsumed when planning fails.
However, `is_stale()` returns true if **any** unconsumed raw is older than the
12-hour cutoff. A failed pass therefore preserves the stale condition. Newly
captured internal prompts add more raws while the old raw keeps the gate open.

The unconsumed-on-plan-error rule should remain; data integrity is not the bug.
The missing pieces are backoff, a circuit breaker, and serialization around that
rule.

### Amplifier 5: silent detached execution

The hook redirects background stdin, stdout, and stderr to null and suppresses
spawn errors so session startup remains fail-safe. That meets the latency and
"never wedge the agent" requirements, but leaves the user with no immediate
signal that dozens of curators or paid/subscription-backed brain calls are
running. The curator log is written only at pass completion.

## Why existing tests did not catch it

- Claude hook integration tests replace `spawn_curate_if_stale()` with a no-op.
- Spawn behavior has no concurrency, debounce, or reentrancy tests.
- Brain backend tests use fake runners and do not exercise real user-scoped hooks.
- Distill tests verify per-document degradation, but not a systemic backend
  failure across a large raw set or a total call budget.
- Curator tests use injected fake brains and run serially.
- No live test launches a subscription CLI brain from inside an installed hook
  environment and asserts that Neurobase's own session is excluded from capture.
- `doctor` validates that hooks and a brain exist, but not that their combination
  can recurse or spend subscription quota in the background.

## Potential fixes

No single change is sufficient. The recommended repair is layered so one missed
guard cannot recreate the incident.

### P0: mark and reject Neurobase-internal agent sessions

When a CLI brain launches `claude -p` or `codex exec`, set a narrowly named
environment marker such as `NEUROBASE_INTERNAL_CALL=1`. Every hook fast path must
check that marker before reading transcripts, injecting memory, or spawning
curation and then exit 0.

Before relying on this design, a live spike must prove that Claude and Codex
propagate the parent environment to their hook subprocesses. If either agent
does not, use an agent-supported "disable hooks" invocation option if available,
or another out-of-band invocation identity that the hook can verify. Prompt text
alone is not an acceptable guard.

This fix prevents both halves of self-feedback: internal `SessionStart` cannot
spawn a curator, and internal `SessionEnd` cannot write the brain prompt back as
a raw.

### P0: enforce a per-project single-flight lock inside `curate`

Acquire an exclusive lock before the stale check and hold it through distill,
planning, application, synthesis, and logging. A second curator for the same
store/project must exit successfully without invoking a brain. The lock must be
cross-process, cross-agent, crash-recoverable, and tested on supported operating
systems.

The authoritative lock belongs in `curate`, not only in the hook spawner,
because manual commands, Claude hooks, and Codex hooks are all entry points. A
short spawn-side debounce can be added as an efficiency optimization, but it is
not the correctness boundary.

### P0: bound every automatic curation pass

Add a hard, configuration-backed budget covering at least:

- maximum raws considered per automatic pass;
- maximum logical brain calls and subprocess attempts;
- maximum distill chunks across the entire pass, not just per raw; and
- maximum wall-clock duration.

Move distillation behind a bounded raw/batch selection rather than distilling
the entire backlog before plan batching. When the budget is exhausted, leave the
remaining raws unconsumed and report a bounded, retryable result.

Automatic hook-triggered curation should use a much smaller budget than an
explicit foreground command. A dry-run or status surface should estimate the
number of eligible raws and worst-case brain calls before execution.

### P0: distinguish content fallback from backend failure

Keep skim fallback for missing transcripts, unsupported renderers, malformed
individual transcripts, invalid digest shape, and other document-local errors.
Stop the remaining distill loop on systemic `BrainError` categories such as
quota exhaustion, authentication failure, binary failure, or repeated timeout.

The brain layer should preserve structured failure categories. In particular,
the Claude backend should inspect the available result envelope as well as
stderr on nonzero exit so a usage-limit condition can trip a circuit breaker.
Once tripped, persist a short project/backend backoff and make later hook starts
no-op without launching another agent CLI.

### P1: make subscription spending explicit

The safest default is for `SessionStart` to perform recall only. Automatic
curation through a subscription-backed CLI should require explicit opt-in, or be
disabled when the selected brain is the same agent whose hook is running. Manual
curation, an OS scheduler with explicit cadence, an API backend with a configured
budget, or a different non-recursive backend are safer execution models.

Changing D8's default behavior is a product-contract change. It requires a spec
update and ADR rather than a silent implementation change.

### P1: detection and observability

- `doctor` should warn or fail when user-scoped hooks plus automatic curation
  plus a same-agent CLI brain create a reentrancy risk.
- `status` should show the active lock, last auto-curate attempt, backoff reason,
  raw backlog eligible for distill, and recent brain-call counts.
- Background failures should write a small start/stop/circuit-breaker record
  before and during the pass, without logging prompt content or secrets.
- Repeated starts or failures above a threshold should produce one local warning,
  not one warning per hook invocation.

### P1: quarantine existing feedback noise

Build a dry-run cleanup command that classifies likely self-generated raws using
multiple signals: internal prompt fingerprints, one-prompt headless session
shape, session-limit-only response, timestamps inside known bursts, and
provenance. It should move candidates to a quarantine directory with a manifest,
not delete them, and require explicit confirmation before permanent removal.

Cleanup must occur only after the feedback path is fixed. Otherwise curation or
cleanup itself can generate more captured internal sessions.

## Implementation status

The first mitigation layer was written on 2026-07-17 and landed as the three
`fix:` commits immediately following this one. To find them:

    git log --oneline --grep='^fix: ' -- src/neurobase/core/locks.py \
        src/neurobase/core/process_guard.py src/neurobase/curator/distill.py

An earlier draft named branch `fix-curator-runaway-guard-lock` here. That was
never accurate — no commit on that branch ever contained `core/locks.py` or
`core/process_guard.py`. The name was reused for unrelated coverage-report
docs, and the mitigation code in fact sat uncommitted in a working tree for
three days before being recovered.

The lesson is deliberately baked into the replacement above: it points at
*commits and paths*, not at a branch name. A branch is a moving reference that
goes stale the moment it is merged, renamed, or reused — which is precisely how
this section came to be wrong. An incident report that misdirects the next
reader is worse than one that says nothing, because "the fix exists somewhere"
is a belief that stops people looking.

The layer comprises:

- Real Claude and Codex CLI brain subprocesses, including backend version
  detection, receive `NEUROBASE_INTERNAL_CALL=1`; the hook fast path returns
  before reading input, capture, recall, or curation when that marker is present.
- `neurobase curate` takes a non-blocking, kernel-backed lock scoped by store and
  project before checking staleness or resolving a brain. Lock losers exit 0
  without an LLM call, and process exit/crash releases the lock automatically.
- The first systemic `BrainError` during distill trips a pass-local breaker. The
  failed and remaining raws use their deterministic skims, so quota/auth/outage
  failure is no longer retried once per raw.
- Regression coverage includes all hook events, both CLI runners, lock release,
  a lock loser that must not resolve a brain, and 20 child processes contending
  against a held lock.
- The full local gate passed: ruff check, ruff format check, mypy, and 847 tests.

This is not authorization to re-enable Claude `SessionStart`. A live disposable
spike still needs to prove that supported Claude and Codex versions propagate
the internal marker to hook subprocesses. Healthy automatic passes also remain
unbounded by a total call/raw/time budget. The hook stays disabled until those
remaining acceptance criteria are resolved and independently reviewed.

## Required regression and live tests

1. Twenty concurrent `curate --if-stale` processes for one project result in at
   most one brain invocation and one writer.
2. A Claude CLI brain child carrying the internal marker causes both Claude hook
   events to exit 0 with no output, no raw write, and no detached spawn.
3. The equivalent Codex CLI path is covered so the same architecture does not
   fail when Codex is selected first or explicitly.
4. A systemic failure on the first distill call stops later distill calls and
   opens a backoff; a malformed single transcript still falls back locally.
5. A large raw backlog cannot exceed the configured automatic call, raw, chunk,
   or time budget.
6. Lock cleanup is verified after success, handled failure, timeout, abrupt child
   termination, and stale-lock recovery.
7. Hook latency and always-exit-zero guarantees remain intact when the lock is
   busy, the marker is set, config is malformed, or the store is unavailable.
8. A live, disposable-store spike with real user-scoped hooks proves that one
   interactive startup produces no captured `DISTILL_SYSTEM`/`PLAN_SYSTEM`
   sessions and no more than one bounded curator process.

## Fix acceptance criteria

The incident should not be considered fixed until all of the following are true:

- Neurobase's own Claude/Codex brain calls cannot enter Neurobase hooks.
- At most one curator per project can invoke a brain at any instant.
- Automatic curation has a deterministic upper bound on external LLM calls.
- A provider quota/auth/outage failure stops further calls and creates backoff.
- The same-agent CLI plus user-scoped-hook configuration is diagnosed clearly.
- Existing self-generated raws can be quarantined without touching legitimate
  captures.
- The full CI gate and the disposable live-hook regression are green.

## Decisions and open questions

Decisions already made:

- Keep Claude `SessionStart` disabled until the acceptance criteria are met.
- Keep the backup of the prior Claude settings.
- Do not purge raw data as part of incident containment.

Open questions requiring implementation spikes or product decisions:

- Do Claude and Codex reliably propagate an internal environment marker to hook
  subprocesses on all supported platforms?
- Do current CLI versions provide a supported per-invocation way to disable
  hooks, and is that preferable to an environment marker?
- Should automatic curation be off by default for all subscription CLI brains,
  or only for the same-agent hook/backend pairing?
- What call and raw budgets are useful without making routine curation too slow?
- Should the lock be implemented with an added cross-platform dependency or a
  small platform-specific abstraction?
- Can provider envelopes distinguish quota exhaustion reliably enough for a
  specific circuit-breaker reason, with a generic systemic-failure fallback?

## Adversarial review record

The initial draft was reviewed from a skeptical incident-response perspective:
assume the apparent root cause is wrong, require a local artifact for every
number, look for a cheaper alternative explanation, test whether containment is
actually complete, and challenge whether each proposed fix closes the whole
failure path.

Findings and resulting revisions:

- **Causality was initially too absolute.** The report now separates confirmed
  facts, high-confidence startup-hook inference, and provider-side unknowns. It
  explicitly notes that the live process tree was not persisted.
- **A provider outage was an omitted alternative.** The triggering transcript's
  temporary-model-unavailability message is now recorded and bounded as a
  contributor that cannot account for the local amplification evidence.
- **Containment scope was overstated.** The report now says the observed
  Claude-triggered loop is stopped, while documenting residual `SessionEnd`
  self-capture and the separate Codex startup entry point.
- **A security claim exceeded the audit.** The unsupported statement that there
  was no secret exfiltration was removed. The report now states precisely what
  was and was not inspected.
- **One burst count was wrong.** Recounting the raw filenames changed 25 to 26;
  the 162-raw, 175-prompt, 1,317-limit, and 129-error counts were rechecked.
- **A lock alone could still allow one enormous pass.** The fix set retains both
  project single-flight and independent raw/call/chunk/time budgets.
- **Per-document fallback masked systemic failure.** The fix set now requires
  typed brain failures and a circuit breaker while preserving local skim
  fallback for document-specific problems.
- **The environment-marker fix depends on unproven propagation.** A live
  cross-platform propagation spike or a supported hooks-disabled invocation is
  an explicit prerequisite, not an assumed implementation detail.
- **Cleanup could destroy legitimate history.** The recommendation is
  fingerprinted dry-run quarantine with a manifest and explicit confirmation,
  never bulk deletion.

Adversarial verdict: **pass after revision**. The remaining uncertainty is named
and does not weaken the central finding: Neurobase created and amplified a large
number of background Claude calls. The report should be promoted into an ADR and
spec change only after the implementation spikes resolve the open design
questions.

## Bottom line

The coverage prompt was the moment the user noticed the failure, not its primary
cause. Neurobase recursively invoked and captured its own Claude-backed curation
sessions, then amplified the loop through concurrent detached curators and an
unbounded, failure-tolerant distill pass. Disabling `SessionStart` is the correct
temporary containment. The durable repair requires reentrancy exclusion,
single-flight curation, hard call budgets, and systemic-failure backoff before
automatic startup curation can be safely re-enabled.
