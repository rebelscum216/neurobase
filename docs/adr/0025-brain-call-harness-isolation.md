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
| `--setting-sources ""` | no **user/project/local** CLAUDE.md, skills, plugins, hooks — not the managed scope, see *Scope* below |
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

## Scope — unmanaged installations only

`--setting-sources` selects among `user`, `project` and `local`. It cannot
deselect enterprise-**managed** settings, which outrank all three, cannot be
overridden from the command line, and may carry hooks, force-enabled plugins and
a policy `CLAUDE.md` that load into the brain call regardless. In `-p` mode
managed hooks apply without an approval prompt.

So this decision's guarantee is: **on an unmanaged installation, the brain call
carries no MCP, no settings-derived context and no tools.** On a managed
installation it is weaker by an amount this project cannot measure from outside,
and G5's refusal fix is correspondingly unverified there. The live evidence below
was gathered on a machine with no managed policy of any shape and cannot speak
to that case.

`doctor`'s `brain isolation` check reports **file-based** managed policy — the
base `managed-settings.json`, `managed-settings.d/` fragments, and the
organization-wide `CLAUDE.md`, per OS — and only when `claude-cli` is the
resolved backend. The org `CLAUDE.md` is a distinct channel from the settings
JSON's `claudeMd` key: not excludable, and delivered to the model as a user
message, so it is the shape most able to reproduce G5 (review F8). It deliberately does **not**
fail closed: a managed policy is usually harmless, and refusing every brain call
on a managed machine would disable curation entirely to prevent a hazard that may
not be present (spec §10/D26 — doctor reports, never refuses).

**What that check cannot do** (review F7): managed policy also reaches Claude
Code by server-delivered/cached settings, macOS managed preferences and Windows
registry policy, none of which a filesystem probe can observe, and no
non-interactive command reports effective managed state. So the check reports
*evidence*, never a conclusion — its green line names the directories inspected
and the channels it could not inspect, and never claims "fully isolated". Treat
it as "none of those three file shapes are present here", not "no managed
policy". Closing the
gap properly requires an isolation boundary managed policy cannot populate — a
container or equivalent — which is a larger change than this decision.

## Consequences

- **CLI coupling is now real and must be stated.** These four flags are the
  contract as of **Claude Code 2.1.218**. A future CLI that renames or drops one
  should fail loudly rather than silently restore the harness. There is no
  runtime capability check today; that is a known limitation, not an oversight.
- **The guarantee is scoped, not absolute** (see *Scope*). Any statement of it
  elsewhere — code comment, test, `known-gaps.md` — must carry the same scope, or
  it is an overclaim.
- **Headless brain calls no longer inherit user configuration.** Intended, and
  parallel to Codex, but a real behavioral change: anyone who configured
  MCP-dependent behavior for headless calls loses it with no warning.
- **Defense in depth for the 2026-07-17 runaway.** A brain call with no MCP, no
  hooks and no tools has very little machinery available to spawn anything.
- **`_plan_request_bytes` still budgets via `combine_prompt`** while this backend
  now passes system and user as separate argv entries. Still conservative (the
  sum is near-identical) but no longer exact for one backend. Left alone
  deliberately rather than perturb `ONE_RAW_PER_BATCH`, which the fence had
  already forced from 1380 to 2724, and F3's correction to 2839.
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

**End-to-end confirmation (2026-07-29).** `curate --if-stale` then ran to
completion on the live store with this decision's code: `status: ok`, 40 raws
consumed in 2 batches, 6 upserts, node refreshed, 3m11s, and `doctor`'s
`curate health` green for the first time since 2026-07-16. Two batches for 40
raws means the first filled the 262,144-byte cap, so a near-full-size plan
request planned cleanly — the size at which the original symptom appeared. The
pass was cheap (23 distill digests were already cached) and drained 40 of 362
raws, so it is confirmation, not a throughput measurement.

What this still does **not** establish, recorded so nobody reads more into it:

- **The nondeterminism is unexplained.** In the *unstripped* state, identical
  repeated calls returned different envelopes: sometimes a refusal string,
  sometimes `is_error: true`, sometimes a `None` result, roughly half and half.
  Whether the empty-result mode is a refusal failing to serialize or a third
  distinct defect is unknown. It did not recur in the end-to-end pass, across
  only 8 brain calls. It may be unrelated to this decision entirely — and it is
  the reason G5 stays open.
- **Nothing about managed installations.** All evidence was gathered on a machine
  with no managed policy of any shape; see *Scope*.

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
