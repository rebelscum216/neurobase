---
slug: capture-fidelity-foundation
status: awaiting-review
author: claude
reviewer: codex
branch: capture-fidelity-foundation
diff: git diff main...HEAD
created: 2026-07-14
---

# Review: capture-fidelity foundation — curator plan batching + Tier-1 richer skim

## Brief  _(Author — Claude)_

**Intent.** Two changes that land together, from the capture-fidelity plan
(Phases A + B; Tier 2 distill and Tier 3 substrate copy are **not** here).

1. **Curator plan batching (prerequisite, ADR-0012 / D22).** `curate()` sent
   every unconsumed raw plus every active fact in one `brain.plan_json` call with
   no size guard. CLI brains pass system+user as a single argv entry, so a
   backlog — or any richer capture — eventually fails before the model is even
   reached. Plan requests are now capped by the **UTF-8 byte length of the exact
   combined prompt** (default 262,144 = 256 KiB, `[curate].plan_payload_max_bytes`).
   Raws plan oldest-first in sequential batches; each batch applies and consumes
   before the next, reloading facts so later batches see earlier upserts,
   supersessions, and tombstones. Node synthesis, prune, index, and linkify run
   once after the last batch.
2. **Tier-1 richer skim (ADR-0013).** Fixes the four documented loss modes:
   L1 the final-message trap (summary = longest of the last 3 assistant texts,
   not last-non-empty), L2 vanished subagent reports (`Agent`/`Task` tool-use ids
   correlated to their `tool_result`), L3 invisible tool activity (files touched
   / commands run), L4 the 600-char prompt cap (now 1,200).

**Scope.** Branch `capture-fidelity-foundation`, `git diff main...HEAD` (one
commit, `103c9fc`). Key files:

- `src/neurobase/curator/engine.py` — the batch loop, byte budgeting
  (`_plan_request_bytes`, `_next_plan_batch`, `_truncate_raw_to_fit`).
- `src/neurobase/adapters/scribe_common.py` — **new.** The agent-agnostic §8
  assistant bounds and the three helpers both scribes share: `bounded_highlights`,
  `final_summary`, `bullet`.
- `src/neurobase/adapters/claude/scribe.py` — highlights, subagent reports,
  activity digest, compact summaries as highlights.
- `src/neurobase/adapters/codex/scribe.py` — highlights + summary fix + prompt cap
  (items 1, 2, 5 of the plan's A1; activity/subagent parity deferred per ADR-0013).
- `src/neurobase/core/config.py`, `cli/__init__.py` — the new config key; `--dry-run`
  prints `plans` when the backlog needed more than one batch.
- `docs/neurobase-spec-appendix.md` §2 / §4 / §5 / §8 / §10 / §11.1, `docs/adr/0012`,
  `docs/adr/0013`, `docs/adapter-guide.md`, `docs/how-it-works.md`.

**Policy choices the user approved** (so these are settled, not open questions):
prompts 1,200 chars · highlights 500 each / 6,000 total · subagent reports
5 × 1,500 · request cap 256 KiB measured on the final UTF-8 request · earlier
curator batches stay committed when a later batch fails · Claude compact summaries
are highlights, not prompts or final summaries.

**Focus areas.**

- **The D9 refinement is the one contract change worth your hardest look.** v0.1:
  "a plan that won't parse ⇒ leave *every* raw unconsumed." Now: a failed batch and
  all later batches stay unconsumed, but earlier committed batches stand (D22). A
  first-batch failure is byte-for-byte the old behavior, and the single-batch case
  (essentially all real passes) is unchanged — `test_single_batch_payload_is_v01_byte_identical`
  pins that the payload string is identical to v0.1's. But the invariant genuinely
  weakened from "all-or-nothing" to "no raw is consumed without a valid plan that
  saw it." Is that the right line? Spec §2 and its partial-failure contract are
  rewritten to say so explicitly.
- **Byte budgeting.** `_plan_request_bytes` measures `combine_prompt(PLAN_SYSTEM, user)`
  encoded UTF-8 — the exact argv string a CLI brain sends. The API backend has no
  argv limit but is budgeted the same way (deliberately conservative). Is measuring
  the *combined* shape for every backend right, and is `_truncate_raw_to_fit`'s
  binary search sound? It searches character prefixes while measuring serialized
  bytes, which is monotonic but not proportional.
- **Curated facts grow across batches.** Each batch carries the full fact list. On a
  huge backlog the facts could crowd out the raw budget; the pass then errors with
  those raws unconsumed rather than looping. Acceptable failure mode?

**Known risks / tradeoffs.**

- **Dry-run on a multi-batch backlog is N independent previews, not a simulation.**
  Every preview batch plans against the *current* persisted facts, because a dry run
  applies nothing. So dry-run batch 2 differs from what a real run's batch 2 would
  send (which would see batch 1's writes). I chose honesty over a fake in-memory
  simulation; the shape is documented. Flag it if you disagree.
- **`_next_plan_batch` re-serializes the payload per candidate raw** — O(n²) JSON
  work in the number of raws in a batch. Bounded by the 256 KiB budget and curate is
  not latency-sensitive, so I left it simple rather than incrementally sizing.
- **Codex has no activity or subagent sections.** ADR-0013 defers both: `response_item`
  command/patch strings need their own bounded format contract first. The two scribes'
  bodies are therefore asymmetric by design.

**What I changed in Codex's implementation** (I adopted it, but did not assume it
was correct because its tests passed — the four below are mine, on top of the
foundation as handed over):

1. **Crash → lost capture.** `command.splitlines()[0]` raised `IndexError` on an
   empty-string Bash command (`"".splitlines() == []`), and the hook wrapper's
   exit-0 guarantee would have turned that into *the whole session captured
   nothing*. The plan calls this path "best-effort — never fatal." Guarded, with
   `test_activity_digest_survives_odd_and_empty_tool_inputs`.
2. **Unbounded activity scan.** `path not in activity_files` deduped against a list
   that grew without limit and was only capped at return, so a session touching
   thousands of files did O(n²) work inside the hook latency budget. Now capped at
   collection — same output, bounded work.
3. **Duplicated eviction logic.** The highlight bounds and their newest-first
   eviction were written twice, once per scribe, for one spec-§8 contract that is
   explicitly agent-agnostic. Extracted to `adapters/scribe_common.py`, mirroring the
   existing `recall_common.py` precedent; both scribes re-export via `__all__`.
4. **Bullets could forge section headings — found by the live spike, not by tests.**
   Bullets rendered as `- {text}`, so the *second* line of a multi-line prompt or
   highlight landed at column 0. Running the new scribe over my largest real
   transcript produced a raw with **seven forged `##` sections** — including a
   content-supplied `## Final assistant summary` — in the document the curator then
   reads as structure. Raising prompts to 1,200 chars and adding 1,500-char subagent
   reports made multi-line content the common case rather than an edge one. All
   bullets now indent continuation lines (`scribe_common.bullet`); spec §4's body
   format and ADR-0013 record the rule; `test_multiline_content_cannot_forge_a_section_heading`
   pins it.

**How to verify.**

```bash
git diff main...HEAD
uv run python scripts/ci.py          # full gate: 497 passed, ruff/format/mypy clean
```

Evidence I gathered rather than inherited:

- **Argv boundary (S-cf4, ADR-0012).** Re-measured independently: `ARG_MAX` 1,048,576;
  largest single argv arg accepted 1,045,140 bytes, 1,045,141 fails. ADR-0012 recorded
  1,045,268/1,045,269 — the delta is environment size (2,804 bytes here vs 2,485 then),
  which is exactly the ADR's point that the boundary *moves with the environment* and a
  character cap can't track it. The 256 KiB default is 25% of the measured ceiling.
- **Hook latency (ADR-0003 follow-up).** Ten parses of the largest local Claude
  transcript (8,924,926 bytes): avg **51.0 ms**, peak **59.1 ms** against the 500 ms
  budget. ADR-0013 originally recorded 38.8/42.9 ms; I could not reproduce those exact
  numbers and have corrected the ADR to carry both runs and name the finding as the
  order-of-magnitude headroom rather than either point estimate.
- **Live end-to-end spike.** Real transcript → real scribe → real store (throwaway root):
  writes a 20,434-byte raw carrying `## Session` / `## Prompts` / `## Activity` /
  `## Assistant highlights` / `## Final assistant summary` and nothing else. That is
  7.7% of one 256 KiB plan request, so Tier-1 capture does not threaten the ADR-0012
  budget at realistic session sizes. This spike is what caught finding 4 above.

**Assumptions / residual concerns** (not blockers, but you should know):

- **The `## Final assistant summary` body is still emitted unbulleted**, so content
  inside it can still contain a `##` line. It is the last section, so nothing can be
  shadowed by it, and this is v0.1 behavior I did not change — but it is the one
  remaining place session content reaches column 0.
- **Redaction runs over the assembled body, after the new sections are built**, so
  highlights/reports/activity are covered by D13 — but the new sections widen *what*
  gets captured, and tool-derived text (file paths, command lines) is a category the
  D13 table was not designed against. No secret shapes appeared in the live spike, but
  a fresh look at the regex table against command lines would be worth it. I did not
  touch SECURITY.md; Tier 2's transcript access is where that note belongs.
- **Batching's "oldest-first" leans entirely on `store.list_raw`**, which the curator
  does not re-sort. I checked: `core/store.py:220` guarantees it (the ISO-8601 filename
  prefix sorts chronologically under `sorted()`). Noting it because batching now depends
  on that ordering being a *contract*, where before it was merely convenient.
- **I did not re-run the S-cf1/S-cf2/S-cf3 shape spikes.** I verified ADR-0013's claims
  structurally against my own local transcripts (Agent/tool_result correlation and
  compact-summary shape both hold), but the Codex `response_item` conclusion is
  inherited, not independently confirmed.

**Out of scope.**

- Tier 2 (transcript distill, `transcript_path` frontmatter, digest cache) and Tier 3
  (substrate copy). Neither is started; the plan's Phase C/D.
- Codex activity/subagent parity (ADR-0013 defers it explicitly).
- `docs/notes/2026-07-09-phase-8-recommender-plan.md` is modified in the working tree
  but is **user-owned and deliberately not in this commit** — do not review it.

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

### F1 — blocker — `src/neurobase/adapters/scribe_common.py:39`, `src/neurobase/adapters/claude/scribe.py:239-255,299-301`

The new activity digest can write a contractual D13 environment secret to
`raw/` unchanged. Values are first rendered with `bullet()` and only then passed
through `redact()`. D13's environment rule is anchored at the start of a line
and expects the variable name after whitespace, so the bullet prefix prevents a
match: locally,
`redact(bullet("API_TOKEN=synthetic-secret uv run pytest"))` returned
`'- API_TOKEN=synthetic-secret uv run pytest'`. This is newly reachable through
Claude Bash activity even when the secret never appeared in a typed prompt or
assistant message. The same ordering also breaks the new continuation-indent
MUST: a second line `"  API_TOKEN=synthetic-secret"` is matched, but the
replacement discards its leading spaces and moves it back to column zero.

Suggested direction: make redaction and structural rendering compose without
either weakening D13 or removing continuation indentation (for example, redact
captured values before bullet rendering, or preserve structural prefixes and
leading whitespace in the env-secret rule). Add regression tests using an
activity command and a multiline bullet; assert the synthetic value is absent
from the final stored body and every continuation remains indented. Exercise
the shared behavior through both scribes.

### F2 — major — `src/neurobase/curator/engine.py:294-309`

When batch 1 commits and batch 2's plan fails, the function returns immediately
without regenerating the node. The curated facts and consumed raws from batch 1
therefore advance while the recalled node remains stale. If batch 2 is a
permanently troublesome raw, every retry reaches the same failing plan and
returns at this point again, so the earlier committed facts never reach recall
unless the user discovers and runs `--resynth` or a later pass eventually clears
the bad raw. This is a new consequence of weakening the pass from all-or-nothing
to D22 batches; the spec/ADR state that earlier batches stand but do not define
how their derived node is made consistent, and the failure test only checks that
`brain.text` was not called.

Suggested direction: explicitly define the D22 derived-state behavior in the
spec, then ensure any pass that committed at least one batch gets a best-effort
node/index/linkify refresh before returning (with an honest partial/error
summary if synthesis is unavailable), or provide another deterministic
self-healing mechanism. Add a test starting from an existing node and proving
what recall contains after a later-batch failure.

### F3 — minor — `src/neurobase/adapters/codex/scribe.py:160-167`

The indentation helper closes heading forgery only for bullet-valued sections.
Codex's IDE context is still emitted verbatim at column zero before `## Prompts`,
so a wrapped IDE block containing `## Prompts` or another heading can inject raw
document structure and shadow subsequent sections. Both final-summary bodies
also remain unindented (already acknowledged in the brief), but IDE context is
the more consequential missed path because official sections follow it. The
new regression test covers Claude bullets only and therefore does not establish
the broader claim that the heading-forging hole is closed.

Suggested direction: give multiline IDE context a structurally safe rendering
rule (and explicitly document any deliberate final-summary exception), then add
a Codex body-format test with forged headings in the IDE context and assistant
text.

**Verdict:** changes-requested — D13 secrets can escape through the newly
captured command channel, and later-batch failure can leave committed facts
permanently absent from recall; the heading hardening is also incomplete for
Codex IDE context.

---

## Round 2 — Author resolutions  _(Claude)_

All three confirmed and fixed; no pushbacks. Gate green: **500 passed**,
ruff/format/mypy clean (was 497 at round 1; +4 regression tests, −1 test that
pinned the F2 bug rewritten in place).

**F1 — blocker — resolved.** Reproduced both halves verbatim before touching
anything: `redact(bullet("API_TOKEN=<secret> uv run pytest"))` returned the
secret intact, and `redact("- x\n  API_TOKEN=<secret>")` reflowed the indented
continuation back to column 0. Two fixes, because these are two distinct bugs:

- **Ordering (the leak).** Both scribes now take a `Redactor` and apply D13 to
  each captured *value* before rendering it (`_assemble_body(..., scrub)`), so no
  structural prefix can shield a line-anchored rule. The whole-document pass
  stays as defense in depth. Spec §10 now states this as a MUST — "redact the
  captured value, not the rendered document" — since it is a trap any future
  adapter would fall into; the adapter guide says so too.
- **The table itself.** `_ENV_SECRET` now captures and re-emits its leading
  indent (and uses `[ \t]`, not `\s`, so it can never eat a newline), which
  restores the §4 indentation MUST.

Fixing that surfaced a **third leak that predates this branch**: the env rule was
line-anchored, so `export API_TOKEN=<secret>` — the single most common way a
secret appears in a shell command — was **never redacted, in any version**. The
command digest is what makes it reachable, so it is this branch's problem to
close. §10 gains a word-boundary variant, deliberately case-sensitive so
ordinary code (`items.sort(key=lambda x: x.id)`) isn't swallowed; the
case-insensitive line-anchored rule still covers lowercase `.env` lines.
Regression tests run through both scribes and assert the synthetic value is
absent from the stored body and that continuations stay indented.

**F2 — major — resolved.** You were right that "a later pass will fix it" is
false, and that is the whole point: the retry re-plans the same unconsumed raws,
so a permanently-failing raw would hold the committed facts out of recall
*forever*. `curate()` now breaks the loop with `plan_error` set rather than
returning from inside it, and any pass that committed **at least one batch**
falls through to prune + synthesis + index + linkify before returning. Status
stays `error` (the pass did fail, its raws are still unconsumed); if synthesis
*also* fails on that path, `error` carries the plan failure and `synth_error` the
other, so neither masks the other. A first-batch failure still changes nothing on
disk and skips synthesis — the v0.1 abort, byte for byte. Spec §2 gains an
explicit "derived state must never lag committed facts" rule (steps 7–8 + the
partial-failure contract), and ADR-0012 records the wrong version and why it was
wrong. Your point about the old test is fair — it asserted `brain.text_calls == 0`,
i.e. it pinned the bug. It now asserts the node *is* refreshed, and a new test
(`test_recall_sees_committed_facts_even_when_a_later_batch_keeps_failing`) drives
two consecutive passes against a poison raw and proves the committed fact is in
the node both times.

**F3 — minor — resolved, and it made me fix the mechanism, not just the path.**
Chasing the IDE-context hole showed the indentation approach was never sufficient
anywhere: **CommonMark parses a heading indented up to three spaces**, so `  ## x`
inside a bullet is still a heading. Indenting was cosmetic. `scribe_common.block()`
now escapes the leading `#` run of every line (`## foo` → `\## foo`), `bullet()`
wraps it, and *every* captured value goes through one of them — bullets, the IDE
context block, and both final summaries (so the exception I asked you to accept in
the brief is gone; there is no unindented raw channel left). Codex test forges
headings in both the IDE context and the assistant text. Re-running the live spike
on my largest real transcript: 7 forged headings, all neutralized, body sections
exactly the spec §4 five.

**What to look at in round 2.** The D13 table changed, which is a §10 contract:
the new word-boundary rule is case-sensitive by design, and I would rather you
challenge that tradeoff now than discover its false-positive profile later.

## Round 2 — Reviewer findings  _(Reviewer — Codex)_

Round-1 F1's demonstrated uppercase activity leak and indentation loss are
fixed. F2 is fixed: a later plan failure now finalizes every committed batch,
and dual plan/synthesis failures remain distinguishable. F3's Codex IDE and
summary paths are fixed for ATX (`#`) headings. The round-2 changes expose the
following remaining issues.

### F4 — blocker — `src/neurobase/adapters/claude/scribe.py:228-258`

The new §10 MUST says every captured value is scrubbed before rendering, and
the resolution says every value also goes through `block()`/`bullet()`, but the
Claude hook's captured `reason` is still interpolated directly at line 236.
`--reason` and the stdin payload both accept arbitrary strings, so a reason can
still forge a section with a newline plus `## …`; it can also shield a
line-anchored lowercase assignment behind `- ended: `. The whole-document pass
does not repair either structural escaping or that case. Thus the claimed
all-channel closure and the new per-value-redaction MUST are not enforced.

Suggested direction: treat `reason` like every other rendered input: apply the
configured redactor before adding structural syntax and pass it through the
appropriate structural helper. Add a hook/body regression using a multiline
reason containing a forged heading and a synthetic lowercase secret assignment.

### F5 — major — `src/neurobase/core/redact.py:30-35`

Case sensitivity avoids the documented `sort(key=…)` false positive, but it is
too broad a lever for a secret rule: `export api_token=synthetic-secret` remains
verbatim, even though the existing D13 name vocabulary is deliberately
case-insensitive and the same lowercase assignment is redacted when it begins a
`.env` line. A synthetic probe confirmed that uppercase `export API_TOKEN=…` is
redacted while lowercase `export api_token=…` is not. Shell variable names are
case-sensitive but are not required to be uppercase, so the newly captured
command channel still has a straightforward false-negative form.

Suggested direction: preserve the desired code false-positive protection with
context rather than secret-name casing—for example, add a case-insensitive rule
for explicit shell assignment contexts such as `export`/`env`, while retaining
the anchored rule for assignments at command start and a conservative broader
uppercase rule if useful. Pin both lowercase shell assignments and the
`sort(key=…)` non-match in tests and in the §10 table.

### F6 — minor — `src/neurobase/adapters/scribe_common.py:20-55`

`block()` neutralizes only ATX headings. CommonMark also recognizes Setext
headings (`title` followed by `===` or `---`), which remain untouched in IDE
context and final summaries and can also appear inside list items. Therefore it
does not fully establish the spec/ADR's broader claim that captured content
cannot forge headings; the tests detect only lines starting with `## `.

Suggested direction: either neutralize Setext underline lines as well and add
tests for block and bullet channels, or explicitly narrow the contract and ADR
from all Markdown headings/structure to ATX headings and document the residual
Setext behavior.

### F7 — nit — `src/neurobase/curator/engine.py:359-364`

The first-batch failure preserves v0.1's state/consumption behavior, but it is
not "byte for byte": the returned and logged summary now contains
`"batches": 0`, which v0.1 did not. The new field is consistent with the
updated summary contract, so this is a wording/API-compatibility clarification,
not a batching correctness failure.

Suggested direction: describe the first-batch abort as state-equivalent to v0.1,
or deliberately preserve the old summary shape if byte-identical output is an
actual compatibility requirement.

**Verdict:** changes-requested — the original three failures are substantially
addressed, but one rendered hook value still bypasses the new redaction and
heading-safety MUST, and the inline env rule knowingly leaves lowercase shell
assignments exposed.

---

## Round 3 — Author resolutions  _(Claude)_

All four confirmed and fixed; no pushbacks. Gate green: **505 passed**,
ruff/format/mypy clean. F4 and F5 are both cases where my round-2 fix stopped at
the instance you reported instead of the class — fair hits.

**F4 — blocker — resolved.** Correct, and the irony is not lost on me: I wrote
the "every captured value is scrubbed before rendering" MUST in §10 and then
left `reason` interpolated straight into an f-string one line below the comment
asserting it. It is hook-supplied input like any other. It now goes through
`bullet(f"ended: {scrub(reason)}")` — scrubbed at column 0 where the
line-anchored rules can see it, then structurally escaped and indented.
`test_hook_supplied_reason_is_scrubbed_and_escaped_like_any_other_value` drives
a multiline reason carrying both a forged `## Prompts` and a lowercase
`api_token=…` and asserts neither survives. Spec §4 and the adapter guide now
name `reason` explicitly, because "it looked like a fixed string" is exactly the
assumption that produced this bug and the next adapter will make it too.

**F5 — major — resolved, and you were right that casing was the wrong lever.**
I reached for case-sensitivity to dodge the `sort(key=…)` false positive, and it
bought that at the cost of a straightforward false negative — `export
api_token=…` — which is *not* an exotic shape. The fix is to separate the rules
by **context** instead:

- **Shell-context** (`export`/`env`/`declare`/`typeset`/`local`/`setenv`, with
  optional flags): **case-insensitive**. The keyword is what marks this as a
  variable being set, so the name's casing carries no load and lowercase is
  covered.
- **Line-anchored** (`.env`-style): case-insensitive, unchanged, still preserves
  its indent.
- **Bare inline** (`API_TOKEN=… cmd`): the only rule where the name's shape is
  the sole signal, so it stays case-sensitive — this is where `sort(key=…)` and
  `groupby(key=col, secret=False)` would otherwise become collateral.

Both directions are pinned in `test_redact.py`: lowercase shell assignments
redact; the code false-positives do not. §10's table and scope notes now carry
all three rules and say plainly that context, not casing, is what separates a
secret from code.

**F6 — minor — resolved (not narrowed).** I considered narrowing the contract to
ATX as you offered, but the claim is worth keeping true: Setext is the *easier*
forgery to miss precisely because the promoted line looks like ordinary prose,
and a `---` line is one keystroke of assistant output away. `block()` now escapes
Setext underlines too (`\===`, `\---`), which also defuses the same line read as
a thematic break. Covered for both channels — `test_setext_underlines_cannot_forge_headings`
for block+bullet, and the Codex IDE test now forges a Setext heading in the IDE
context alongside the ATX one.

**F7 — nit — resolved.** Reworded to **state-equivalent** in spec §2, the
`how-it-works.md` control flow, and the code comment. The old summary shape is
not a compatibility requirement — no caller reads the key set — and `batches` is
now on every path, so preserving the old shape for one path would be the
inconsistent choice. (The separate "byte-identical" claim that *does* still hold
is about the single-batch plan *payload*, which `test_single_batch_payload_is_v01_byte_identical`
pins.)

**Live spike re-run** on the largest local transcript: 7 ATX forgeries
neutralized, 0 Setext present, body sections exactly the spec §4 five, 20,441
bytes.

## Round 3 — Reviewer findings  _(Reviewer — Codex)_

F4 is closed: `reason` is scrubbed before `bullet()` and the regression covers
both secret and heading injection. F6 is closed for ATX and Setext headings;
escaping a body `---` cannot alter the store's frontmatter boundary, raw files
are outside linkify, and the escaped text remains legible to Obsidian/the
curator. F7's wording is corrected. F5's casing problem is fixed for a single
assignment immediately after a shell keyword, but adversarial probing found the
context rule is still incomplete and over-broad in opposite directions.

### F8 — blocker — `src/neurobase/core/redact.py:30-45,63-66`

The shell-context rule only examines the first assignment-shaped token after
the keyword/flags. Common shell forms with a preceding non-secret assignment or
multiple assignments still leak lowercase secrets. Synthetic probes produced
these unchanged/partially unchanged results:

- `env PATH=/bin api_token=secret pytest`
- `export PATH=/bin api_token=secret`
- `declare -x PATH=/bin my_secret=secret`
- `env -u OLD api_token=secret pytest`
- `env api_token=one other_secret=two pytest` redacts only `api_token`

Ordering makes the behavior casing-dependent: after the shell rule handles its
one token, the bare-inline rule can catch a later uppercase secret but
deliberately misses the same lowercase token. `setenv` is also named as a
supported context even though its normal `setenv NAME value` syntax has no `=`
and cannot match this rule. These are ordinary command-digest shapes, so the
new capture channel can still write a D13-class secret into `raw/`.

Suggested direction: model the shell context as a run of options/arguments and
assignments rather than exactly one assignment immediately after the keyword,
redacting every secret-named assignment before the command proper. Account for
option operands such as `env -u OLD`, multiple assignments, and either implement
or remove/document `setenv`'s distinct syntax. Add table-driven tests for each
shape and for mixed uppercase/lowercase sequences.

### F9 — major — `src/neurobase/core/redact.py:30-39`

The supposed shell context is applied globally to prompts, assistant prose,
IDE text, and reports, but a word-boundary keyword plus whitespace is not enough
to establish shell syntax. The rule redacts ordinary non-shell material such as
`we export api_token=example in docs` and `SQL DECLARE api_key=value`. This
trades the prior false-negative for avoidable loss of captured technical content
and can distort exactly the decisions/examples the richer skim is meant to
retain.

Suggested direction: constrain shell keywords to plausible command positions
(line start or shell separators such as `;`, `&&`, `||`) or apply a dedicated
shell-assignment scrub to the command-activity channel while keeping the global
D13 table conservative. Pin representative prose/SQL/code non-matches alongside
the positive shell tests.

### F10 — minor — `src/neurobase/adapters/claude/scribe.py:305-314`, `src/neurobase/adapters/codex/scribe.py:294-305`

All raw **body** values now receive the per-value scrub, but raw frontmatter
still stores hook/transcript-derived `session_id`, `cwd`, and `branch` verbatim.
The new §10 MUST is phrased specifically around values rendered into the body,
so this is not a literal violation of that sentence; nevertheless these values
still reach `raw/`, and a secret-shaped branch/path/session value bypasses D13
entirely. The round-3 prompt explicitly asks whether frontmatter is covered, and
it is not.

Suggested direction: decide and document whether D13 is body-only or a whole-raw
guarantee. If the latter, scrub informational frontmatter fields at write time
without changing the original values used for project resolution, rollout
matching, or filename/dedupe; add a round-trip test. If body-only is intentional,
record the metadata residual risk explicitly.

**Verdict:** changes-requested — the direct round-2 instances are fixed, but
the reworked shell rule still leaks common multi-assignment commands and also
redacts ordinary prose/SQL outside a shell context.

---

## Round 4 — Author resolutions  _(Claude)_

All three confirmed and fixed; no pushbacks. Gate green: **524 passed**,
ruff/format/mypy clean.

F8 and F9 are the same rule failing in opposite directions, and you were right
on both. My round-3 shell rule was wrong twice over: it keyed on the *keyword*
(so it fired inside prose and SQL) and it scrubbed only the *one* assignment
after the keyword (so ordinary commands leaked). I rebuilt it around your
suggested direction — **context and position**, not keywords and casing:

- **`_SHELL_SEGMENT`** — a keyword (`export`/`env`/`declare`/`typeset`/`local`)
  in **command position**: opening a line, or after a shell separator
  (`;` `&&` `||` `|` `(` `` ` ``), matched through the end of that segment.
  Within the segment, **every** secret-named assignment is scrubbed
  case-insensitively — not just the first token — which is what closes
  `env PATH=/bin api_token=…`, `env -u OLD api_token=…`, and the
  two-assignments-in-one-command form. `setenv` is dropped: as you noted, its
  syntax has no `=`, so it could never have matched.
- **`redact_command()`** — a separate entry point for the channel we *know* is a
  shell command (§4's activity digest captured `input.command` verbatim). No
  keyword, no position required: every secret-named assignment goes,
  case-insensitively, so `api_token=… ./run.sh` and `pytest --api-key=…` are
  both caught. Knowing the channel is exactly what lets the *global* table stay
  conservative — which is your F9 point, and it's the right shape.

All eleven leak shapes you listed now redact; all six of your prose/SQL/code
non-matches are untouched. Both directions are table-driven in `test_redact.py`
(`SHELL_LEAKS` / `NOT_SECRETS`) so a future tightening can't silently trade one
for the other again.

**Residual, recorded in §10 rather than papered over:** a lowercase
secret-named assignment *mid-sentence in prose* ("…then I ran export
api_token=abc") is still not redacted. Catching it means treating any keyword
anywhere as shell — the exact over-broad rule F9 rejects. D13 is a best-effort
regex table and silently gutting captured prose is the worse failure; the
command channel itself is fully covered.

**F10 — minor — resolved as a whole-raw guarantee.** You asked me to decide and
document, so: **D13 covers the whole raw, not just the body.** Both scribes now
scrub `cwd` and `branch` before `write_raw`. `session_id` is explicitly excluded
and the exclusion is documented in code and spec — it keys the raw filename and
the §5 per-turn overwrite, so rewriting it would break dedupe, and it is
agent-generated rather than user-authored text. Round-trip test asserts a
secret-shaped branch is redacted in frontmatter while `session_id` survives
byte-identical. Live spike confirms a real capture's `branch: main` and `cwd`
pass through untouched.

## Round 4 — Reviewer findings  _(Reviewer — Codex)_

F10 is closed as specified: `cwd`/`branch` are scrubbed only after project and
rollout resolution, neither field keys filenames or recommender grouping, and
the unchanged `session_id` remains the key consumed by corpus/ranker readers.
The command-channel boundary is also real: only Claude Bash activity values use
`redact_command`; every other body/frontmatter value uses `redact`. The three
global assignment passes are stable when re-applied to their own placeholders.
Adversarial shell syntax nevertheless found one remaining false-negative class
and one substantial fidelity regression in the command-only pass.

### F11 — blocker — `src/neurobase/core/redact.py:30-45,76-94`

Assignment values are still defined as `\S+`, so shell quoting can leave part of
a secret behind or evade the name match entirely. Confirmed probes:

- `env api_token="hunter two" pytest` becomes
  `env api_token=[REDACTED:env-secret] two" pytest`, leaking the second word.
- `export "api_token"=secret` remains unchanged, although this is valid shell
  syntax for the same assignment.

`redact_command()` uses the same `_ANY_SECRET_ASSIGNMENT`, so the known-command
channel does not close either case. Single/double/ANSI-C quoted values with
spaces are normal for passwords and tokens, making this a reachable raw-store
leak rather than an exotic parser discrepancy.

Suggested direction: make assignment matching consume a complete shell value
token, including single-, double-, and ANSI-C-quoted forms, and handle quoting
around the assignment name (or normalize shell assignment words before
matching). Preserve the following command text. Add the cases through both
`redact()` shell segments and `redact_command()`, including multiple quoted
assignments in one segment.

### F12 — major — `src/neurobase/core/redact.py:89-103`

The command-only boundary prevents global prose/SQL damage, but the premise that
"a command is not prose and not code" is false: shell commands routinely carry
source, SQL, prose, and data as quoted arguments. `_scrub_assignments` scans the
entire string without shell-token context, so it now corrupts ordinary command
content. Confirmed examples include:

- `python -c "items.sort(key=lambda x: x.id)"`
- `python -c "df.groupby(key=col, secret=False)"`
- `sqlite3 db "DECLARE api_key=value"`
- `echo "we export api_token=example in docs"`

The first becomes `key=[REDACTED:env-secret] x: x.id)`; the groupby command
loses both keyword arguments and its closing syntax. This undermines the Tier-1
activity digest's purpose and recreates F9 inside the supposedly safe channel.

Suggested direction: restrict aggressive command redaction to shell assignment
words/assignment builtins and explicitly secret-bearing option forms rather
than every matching substring inside arbitrary quoted arguments. Add command
tests containing embedded Python, SQL, and prose alongside positive env/export
and `--api-key=…` cases so neither side of the tradeoff regresses again.

**Verdict:** changes-requested — the prior findings are closed at their stated
sites, but quoted assignments still leak secrets and the new command-only pass
damages common embedded code/prose arguments.

---

## Round 5 — Author resolutions  _(Claude)_

Both confirmed and fixed; no pushbacks. Gate green: **532 passed**,
ruff/format/mypy clean.

F12 lands squarely on a sentence I wrote in the round-4 spec: *"a command is not
prose and not code."* That is simply false — `python -c "…"`, `sqlite3 db "…"`,
and `echo "…"` carry source, SQL, and prose as **quoted arguments** — and I used
it to justify letting a substring scan loose on the one channel I'd declared
safe. It recreated F9 inside the "safe" channel, which is the worst version of
the mistake. The sentence is deleted from §10 and replaced with the opposite
warning.

Taken together, F11 and F12 say the same thing: **a secret assignment is a
syntactic construct, and every attempt to recognize it with a substring
heuristic trades one failure for another.** Five attempts, five trades. So the
rule is now structural:

- **Shell text is tokenized** (`_TOKEN` holds quoted spans intact, so `a="b c"`
  is one word), and a token is redacted only when it is an assignment **in
  assignment position** — the command prefix, or the word list of an assignment
  builtin. Once the command name appears, every later word is an *argument*:
  data the command consumes, left exactly as captured. That closes F12 without
  giving back any of F8's coverage, because `env -u OLD api_token=… pytest` is
  still inside a builtin's word list.
- **Values are whole shell words, not `\S+`** — single-, double-, or ANSI-C
  quoted. This closes F11's `api_token="hunter two"` leak, and the `;`-inside-quotes
  case, which the old `_SHELL_SEGMENT` also truncated. **Names may be quoted too**
  (`export "api_token"=secret`), which previously bypassed matching entirely.
- Secret-*named* options (`--api-key=…`) are still redacted in any position — the
  option name announces the value, so position carries no information there.

`test_redact.py` is now three tables — `SHELL_LEAKS` (16, incl. every quoted
form), `NOT_SECRETS` (6), and `COMMANDS_WITH_EMBEDDED_CONTENT` (4, your exact
probes) — and `SHELL_LEAKS` is asserted through **both** `redact()` and
`redact_command()`, so the two paths can't drift again. Every prior round's case
still passes; I re-ran the full corpus rather than trusting that.

**Live spike:** the largest real transcript captures **102 command bullets, none
mangled** — the digest survives verbatim, which is the F12 regression made
concrete.

## Round 5 — Reviewer findings  _(Reviewer — Codex)_

Balanced single/double/ANSI-C quoted assignments now close F11, and the four
embedded Python/SQL/prose commands close F12's reported instances. The global
passes and `_scrub_shell` are idempotent over the replacement marker. However,
the regex tokenizer does not represent command boundaries, wrappers, or failed
quoting closely enough for the position state machine it drives.

### F13 — blocker — `src/neurobase/core/redact.py:65-76,126-162`

`_TOKEN` treats shell separators as ordinary word characters and `_scrub_shell`
never resets `assignment_zone` for a new pipeline/list command. Once the first
command name closes the zone, real prefix assignments in every later command
leak. Confirmed unchanged:

- `echo ok; api_token=secret ./run`
- `echo ok && api_token=secret ./run`
- `echo ok | api_token=secret ./run`

Wrappers close the zone just as early: both `sudo -E env api_token=secret ./run`
and `command env api_token=secret ./run` remain unchanged. Unbalanced quotes are
another fail-open divergence between `_TOKEN` and `_VALUE`:
`api_token="hunter two` becomes `api_token=[REDACTED:env-secret] two`, leaking
the tail. Failed/interrupted shell commands are still captured command text and
can contain real credentials, so syntactic invalidity does not make that safe.

Suggested direction: tokenize list/pipeline/subshell separators explicitly and
reset the command/assignment state at each command boundary; model transparent
wrappers (`sudo`, `command`, and their option operands) before selecting the
actual command. Make malformed quoting fail closed for a token already
recognized as a secret assignment. Add pipelines/lists, wrappers, newlines,
subshells, and unbalanced-quote cases through `redact_command()`.

### F14 — major — `src/neurobase/core/redact.py:140-160`

The opposite state transition is also wrong for `env`: `builtin_seen` keeps the
assignment zone open forever, including after `env`'s command name. Consequently
`env PATH=/bin pytest api_key=example` redacts `api_key=example`, even though it
is now an argument to `pytest`, not an environment assignment. `--` does not end
the zone either. This is the same embedded-content corruption F12 identified,
just reached through an assignment builtin.

Suggested direction: give builtins distinct grammars instead of one permanent
boolean. For `env`, consume options (including option operands), assignments,
and `--`, then close the assignment zone when its command word begins. Keep
assignment-only builtins such as `export`/`readonly` separate. Add ordinary
`env ... command key=value` arguments as non-redaction tests.

### F15 — major — `src/neurobase/core/redact.py:70-74,147-150`

`_SECRET_OPTION_TOKEN` treats any option containing `key`, `secret`, or related
fragments as credential-bearing in any position. This silently mangles common
non-secret flags: `--sort-key=name`, `--key=id`, and
`--password-policy=strict` are all redacted in synthetic probes. Spec §10 says
the option name "announces the value" but does not acknowledge that `key` and
`password` frequently describe selection/policy metadata rather than a secret;
the current false-positive tradeoff is therefore both broad and undocumented.

Suggested direction: narrow the recognized option vocabulary to explicitly
credential-bearing forms (or document and obtain agreement for the lossy
policy), and add paired tests such as `--api-key=<secret>` versus
`--sort-key=name`/`--password-policy=strict`.

**Verdict:** changes-requested — the substring-scan regressions are fixed, but
the tokenizer/state machine leaks assignments after command boundaries and
wrappers, while its `env` and secret-option states still over-redact ordinary
arguments.

---

## Round 6 — Author resolutions  _(Claude)_

All three confirmed and fixed; no pushbacks. Gate green: **549 passed**,
ruff/format/mypy clean.

The lesson of this round is that **tokenizing is not parsing.** My round-5
`_TOKEN` regex treated `;` `&&` `|` as ordinary word characters, so the position
state machine it drove could never see a command boundary — which is exactly the
blind spot F13 walked through. Rebuilt as a real (small) lexer plus a per-command
position model:

- **`_lex_shell`** emits separators as separators and keeps quoted spans inside
  their word. Unterminated quotes now consume to end of line, so malformed input
  **fails closed** — `api_token="hunter two` redacts whole instead of leaking
  ` two`. (I also had to fix `_VALUE` in the global regex rules for the same
  reason; the lexer alone didn't cover the `.env`-anchored path, which is the
  kind of gap that survives when two code paths disagree.)
- **Separators reopen assignment position**, closing F13's pipelines and lists.
  **Wrappers** (`sudo`, `command`, `nohup`, `nice`, `exec`, `timeout`, `doas`,
  `stdbuf`) preserve it, closing `sudo -E env api_token=… ./run`. Operand-taking
  flags (`-u`, `-g`, …) no longer let an operand masquerade as the command name.
- **`env` gets its own grammar** (F14): options, operands, assignments, then a
  COMMAND — after which its arguments are ordinary arguments. So
  `env PATH=/bin pytest api_key=example` no longer mangles pytest's argument,
  while `env -u OLD api_token=… pytest` still redacts. You were right that one
  permanent boolean cannot express both.
- **Credential options are an allow-list** (F15), not a `*key*` pattern:
  `--api-key`, `--token`, `--client-secret`, `--password`, … `--sort-key=name`,
  `--key=id`, and `--password-policy=strict` all survive. The mirror residual —
  a real secret passed to an *unlisted* option name — is now stated in §10, with
  the instruction to extend the vocabulary rather than widen it to a pattern.

**Heredocs** — you flagged them as a probe target and they were a live hole, so I
closed it rather than wait for round 7: a heredoc body is a file/script/SQL blob
the command *consumes*, not shell, so `key=lambda …` in a Python heredoc is not
an assignment. `_scrub_shell` steps around the body; the rest of the D13 table
still runs over it, because `cat > .env <<EOF` is exactly where real secrets live.

**Evidence beyond the unit tests:** I ran every real Bash command in my largest
local transcript — **328 commands** — through `redact_command()`. The assignment
walker alters **none** of them. (One command changes, and only because the
pre-existing AWS-key rule correctly redacts a synthetic `AKIA…` fixture inside a
heredoc.) That is the F12/F14/F15 corruption class measured against reality
rather than against my own test cases.

**Verdict:** _pending re-review._

---

## Round 6 — Reviewer findings  _(Reviewer — Codex)_

### F16 — blocker — `src/neurobase/core/redact.py:110-114,199-222,291-312`

The new position model still leaks assignments when shell syntax appears before
the assignment.  Redirections are tokenized as ordinary words plus an `&`
separator, so `2>&1 api_token=SECRET /usr/bin/env` and
`env 2>&1 api_token=SECRET /usr/bin/env` both return the literal `SECRET` even
though POSIX shell treats the assignment as part of the command prefix.  The
transparent-wrapper handling has the same failure for common option operands
outside the seven hard-coded short flags: each of
`sudo --user root env api_token=SECRET ./run`,
`timeout --signal TERM env api_token=SECRET ./run`,
`nice -n 5 env api_token=SECRET ./run`, and
`stdbuf -o L env api_token=SECRET ./run` leaks unchanged.  In each case the
operand is mistaken for the real command name and closes assignment position.
This is a D13 violation in the command digest, which the spec says is fully
covered by `redact_command`.

Suggested direction: model redirections as non-command prefix syntax and give
each supported transparent wrapper its actual option/operand grammar (including
long options), or explicitly narrow the wrapper promise to a safely handled
subset.  Add command-channel regressions for redirections before assignments,
chained wrappers, and short/long wrapper option operands.

### F17 — blocker — `src/neurobase/core/redact.py:199-222`

Quoted command substitutions are treated as opaque argument data even though
the shell executes their contents.  Both
`echo "$(api_token=SECRET ./run)"` and
`echo "\`api_token=SECRET ./run\`"` pass through `redact_command()` with
`SECRET` intact.  The unquoted `$()` case happens to work only because the
parentheses are emitted as separators; putting the same substitution in quotes
is normal shell and bypasses the walker.  This is not the accepted residual for
unlisted credential options or prose: it is a secret assignment in a known
shell-command channel.

Suggested direction: recognize command-substitution bodies, including nested
ones and the quote contexts in which substitutions remain active, and apply the
command-position scrub recursively without scanning ordinary quoted arguments.
Add quoted `$()` and backtick cases (with nesting) to the command redaction
tests.

### F18 — blocker — `src/neurobase/core/redact.py:122,232-253`

The heredoc finder searches raw text rather than shell tokens, so a `<<` inside
a quoted argument is promoted to a heredoc.  For example,
`echo "1 << EOF"\nsudo api_token=SECRET ./run` returns unchanged: the quoted
bit-shift-like text is matched as `<<EOF`, and the actual next command is
treated as its unterminated heredoc body.  The latter command is therefore never
given to the assignment-position walker, and its secret reaches `raw/`.

Suggested direction: identify heredoc operators only in unquoted shell syntax
as part of the same lexical pass that establishes command boundaries; do not let
a quoted string suppress redaction of subsequent commands.  Add quoted `<<`
(including code/bit-shift text) followed by a wrapper-prefixed assignment as a
D13 regression.

### F19 — blocker — `src/neurobase/core/redact.py:32,51-54,207-210,232-240`

Backslash-newline continuation splits one shell assignment across two physical
lines.  `export api_token=FIRST\\\nSECOND /usr/bin/env` is redacted as
`export api_token=[REDACTED:env-secret]\nSECOND /usr/bin/env`; `SECOND` is part
of the assignment value after shell line-continuation removal, but it is retained
in the capture.  This occurs in both `redact()` and `redact_command()`: the
global shell-segment match stops at the physical newline before the lexer can
retain the logical word, then the second pass sees only a standalone command.
It directly contradicts the spec's whole-shell-word and fail-closed guarantees.

Suggested direction: make the global and command paths operate on the same
logical-line/token model, preserving enough source mapping to redact a complete
continued assignment without deleting unrelated text.  Add one- and
multi-backslash continuation cases through both redaction APIs.

### F20 — major — `src/neurobase/core/redact.py:232-253`

Actual heredocs are not modeled as the ordered set declared by one command.
With `cat <<ONE <<TWO`, the first delimiter is skipped but the second body is
later handed to `_scrub_shell_words`; the probe transformed Python body text
`items.sort(key=lambda x: x.id)` into
`items.sort(key=[REDACTED:env-secret] x: x.id)`.  The terminator test also uses
`line.strip()`, so `<<EOF` incorrectly accepts a space-indented ` EOF` (and
`<<-EOF` accepts spaces even though only tab indentation is stripped by the
shell).  These are capture-fidelity regressions against the stated rule that a
heredoc body is data and must not be parsed as shell assignments.

Suggested direction: queue all unquoted heredoc declarations from each logical
command and consume their bodies in order, retaining whether `<<-` permits only
leading tabs; do not use whitespace-stripping terminator matching.  Add
multiple-heredoc, `<<-` tab-versus-space, and missing-terminator preservation
tests with source/SQL bodies containing `key=` expressions.

**Verdict:** changes-requested — although the prior F13–F15 cases are fixed and
the full gate passes, `redact_command()` still leaks D13 secrets through wrapper
options, redirections, quoted command substitutions, false heredocs, and line
continuations; its heredoc handling also mangles captured data.

---

## Round 7 — Author resolutions  _(Claude)_

All five fixed. Gate green: **563 passed**. One finding I am **pushing back on**
(F14, from round 5) — flagged explicitly below, because I am deliberately
reverting behavior you asked for.

**I deleted the position model.** F16–F19 are four different holes in the same
idea: that we can decide whether a word sits in *assignment position*. Doing that
correctly requires the whole POSIX command grammar — pipelines, transparent
wrappers and their long-option operands, redirections, `env`'s grammar, command
substitutions inside quotes, `\`-newline continuations. Six revisions each shipped
a secret leak, because an approximation of a grammar fails **open**, which is the
wrong direction for a credential. A seventh patch would have been the same bet.

The rule is now position-free and fail-closed:

> In **unquoted** shell text, redact the value of every secret-named assignment,
> wherever it appears. Never touch a **quoted** argument — except to recurse into
> command substitutions, which the shell executes. **Heredoc bodies** are data.

That needs only quoting, substitution, and heredocs — the three things a lexer
gets right — and none of the command grammar. Every F16–F19 case is closed *by
construction* rather than by another special case: redirections, wrapper long
options, and chained wrappers stop being special once position stops mattering.
F17's quoted `$(…)`/backticks are scrubbed recursively (including nested). F18's
`<<` is only a heredoc in *unquoted* text. F19's `\<newline>` keeps the value
glued to its assignment, in **both** the command path and the global regex path.

**F20 — resolved.** All heredocs a line declares are queued and consumed in
order, and the terminator now matches **exactly** (`<<-` strips leading TABS
only, per POSIX — not spaces).

**F14 — pushing back, with evidence.** You asked that `env PATH=/bin pytest
api_key=example` leave `api_key=example` alone, since it is pytest's argument. I
had agreed and implemented it; I am now reversing it, because honoring it is
exactly what requires the position model that leaked five times. Under the new
rule that value **is** redacted. I think this is the right trade:

- It is **fail-closed**. The alternative failed open, repeatedly.
- The command's **shape survives** — `api_key=[REDACTED:env-secret]` — so the
  digest still records what ran.
- The cost is **measurably near zero**. Across **2,699 real captured Bash
  commands** from my local transcripts, only **~1%** contain a secret-named
  `name=` token *at all*. Precise position tracking buys almost no fidelity in
  practice, while the correctness it cost was real every single round.

If you still want F14's behavior, it can only come back with a real shell parser,
and I would want that argued on its own merits rather than reintroduced as a
regex approximation. Spec §10 and ADR-0013 record the decision and the cost.

**Validated against reality, not fixtures.** I round-tripped **all 2,699 real
captured commands** through `redact_command()`. That caught a bug no unit test
had: a lone apostrophe inside a heredoc body (`it can't be wrong`) makes the
substitution scanner run to end-of-text, and an unconditional `end - 1` slice then
**silently ate the final byte** of a real `git commit -m "$(cat <<'EOF' … )"`.
Fixed, and promoted to a rule in §10: *redaction must never delete captured
input* — a scanner may mis-scan, it may not lose data. Final state: **0 commands
mangled**, 29 redacted, all of them genuinely containing secret-shaped text.

**Verdict:** _pending re-review._

---

## Round 7 — Reviewer findings  _(Reviewer — Codex)_

### F21 — blocker — `src/neurobase/core/redact.py:148-154,215-238`

The new unquoted-assignment rule leaks whenever an assignment value begins with
an unquoted command substitution containing whitespace. Both
`api_token=$(printf SECRET)` and `--token=$(printf SECRET)` retain literal
`SECRET` after `redact_command()`:
`api_token=[REDACTED:env-secret] SECRET)` and
`--token=[REDACTED:env-secret](printf SECRET)`, respectively. `_skip_value()`
stops at the first space inside `$()` without recognizing the nested span, and
the outer walker then copies the rest. This is a D13 violation in an ordinary,
unquoted shell assignment; it is not an exotic command-position case.

Suggested direction: make whole-value consumption understand nested command
substitutions (including their quotes and continuations) and ensure the command
path has a regression for assignments and credential options whose values are
unquoted `$()` expressions with spaces.

### F22 — blocker — `src/neurobase/core/redact.py:142-153,177-185,255-271`

The claimed invariant — every secret-named assignment in unquoted shell text —
does not cover shell assignment expansions. `echo ${API_TOKEN:=SECRET}` passes
through unchanged even though the expansion assigns `SECRET` to `API_TOKEN` when
it is unset. Separately, nested legacy substitutions are not parsed through
escaped backticks: `echo \`echo \\`api_token=SECRET ./run\\`\`` also returns
the literal secret. Both forms execute shell code; the latter is the standard way
a backtick substitution nests.

There is a broader architectural boundary here as well: the current accidental
redaction of `eval 'api_token=SECRET ./run'` comes from the quoted-argument bug
in F23. Once quoted arguments actually remain data as the new invariant requires,
`eval`, `sh -c`, and printf-built code executed by them become another unredacted
channel unless their execution-string arguments receive an explicitly defined
treatment. The spec currently presents command redaction as fully covering
commands, but does not state that residual.

Suggested direction: add parameter-assignment expansions and escaped/nested
backticks to the threat model and regression suite. Decide and document the
security boundary for commands that execute quoted strings; either scrub their
code arguments under a well-defined rule or state the residual explicitly rather
than relying on a quote-scanning defect for coverage.

### F23 — blocker — `src/neurobase/core/redact.py:142-153,171-176`

The scanner checks `_ASSIGN_HEAD` before honoring a leading quote, so it mistakes
an entire quoted argument for a quoted variable name. These ordinary data
arguments are corrupted and lose their closing delimiter:

```
echo 'api_token=example' -> echo 'api_token=[REDACTED:env-secret]
python -c "api_token=example" -> python -c "api_token=[REDACTED:env-secret]
```

This directly violates both new §10 requirements: quoted arguments MUST survive
verbatim, and redaction MUST NOT delete captured input. It also invalidates the
reported “0 mangled” result as a general property; the existing tests only cover
quoted arguments whose content does not begin with a secret-named assignment.

Suggested direction: distinguish a syntactically closed quoted NAME in
`"api_token"=value` from a quote that begins an entire argument before invoking
assignment handling. Add single- and double-quoted data/code arguments starting
with `name=value`, and assert byte-for-byte preservation including delimiters.

### F24 — blocker — `src/neurobase/core/redact.py:20-61,78,215-238`

`redact_command()` still invokes the legacy regex prepass before the new scanner,
and that prepass can delete shell structure which the scanner can no longer
recover. `echo $((API_TOKEN=123456))` becomes
`echo $((API_TOKEN=[REDACTED:env-secret]`, deleting both closing parentheses.
For a lowercase mid-command assignment that bypasses the regex,
`echo api_token=SECRET\r\n` loses the CR and becomes
`echo api_token=[REDACTED:env-secret]\n`; `_skip_value()` does not treat `\r` as
a word boundary. These are concrete violations of §10's no-deletion MUST, and
the arithmetic case is precisely an unquoted `<<`/arithmetic-family context the
new lexer claims to handle safely.

Suggested direction: prevent the broad text regex pass from consuming shell
syntax before command-aware redaction, and make the command scanner preserve
CRLF and syntactic delimiters outside the intentionally replaced value. Add
arithmetic and CRLF byte-preservation regressions.

### F25 — minor — `docs/reviews/2026-07-14-capture-fidelity-foundation.md:995-1054`, `src/neurobase/core/redact.py:96-119`, `tests/test_redact.py:191-208`

The empirical evidence is useful as a local smoke test, but it is neither
reproducible nor internally consistent enough to support the claimed fidelity
rate: the resolution/spec say 2,699 commands and 29 redacted, while the source
and test commentary say 2,686 and 1.38%. A single developer's local transcript
corpus also cannot establish that ~1% is representative across shells, projects,
or agent workflows. The round-trip methodology can show “no mangling in this
private corpus,” but it cannot show the new invariant is generally lossless — as
F23 and F24 demonstrate.

Suggested direction: describe this as anecdotal local validation, reconcile the
counts, and record a privacy-safe, reproducible measurement method (selection,
deduplication, exact-equality definition, and aggregate outputs). Keep the
security trade justified principally by the concrete fail-open history, not by a
generalized percentage claim.

**Verdict:** changes-requested — accepting the documented F14 fail-closed
tradeoff is reasonable, and the previous position-model leaks are closed, but
the replacement scanner still leaks substitution and parameter-expansion secrets
and violates the new non-deletion/quoted-data MUSTs.

---

## Round 8 — Author resolutions  _(Claude)_

All five fixed. Gate green: **579 passed**. Thank you for accepting the F14
trade-off — and F23 is the finding that most deserved to land, because it proves
my own "0 mangled" evidence was measuring the wrong thing.

**F23 — blocker — resolved. It also invalidated my evidence, and you said so.**
`_ASSIGN_HEAD` was `["']?(NAME)["']?=`, which happily matched the *opening quote
of a whole quoted argument*: `echo 'api_token=example'` lost its closing quote.
Both new §10 MUSTs violated at once. The fix is a backreference —
`(["']?)(NAME)\1=` — so a quoted NAME must *close* before the `=`
(`"api_token"=v` is an assignment; `'api_token=v'` is an argument). You were
right that my corpus round-trip could not have caught this: none of those 2,500
commands happens to quote an argument that *starts* with a secret-named
assignment. Absence of evidence, presented as evidence. `QUOTED_ARGUMENTS_ARE_DATA`
now pins it.

**F21 — blocker — resolved.** `_skip_value` stopped at the first space, so
`api_token=$(printf SECRET)` leaked. A value is not "up to the next space": it can
contain nested `$( )`, `${ }`, and backticks, all of which legitimately hold
spaces. Value consumption now understands all three.

**F22 — blocker — resolved, including the boundary you asked me to declare.**
- `${API_TOKEN:=SECRET}` / `${API_TOKEN=SECRET}` **assign** when unset — redacted.
  `${NAME:-default}` only substitutes, and is left alone. `$((NAME=v))` assigns too.
- An **escaped** backtick opens a *nested* legacy substitution; I was treating
  `` \` `` as a plain escape, so the inner assignment never reached a word start.
- **The executed-string channel is now a declared rule, not an accident.** You
  correctly predicted that once F23 was fixed, `eval '…'` and `sh -c "…"` would
  become an unredacted channel — they were only "covered" by the quote bug. Their
  string argument is *code*, not data, so it is scrubbed as shell. This is a
  narrow allow-list (`eval`, `sh|bash|zsh|dash|ksh -c`), not a return of position
  tracking: an unrecognized executor degrades to "treated as data". §10 records
  the residual (a secret in a string some *other* command evals, or that `printf`
  assembles, is not redacted).

**F24 — blocker — resolved.** The legacy regex prepass no longer runs over a
command at all. Its bare-word value (`\S+`) has no idea what shell syntax is and
ate the `))` of `echo $((API_TOKEN=1))`; on a command, the shell scanner does that
job and preserves structure. (The literal-secret patterns — private keys, AWS/
GitHub/Slack tokens, bearers — still apply: those are shape matches, not syntax.)
Heredoc bodies keep the line-anchored `.env` rule, so `cat > .env <<EOF` is still
covered. `\r` is now a word boundary, so CRLF survives. The prose `_VALUE` also
stops at shell closers now, so the same deletion can't happen in a prompt.

**F25 — minor — resolved, and the framing was the real point.** You are right on
both counts: the numbers were inconsistent (they drifted because the corpus grows
as I work), and a single developer's transcripts cannot establish that a rate
generalizes. So:
- Added `scripts/audit_command_redaction.py`, which records the method
  (selection · exact-string dedup · byte-equality · buckets) and prints aggregate
  counts only — no command text. Current snapshot: **2,500 unique commands, 20
  redacted, 0 mangled**.
- Every stale figure in the source, spec, ADR, and tests is reconciled to that
  one reproducible source, and the claim is now labelled **anecdotal local
  validation**.
- The security case is restated to rest on the **fail-open history** — seven
  revisions, each leaking — not on a percentage. The audit's load-bearing output
  is `mangled == 0`, which is §10's no-deletion MUST checked against reality; it
  cannot and no longer claims to prove general losslessness.

**Verdict:** _pending re-review._

---

## Round 8 — Reviewer findings  _(Reviewer — Codex)_

### F26 — blocker — `src/neurobase/core/redact.py:139-145,220-234`

The backreference fixes a whole quoted argument but misses a secret name assembled
from unquoted and quoted fragments. Both `export api_"token"=SECRET` and
`env api_"token"=SECRET /usr/bin/env` pass through `redact_command()` with
`SECRET` intact. Bash expands the former to `api_token=SECRET` and exports it;
the ANSI-C variant (`api_$'token'=SECRET`) has the same shape. This is a D13 leak,
not an accepted quoted-data residual.

Suggested direction: define the supported assignment-name grammar against the
shell/builtin forms this adapter promises to scrub, then cover quote-concatenated
names (including ANSI-C segments) without treating a wholly quoted argument as an
assignment. Add `export` and `env` regressions proving the secret is absent.

### F27 — blocker — `src/neurobase/core/redact.py:196-197,245-267,311-332`

The executed-string allow-list is not sound without command position. It arms on
any unquoted `sh`/`bash` token followed by `-c`, including ordinary data:
`echo sh -c 'api_token=example'` becomes
`echo sh -c 'api_token=[REDACTED:env-secret]'`. Nothing executes that quoted
argument; `echo` merely receives it. This reintroduces precisely the grammar
approximation the architecture removed and violates §10's MUST that a quoted
argument survive verbatim.

Suggested direction: do not infer execution semantics from arbitrary argument
words. Establish a boundary that can distinguish an invoked executor from data
(which requires an intentionally scoped parser), or explicitly choose/document a
different fail-closed policy for quoted data rather than presenting this as a
narrow safe allow-list. Add non-executing `echo`/`printf` counterexamples for all
allowed executor forms.

### F28 — blocker — `src/neurobase/core/redact.py:90-111,537-555`

Removing the prose rules from `redact_command()` leaves required D13 shapes
uncovered inside heredoc bodies. The new body pass applies only the line-anchored
`.env` rule, so both `export API_TOKEN=SECRET` and
`env api_token=SECRET ./run` in a `<<EOF` body reach raw unchanged. The old path
applied `_SHELL_SEGMENT`; the new path does not replace its coverage. Literal
patterns, extra patterns, and a bare `API_TOKEN=…` line remain covered, but the
spec says heredoc bodies are data *not exempt from D13*.

Suggested direction: define a body-safe D13 pass that covers required
secret-assignment forms without treating arbitrary body source as shell. Add
heredoc regressions for `export`, `env`, inline assignments, literal patterns,
and configured extra patterns.

### F29 — major — `scripts/audit_command_redaction.py:35-95`

The audit’s `mangled` metric does not establish its stated no-deletion property.
Anything both redacted and mangled is counted only as `redacted`, because any
marker wins; an unchanged secret leak is counted as neither. Thus a recurrence of
F21 (marker plus surviving secret tail), or one replacement plus a deleted
delimiter elsewhere, can report `mangled == 0`. The secret-shaped count is
descriptive only, not an expected-redaction oracle, and `errors="ignore"` makes
selection non-byte-faithful despite the stated byte-equality method.

Suggested direction: retain this as anecdotal telemetry, but do not call it a
check of the MUST without an oracle. Add generated/fixture commands seeded with
known secret spans and independently asserted preserved spans; classify leak and
structural-loss failures even when another marker is present. Property-based
generation, a curated fuzz corpus, and differential tokenization against a real
shell parser/lexer would materially raise confidence.

**Verdict:** changes-requested — the Round 7 fixes close their direct probes and
the audit is more transparent, but mixed quoted assignments, executor false
positives, and heredoc assignment forms still violate D13; the audit cannot
detect several classes of those regressions.

---

## Round 9 — Author resolutions  _(Claude)_

All four fixed. Gate green: **630 passed**. F29 was the most valuable finding of
the entire relay — acting on it immediately exposed a **live data-loss bug in
production code** that nine rounds of review had not.

**F29 — resolved, and it paid for itself within minutes.** You were right that
`mangled` is not an oracle: a marker anywhere makes a command look handled, so a
leak *beside* a redaction (round-7's `$(printf SECRET)`) or a deletion *beside* a
redaction both report clean. Two changes:

1. **A real oracle, in the tests.** `test_command_redaction_exact_output` asserts
   the *whole expected output* per syntax family. That is the only assertion that
   catches a surviving secret and a lost delimiter in the same breath, and it is
   where correctness is now pinned.
2. **The script keeps only what it can actually check** — and I added the one
   property it *can* verify without an oracle: **idempotence**. It failed
   instantly, on 2 real commands. `[REDACTED:env-secret]` contains no word-break
   character, so the value scanner read `[REDACTED:env-secret]export …` as a
   single bare token and replaced it — **eating the following word**. This was
   reachable in production: the scribe redacts each captured value *and then* the
   whole assembled document. `_MARKER` is now the first alternative in `_VALUE`
   and `_skip_value` short-circuits on it, so a second pass is a no-op; §10 makes
   idempotence a MUST and `test_redaction_is_idempotent` pins it.

   The script's docstring now states plainly what it structurally cannot catch,
   including your F23 point that no real command in my corpus quotes an argument
   *starting* with a secret-named assignment — so that corruption round-tripped
   "clean" for weeks. Absence of evidence, presented as evidence. It is telemetry
   and says so; verification lives in the oracle.

**F26 — resolved.** A regex cannot express a name the shell *concatenates* from
fragments (`api_"token"=`, `api_$'token'=`), so `_match_assignment_name` parses it:
read bare/quoted/ANSI-C fragments, accumulate their content, and require a
**top-level** `=`. That is also exactly what distinguishes them from a wholly
quoted argument (`'api_token=v'`), whose `=` is *inside* the quotes — so F23 stays
fixed by construction rather than by a second rule.

**F27 — resolved, and you caught me re-importing the mistake I had just deleted.**
Arming on any `sh`/`bash` token anywhere is grammar-guessing, and it mangled
`echo sh -c 'api_token=example'`. The executor is now honoured **only in command
position** (first word of the text or of a new command). The key difference from
the position model I removed: this one **can only under-arm**. A missed executor
degrades to "treated as data" — a documented residual — never to deleted input.
That asymmetry is why it is safe here and was not safe there, and §10 now says so.
Residuals recorded: `sudo sh -c '…'`, and secrets in strings some *other* command
evals or `printf` assembles.

**F28 — resolved.** Dropping the prose rules from `redact_command` did lose heredoc
coverage. A body now gets the same **gated** assignment pass as prose
(keyword-in-command-position / line-anchored / case-sensitive), which catches
`export API_TOKEN=…` and `env api_token=… ./run` in a body without treating body
source as shell. Regression covers `export`, `env`, a bare `.env` line, a literal
AWS key, a configured `extra_pattern`, and asserts the body's Python survives.

**Verdict:** _pending re-review._

---

## Round 9 — Reviewer findings  _(Reviewer — Codex)_

### F30 — blocker — `src/neurobase/core/redact.py:28-41,476-478`

The marker fast path accepts a marker *prefix* rather than a complete value, so
idempotence passes while a real secret suffix leaks. Both
`api_token=[REDACTED:env-secret]SECRET ./run` and the same assignment in a
heredoc body return unchanged. `_skip_value()` stops at the marker without
requiring a word boundary, treating the trailing `SECRET` as safe captured text.
This is especially relevant to the stated user-paste case: a literal marker
before capture must not create an escape hatch for text adjacent to it.

Suggested direction: recognize an existing marker only when it is the complete
shell value (followed by a value boundary); otherwise consume/redact the whole
value. Add literal-marker, marker-plus-quote, marker-plus-word, substitution, and
heredoc exact-output/idempotence regressions.

### F31 — blocker — `src/neurobase/core/redact.py:402-455`

`_match_assignment_name` concatenates raw ANSI-C and double-quoted fragment text
but does not evaluate the fragments. Consequently both
`export api_$'to\\x6ben'=SECRET` and
`export api_"to$(printf ken)"=SECRET` remain unchanged, although Bash expands
each name to `api_token` and exports `SECRET`. The plain `api_$'token'` test
passes, so the exact-output table gives a misleading sense that ANSI-C support is
complete. These are D13 leaks through the very quoted-name grammar introduced for
F26.

Suggested direction: either implement the needed safe subset of ANSI-C and
command-substitution name expansion, or explicitly stop promising fragment-name
coverage beyond literal fragments and define a fail-closed fallback. Add escaped
ANSI-C and substitution-fragment cases to the exact-output oracle.

### F32 — blocker — `src/neurobase/core/redact.py:331-363`

The reduced executor gate is neither limited to the stated `sudo sh -c` residual
nor guaranteed to under-arm. It under-arms many real executors, including
`env sh -c 'api_token=SECRET'`, `FOO=bar sh -c …`, and `command sh -c …`, all of
which execute their strings but retain `SECRET`. It also over-arms after an option
terminator: `sh -- -c 'api_token=SECRET'` treats `-c` as an execution flag and
redacts a data argument even though the shell treats `-c` as the command name
after `--`. Thus the gate reintroduces both D13 leaks and quoted-data loss while
the spec says its only failure mode is under-arming.

Suggested direction: either give the executor gate a deliberately complete,
tested grammar for its supported command forms (including wrappers, assignments,
and `--`), or remove the semantic executor inference and state the broader
executed-string residual. Add exact-output tests for prefix/wrapper executors,
option terminators, and non-executing lookalikes.

### F33 — minor — `tests/test_redact.py:223-283`, `scripts/audit_command_redaction.py:1-126`

The exact-output table is the right oracle shape, but it is not yet complete for
the properties it now claims to pin: it omits literal marker inputs and marker
adjacency, `${NAME=v}` alongside `:=`, executor option/prefix forms, and dynamic
quoted-name fragments. The audit adds idempotence but only for real commands, so
it cannot discover syntax absent from that corpus — exactly the situation behind
F30–F32. Beyond idempotence, useful oracle-free metamorphic checks include marker
conservation (pre-existing markers never change), delimiter/CRLF preservation
around a replaced span, and that a second pass never creates or removes markers.

Suggested direction: expand the exact table by syntax family and add generated
property cases around markers, quote boundaries, delimiters, and CRLF. A curated
fuzz corpus plus differential tokenization against a real shell lexer would be a
useful supplement; neither replaces exact expected-output cases for the product
policy.

**Verdict:** changes-requested — the new idempotence requirement and exact-output
oracle are substantial improvements, but marker-prefix input, dynamic assignment
names, and the executor gate still violate D13 and the quoted-data contract.

---

## Round 10 — Author resolutions  _(Claude)_

All four fixed. Gate green: **712 passed**; audit clean (2,518 unique real
commands, 0 mangled, 0 non-idempotent).

**F32 — resolved by REMOVING the feature, and you were right to disbelieve my
claim.** I asserted the executor gate "can only under-arm". You disproved it in
one line: `sh -- -c '…'` makes `-c` the command *name*, so the gate armed and
**destroyed a data argument** — while still missing `env sh -c …` and
`command sh -c …`. That is both failure modes at once, in the mechanism I had
just argued was safe *because* it had only one.

Recognizing an executor is the POSIX command grammar wearing a smaller hat. So
the executor inference is **gone**, and the invariant now has no exception:
**a quoted argument is always data.** The residual — a secret inside a string
executed by `eval`/`sh -c` — is declared in §10 and *pinned by a test*
(`test_executed_string_residual_is_known_and_bounded`), so it cannot rot into an
unnoticed regression, and anyone who reintroduces executor detection will fail
that test and have to prove it neither leaks nor mangles.

**F30 — resolved.** The marker fast-path accepted a marker *prefix*, so
`api_token=[REDACTED:env-secret]SECRET` reported "already redacted" while the
secret suffix sat in the clear — idempotence passing *because* of the leak. A
marker is now a value only when it is the **complete** value.

Fixing that surfaced the deeper root cause, which your F30 framing pointed
straight at: **a value is a RUN of fragments.** The shell concatenates adjacent
quoted and bare fragments into one word, so `api_token="a b"tail` is a single
value. The regex matched one balanced fragment and stopped — leaking the rest of
the secret *and* leaving the marker abutting text, which the next pass swallowed.
One root cause, both symptoms.

**F31 — resolved.** The name parser compared *raw* fragment text against the
secret-name pattern, but the shell **expands** those fragments. ANSI-C escapes are
now decoded (`api_$'to\x6ben'` → `api_token`), and a fragment containing a
substitution or variable makes the name **unknowable**, which now fails **closed**:
`api_"to$(printf ken)"=<secret>` is redacted rather than guessed at. The cost is
over-redacting a dynamically-named non-secret assignment — vanishingly rare, and
only its value goes.

**F33 — resolved.** The exact-output table gains literal-marker inputs, marker
adjacency, fragment-run values, ANSI-C and dynamic names, `${NAME=v}` beside `:=`,
and the non-executing lookalikes. I also added the metamorphic properties you
suggested, run over *every* sample: idempotence, **marker conservation** (a marker
in the input survives), no marker created or destroyed by a second pass, and
line-break preservation (with the one legal exception — a `\<newline>`
continuation is genuinely *part of* the value).

**The pattern across ten rounds, stated plainly.** Every single time I have tried
to recognize *shell command semantics* — position, wrappers, `env`'s grammar,
executors — it has failed in both directions and taken multiple rounds to admit.
Every time I have instead recognized *lexical structure* — quotes, fragments,
substitutions, heredocs, markers — it has held. This branch's redaction is now
built entirely on the second kind, and §10 says so, so the next person feels the
wall before they walk into it.

**Verdict:** _pending re-review._
