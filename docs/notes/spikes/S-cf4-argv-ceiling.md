# S-cf4 — real `claude -p` argv ceiling on macOS

Date: 2026-07-15
Series: capture-fidelity (Part II plan §C). Feeds the Phase C ADR (A3/A4
payload defaults). Companion: [S-cf5](S-cf5-distill-quality.md).

## Question

The curator passes the whole plan payload as **one argv string** to `claude -p`
(`brain/claude_cli.py`: `cmd = ["claude", "-p", prompt, …]`). Copying a multi-MB
transcript into that slot fails with `E2BIG` at `execve` time. What is the real
single-arg ceiling on this machine, and does the shipped payload cap sit safely
under it with headroom for the environment?

Note: S-cf4 was first cited by [ADR-0012](../../adr/0012-curator-plan-payload-batching.md),
which shipped Phase B batching with `[curate].plan_payload_max_bytes = 262_144`
(256 KiB) — **byte-budgeted, not char-budgeted**. The plan's §A4 draft named
`300_000`; the shipped default is the rounder 262 144. This note records the
independent re-measurement that confirms that default's margin and carries it
into Phase C. Both values are checked below.

**Exit criterion:** an empirically measured ceiling, and a stated safety margin
for the shipped payload cap.

## Method

Binary-search the largest single argv string that a no-op (`/usr/bin/true`,
which accepts any args) can be `execve`'d with before `OSError [Errno 7]`, under
two environments — the minimal probe env, and an env inflated by ~29 KB to mimic
a fat interactive shell (long PATH, many exports, shell functions). The kernel
limit is `execve`-wide (argv + envp + overhead ≤ `ARG_MAX`), so it does not
depend on `claude` itself — `claude` reads whatever argv survives the exec.

Probe: `scratchpad/scf4_argv_probe.py` (reproducible; no LLM, no network).

## Observed

```
ARG_MAX (sysconf)         :  1,048,576
base env bytes            :      2,845
max single arg (min env)  :  1,045,244
added fat env bytes       :     29,456
max single arg (fat env)  :  1,015,340

PLAN_PAYLOAD_MAX_CHARS    :    300,000
headroom under min env    :    745,244  (3.5x)
headroom under fat env    :    715,340  (3.4x)
```

- The ceiling tracks `ARG_MAX` (1 MiB) minus the environment and per-string
  overhead: ~1.045 MB with a tiny env, ~1.015 MB once the env carries ~29 KB.
- The **shipped 262 144-byte** cap sits **~3.9× under** the worst measured
  ceiling; the plan's draft 300 000 sits ~3.4× under. Either is safe — even a
  pathological 64 KB environment leaves both with well over 600 KB of slack.

## Findings for the contract (A3/A4)

1. **The shipped 262 144-byte default is safe and conservative.** ~3.9× margin
   absorbs env growth, argv-pointer overhead, and the
   `-p`/`--output-format`/`--max-turns` flags. No reason to raise it toward the
   plan's 300 000; keep 262 144.
2. **The cap is measured in *bytes*, not chars — already correct.** Phase B's
   batching closes a batch on `_plan_request_bytes(payload) <= max_bytes`
   (`curator/engine.py:_next_plan_batch`), so a multibyte-UTF-8 payload cannot
   silently blow past a char-counted cap. Any spec prose that says "300000
   chars" should be read as the byte-budgeted 262 144 the code enforces.
3. **`DISTILL_CHUNK_CHARS = 200_000` is comfortable** for the same reason — each
   chunk is a separate `claude -p` arg well under the ceiling. (Chunk sizing is
   really about model context, not argv; see S-cf5.)
4. **The ceiling is env-dependent, so keep the margin.** A user with a very
   large exported environment shrinks it; do not raise the default toward the
   measured max.

## Result

S-cf4 **closed** (confirms ADR-0012). Measured ceiling ≈ 1.02–1.05 MB; the
shipped `plan_payload_max_bytes = 262_144` is safe with ~3.9× headroom, and the
budget is correctly byte-based. No default change. The Phase C ADR relies on
this cap unchanged and adds only the per-session distill step above it.
