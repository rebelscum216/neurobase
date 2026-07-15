# S-cf4 — real `claude -p` argv ceiling on macOS

Date: 2026-07-15
Series: capture-fidelity (Part II plan §C). Feeds the Phase C ADR (A3/A4
payload defaults). Companion: [S-cf5](S-cf5-distill-quality.md).

## Question

The curator passes the whole plan payload as **one argv string** to `claude -p`
(`brain/claude_cli.py`: `cmd = ["claude", "-p", prompt, …]`). Copying a multi-MB
transcript into that slot fails with `E2BIG` at `execve` time. What is the real
single-arg ceiling on this machine, and does `PLAN_PAYLOAD_MAX_CHARS = 300_000`
(plan §A4) sit safely under it with headroom for the environment?

**Exit criterion:** an empirically measured ceiling, and a stated safety margin
for the 300 K default.

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
- `PLAN_PAYLOAD_MAX_CHARS = 300_000` sits **~3.4× under** the worst measured
  ceiling. Even a pathological 64 KB environment leaves the 300 K payload with
  well over 600 KB of slack.

## Findings for the contract (A3/A4)

1. **300 K is a safe, conservative default.** ~3.4× margin absorbs env growth,
   argv-pointer overhead, and the `-p`/`--output-format`/`--max-turns` flags.
2. **The cap is measured in *bytes*, not chars — already correct.** Phase B's
   batching closes a batch on `_plan_request_bytes(payload) <= max_bytes`
   (`curator/engine.py:_next_plan_batch`), so a multibyte-UTF-8 payload cannot
   silently blow past a char-counted cap. The plan text says "300000 chars";
   the implemented budget is bytes, which is the safe interpretation. Keep it
   byte-based and describe the default in bytes in the spec.
3. **`DISTILL_CHUNK_CHARS = 200_000` is comfortable** for the same reason — each
   chunk is a separate `claude -p` arg well under the ceiling. (Chunk sizing is
   really about model context, not argv; see S-cf5.)
4. **The ceiling is env-dependent, so keep the margin.** A user with a very
   large exported environment shrinks it; do not raise the default toward the
   measured max. 300 K stays.

## Result

S-cf4 **closed**. Measured ceiling ≈ 1.02–1.05 MB; `PLAN_PAYLOAD_MAX_CHARS =
300_000` (bytes) is safe with ~3.4× headroom. No change to the Phase B batching
default. Folds into the Phase C ADR as the payload-sizing rationale.
