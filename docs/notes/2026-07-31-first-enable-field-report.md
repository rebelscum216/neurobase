# First enable on a repo with history — field report (2026-07-31)

This note records the first `neurobase enable` on an existing repo with many
past sessions. The repo is `Project-A` (project slug `project-a`). The install
was already complete at user scope. Only the project registration was missing.

The note has two parts. Part 1 records what the session did. Part 2 records what
the session measured and found. One defect needs a fix. The other items are
measurements. Use them to size expectations and to write better probes.

Author: a Claude session working in `~/AI Projects/Project-A`.
Store used: `~/neurobase` (schema 1). Brain: `claude-cli`.

**Neutralized for publication:** the repo is an internal work project, published here as
`Project-A` / `project-a`, and the account name appears as `first.last` / `first-last`.
⚠️ **The dot-vs-dash and space-vs-dash differences in the paths below are NOT artifacts of
that substitution — they are the defect** (the encoding mismatch filed as `G9`). The
placeholder was chosen to carry a dot precisely so the mismatch survives publication.

---

## Part 1 — what the session did

1. **Enabled the project.** `neurobase enable` registered slug `project-a` and
   made the memory tree. `doctor` was already green for the shim, the store, the
   Claude hooks, the Codex hooks, the trust hashes, and both MCP registrations.
2. **Verified the Codex path end to end.** See Finding 5 for the method and for
   the reason a config check is not sufficient.
3. **Seeded 20 curated facts** from the Claude auto-memory directory. The first
   attempt imported nothing. See Finding 1.
4. **Curated 7 raw captures.** The result was 1 upsert, 0 tombstones, 21 active
   facts, and 1 node. The pass took 964 seconds.

Final state: 0 unconsumed raw, 7 consumed raw, 21 active facts, 1 node.

---

## Part 2 — findings

### Finding 1 (defect) — `claude_memory_dir` under-encodes the project path

`seed.claude_memory_dir()` makes the auto-memory path with this rule:

```python
encoded = project_root.as_posix().replace("/", "-")
```

Claude Code also replaces a dot and a space with a hyphen. Therefore the two
paths do not agree when the project path contains a dot or a space.

| Source | Path |
|---|---|
| Neurobase makes | `~/.claude/projects/-Users-first.last-AI Projects-Project-A/memory` |
| Claude Code uses | `~/.claude/projects/-Users-first-last-AI-Projects-Project-A/memory` |

The first path does not exist. The second path holds 21 files.

**The failure is silent.** `import_from_claude_memory()` treats a missing
directory as a normal condition and returns an empty `SeedResult`. Therefore
`neurobase seed --from-claude-memory` prints `{"imported": [], "unchanged": [],
"skipped": []}` and exits 0. The command reports success and imports nothing.

**Scope.** The defect applies to every project whose path contains a dot or a
space. On this machine the user name is `first.last`. Therefore the defect
applies to almost every project.

**Workaround.** Give the true path to `--from-dir`. This import wrote all 20
facts:

```
neurobase seed --from-dir "$HOME/.claude/projects/-Users-first-last-AI-Projects-Project-A/memory"
```

**Fix direction.** Derive the encoding rule from the behavior of Claude Code.
Do not guess the rule. Add a test that uses a path with a dot and a space. Also
make the missing-directory condition visible. A caller that asks for
`--from-claude-memory` expects an import. Report the directory that the code
looked for when the import is empty.

This defect belongs in `docs/known-gaps.md`. This session did not edit that file,
because the file is shared. The owning session must make that edit.

### Finding 2 — first-run cost on a repo with history

A first curate pass on a repo with history is a large one-time job. Measured
numbers for 7 captures:

| Item | Value |
|---|---|
| Raw session transcripts | 15,496,478 bytes |
| Rendered transcripts | 1,914,978 bytes (12.4% of raw) |
| Brain calls | 22 (plus 1 `claude --version`) |
| Duration of each brain call | 35–50 s |
| Total duration | 964 s (16.1 min) |
| Result | 1 upsert, 0 tombstones, 21 facts, 1 node |

The steady state is different. One capture needs 3 calls and 65 seconds. The
cost above is a backlog cost, and it occurs one time for each repo.

The brain timeout is not the constraint. `DEFAULT_TIMEOUT_SECONDS` is 120 s, and
each call finished in 35–50 s. No call retried. The constraint is the total wall
time of the command. Two runs at 300 s and 600 s were killed before the end. A
1800 s wall was sufficient.

**Suggested action.** Tell the user the size of the backlog before a first pass
starts. Print the count of unconsumed raw and an estimate of the duration.

### Finding 3 — estimate from the rendered size, never from the raw size

`render_transcript()` reduces each tool call to one line. The rendered text was
12.4% of the raw text in this sample, and the ratio held between 10.5% and 13.7%
across all 7 transcripts.

An estimate that uses the raw size gives about twice the true number of chunks.
This session made that error and reported a wrong call count.

`DISTILL_CHUNK_CHARS` is 200,000, and `MAX_DISTILL_CHUNKS` is 5. The largest
transcript in this sample rendered to 493,948 characters. That size is 3 chunks.
Therefore the chunk cap never applied, and no middle chunk was dropped.

### Finding 4 — what makes a session transcript large

A session transcript holds every tool call and every tool result. The table
below describes the largest transcript in this sample: 3,876,894 bytes over
1,441 records.

| Share | Bytes | Records | Content |
|---|---|---|---|
| 44.4% | 1,719,657 | 403 | `user:tool_result` |
| 23.8% | 924,225 | 403 | `assistant:tool_use` |
| 14.0% | 542,645 | 152 | `assistant:thinking` |
| 7.4% | 287,192 | 174 | `assistant:text` |
| 6.1% | 235,752 | 87 | `attachment` |
| **1.6%** | **60,887** | **14** | **`user:text`** |

Tool traffic is 68% of the file. The text that the user typed is 1.6%.

Also, 49.5% of the file is outside the `message` object. The largest field there
is `toolUseResult` at 988,764 bytes. That field holds a second copy of the tool
results. The remainder is per-record data: `sessionId`, `parentUuid`, `cwd`,
`gitBranch`, `uuid`, and `timestamp`. That data costs about 1,330 bytes for each
record.

### Finding 5 — a hook probe can give a false result

Three properties of the hook path make a naive probe unreliable. Use the method
at the end of this finding.

1. **`run_hook` never fails.** The function catches every exception and exits 0.
   Therefore exit 0 does not prove that the hook did work.
2. **`session-start` does not capture.** The event injects memory, and it also
   calls `spawn_curate_if_stale`. The `stop` event does the capture, and it reads
   the agent rollout file. A probe that fires `session-start` and then counts raw
   captures will always find none.
3. **`is_internal_call()` disables the hook.** The function returns true when
   `NEUROBASE_INTERNAL_CALL` is `1`. A probe that runs inside a process that
   Neurobase started does nothing, and it reports nothing.

**Method that works.** Run a real agent session in the repo, then compare the
raw count before and after. This session ran `codex exec` with a known prompt.
The capture that appeared held the matching `session_id`, the correct `cwd`, and
the branch. Those fields prove the origin of the capture.

### Finding 6 — a dry run on a subset does not preview the full plan

A dry run over 1 capture proposed one fact about the `gh` CLI. The full pass over
7 captures did not write that fact. The full pass wrote a different fact, and
that fact came from 3 captures together.

The behavior is reasonable, because a cross-session pattern is worth more than a
single-session detail. Do not present a subset dry run as a preview of the full
plan.

### Finding 7 — a brain child can outlive the parent

`pkill` on a `neurobase curate` process left the `claude -p` child alive. The
child must be stopped separately. Check for a stray `claude -p` process after any
interrupted pass.

### Finding 8 — brain selection is global

No `[brain]` configuration existed on this machine. Therefore `backend` stayed at
`auto`, and the order `claude-cli → codex-cli → anthropic-api → openai-api`
selected `claude-cli`, because `claude` is on the PATH. There is no per-project
brain setting in the store.

---

## Method note

The call counts in this note come from a PATH shim. The shim logged each `claude`
invocation with a timestamp, an argument size, and a duration, and then it called
the real CLI. The measurement of a single capture ran against a copy of the store
in a temporary directory. The live store was not used for that measurement.
