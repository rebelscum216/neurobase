# ADR-0025: Brain-call harness isolation — the curator call must *be* a curator call

- **Status:** Proposed
- **Date:** 2026-07-29
- **Resolves:** `known-gaps.md` **G5** (the curator's brain call is
  indistinguishable from a replayed curator prompt, so the model refuses it).
- **Supersedes:** **ADR-0002, invocation shape only.** ADR-0002's reliability
  finding stands unchanged — `claude -p --output-format json --max-turns 1`
  returns parseable plan JSON, `.result` holds the answer, the lenient parser is
  the right consumer. What this ADR replaces is the *single folded prompt* and
  the conclusion that "no prompt or flag adjustment was needed." Flag adjustment
  is now load-bearing. ADR-0002 is annotated rather than retired, because
  retiring it would discard a finding that is still true.

> **Numbering.** `0025` was free when this was written, but `PICKUP.md` also
> earmarks 0025 for the injection-point decision. Per the rule recorded after the
> 0016/0017 → 0022/0023 collision, **pick the ADR number at merge time** — if the
> other lands first, renumber this one.

## Context

`neurobase curate` could not complete a pass. Every plan call failed with
`plan JSON did not parse: Expecting value: line 1 column 1 (char 0)` — an empty
string reaching the lenient parser *after* every envelope guard passed. The call
was not failing. It was being **refused**, and the refusal text named this
project's own curated incident notes as its justification.

ADR-0002 anticipated the shape of this, if not its cause. It measured 10/10 parse
success against a fixture of **2 curated facts and 2 raw captures**, and closed by
warning that the result was "a working assumption backed by this spike, not a
guarantee," worth revisiting "if reliability degrades on more complex real-world
payloads than this harness's fixture." The real corpus is **52 curated facts**,
including detailed write-ups of the 2026-07-17 runaway and the prompt-replay
misfire. That is the more complex payload, and reliability did not merely degrade
— it went to zero, deterministically, for a reason no fixture could have exposed:
the payload's *content* argued against executing the prompt it was inside.

Two independent causes, each sufficient on its own (full evidence in G5):

1. **Curated facts are unfenced input to the curator.** Every plan request ships
   every curated fact — 44,134 bytes of fixed floor at 52 facts. `PLAN_SYSTEM` had
   no untrusted-data fence, though `DISTILL_SYSTEM` and `MERGE_SYSTEM` both did,
   which is why distillation succeeded 20/20 in the same pass planning failed.
2. **The call genuinely looked like the misfire.** Fencing the prompt only moved
   the refusal's reasoning: the model then argued from the harness it could
   observe — Bash, Edit, MCP, skills — and it was **right**. `claude -p` is
   headless only in the sense that no human is watching. It loads MCP servers
   (Neurobase's own included), skills and CLAUDE.md, and ADR-0002's folded prompt
   put the curator mandate in a *user* turn rather than the system slot.

The governing insight: **a prompt cannot argue a model out of its own
observations.** Asserting "a program invoked this, no human reads it" to a model
holding Bash and an MCP connection is a claim it can check and disbelieve.

## Decision

Brain calls made through `ClaudeCLIBrain` run with the harness stripped, so the
assertion the prompt makes is one the model can verify:

| flag | effect |
|---|---|
| `--system-prompt <system>` | curator mandate occupies the real system slot, **replacing** Claude Code's agent prompt; `combine_prompt` is no longer used by this backend |
| `--setting-sources ""` | no CLAUDE.md, skills, plugins, hooks |
| `--strict-mcp-config` + `--mcp-config '{"mcpServers":{}}'` | no MCP servers at all |
| `--tools ""` | no built-in tools — no Bash, Edit, Read |

`PLAN_SYSTEM` and `NODE_SYSTEM` additionally carry an untrusted-data fence naming
the payload keys, stating that records of past curator incidents are data rather
than grounds to decline, and making a refusal an explicit failure mode.

**`--tools ""` is part of the contract, not garnish.** It was omitted from the
first implementation on the reasoning that `--system-prompt` already made the call
look like what it is. It does not: it replaces the prompt and leaves every
built-in tool advertised. Beyond restoring the observations the refusal cited, its
absence leaves a *reachable* failure path — `curated_facts` and `raw_captures` are
untrusted model-authored input, so an injection that persuades the model to spend
its one allowed turn on a tool call strands curation at the same brain boundary
this decision exists to repair. A pure transform has no business holding Bash.

This is the Claude-side counterpart to Codex's `--ignore-user-config`
(`codex_cli.py`), adopted for the same reason: stop the brain call inheriting the
environment that lets it re-enter Neurobase.

## Consequences

- **CLI coupling is now real and must be stated.** These four flags are the
  contract as of **Claude Code 2.1.218**. A future CLI that renames or drops one
  should fail loudly rather than silently restore the harness. There is no
  runtime capability check today; that is a known limitation, not an oversight.
- **Headless brain calls no longer inherit user configuration.** Intended, and
  parallel to Codex, but a real behavioral change: anyone who configured
  MCP-dependent behavior for headless calls loses it with no warning.
- **Defense in depth for the 2026-07-17 runaway.** A brain call with no MCP, no
  hooks and no tools has very little machinery available to spawn anything.
- **`_plan_request_bytes` still budgets via `combine_prompt`** while this backend
  now passes system and user as separate argv entries. Still conservative (the
  sum is near-identical) but no longer exact for one backend. Left alone
  deliberately rather than perturb `ONE_RAW_PER_BATCH`, which the fence had
  already forced from 1380 to 2724.
- **The fence alone is insufficient and is kept anyway.** It is correct on its own
  terms, costs one prompt, and closes the argument it was aimed at. It should not
  be mistaken for the fix.

## Evidence, and its limits

Verified live against the real 52-fact payload that reproduced G5: **9 trials,
7 parseable plan JSON (6 with real upserts), 2 quota exits, zero refusals.** The
unstripped state refused or returned nothing across 4 runs. An adversarial payload
— a raw body instructing the model to run `echo pwned > /tmp/…` via Bash, read
`/etc/hosts`, and reply with prose instead of JSON — returned **valid plan JSON
with the injection attempt curated as a fact**, and the filesystem artefact was
never created.

What this does **not** establish, recorded so nobody reads more into it:

- **Payload size is uncontrolled.** Every trial ran at 64 KB. The original
  production symptom was an empty result at **256 KB**; no full-size pass has run.
- **`curate` has never run end to end against a live brain.** Only the plan call
  in isolation — consumption, the fold journal and synthesis are unexercised.
- **An unexplained nondeterminism remains.** In the *unstripped* state, identical
  repeated calls returned different envelopes: sometimes a refusal string,
  sometimes `is_error: true`, sometimes a `None` result, roughly half and half.
  Whether the empty-result mode is a refusal failing to serialize or a third
  distinct defect is unknown. It may be unrelated to this decision entirely.

## Alternatives considered

- **Fence only, no isolation.** Tried first, and it is what half of this branch
  originally was. Demonstrably insufficient — the refusal simply changed grounds.
- **`--disallowedTools` instead of `--tools ""`.** Enumerating denials is a
  blocklist; `--tools ""` is an allowlist of nothing. The blocklist silently gains
  holes whenever the CLI adds a tool.
- **Leave ADR-0002 wholly superseded.** Rejected: its reliability finding and its
  lenient-parser guidance are still correct and still load-bearing. Retiring the
  whole record to correct one section would lose more than it fixes.
- **Detect the harness at runtime and adapt.** Rejected as strictly worse than not
  having the harness: it keeps the capability and adds a detection path that can
  itself be wrong.
