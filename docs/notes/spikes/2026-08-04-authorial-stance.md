# Authorial stance vs. compression framing — 2026-08-04

**What this is:** the write-up for [`2026-08-04-stance-test.py`](2026-08-04-stance-test.py),
a one-off harness that asked whether *framing* the distill prompt as "write for your
successor" changes the digest, holding everything else constant. Lab notebook — this
records what was measured and under what conditions, and decides nothing. It is not one
of the numbered build-plan spikes.

## Why this question, and not the one originally asked

The memory-layering work (`../2026-08-01-memory-layering-ideas.md` §10.3) asked whether
*agent-authored* session summaries read better than distilled ones. That comparison
cannot be isolated: holding the input constant means feeding the authoring arm the same
rendered transcript, at which point it is no longer the live session owner and authorship
has been designed out of the experiment.

So this tested the tractable half — **authorial stance as a single variable**:

| arm | system prompt |
|---|---|
| `COMPRESS` | `DISTILL_SYSTEM`, imported not copied, with only the cap number substituted |
| `STANCE` | the same prompt with audience/purpose reframed as a successor handover |

Held constant by construction: one frozen render per session, redacted once and shared
byte-for-byte by both arms; one cap stated to both; chunking and the untrusted-data fence;
the merge prompt (stance-matched, since every session here renders past
`DISTILL_CHUNK_CHARS`, so the final text always comes from the merge step). The harness
writes the exact prompt diff to `PROMPT-DELTA.diff`, so "only stance differs" is auditable
rather than asserted.

**The cap was raised to 25,000 for both arms deliberately.** At the shipped 6,000 the cap
becomes the independent variable — which is exactly what corrupted an earlier comparison
before `G11` was found. See `../../known-gaps.md`.

## What was observed

Three paired sessions from one project, six generations total:

| session | `COMPRESS` chars | `STANCE` chars | Δ | `STANCE` leads with |
|---|---|---|---|---|
| `f4d70ea9` | 19,790 | 17,156 | **−13%** | Decisions |
| `d53ef8b6` | 30,032 | 25,045 | **−17%** | **Unresolved** |
| `9822fafc` | 32,998 | 25,643 | **−22%** | Decisions |

- **Successor-framing produced 13–22% shorter output at identical section coverage.** All
  six generations emitted 4/4 headings, so completeness was never the differentiator.
- **All three pairs were model-verified comparable** — both arms served by the same model
  set (`claude-opus-5[1m]` plus a `claude-haiku-4-5` auxiliary), per-model call counts
  equal to each arm's call count. This is the first such comparison where that is a
  checked fact rather than an assumption, and it is only checkable because per-call model
  attribution shipped in PR #17.
- **Restructuring is real but session-dependent.** `d53ef8b6`'s stance arm led with
  `## Unresolved` — once retitled `## Unresolved (act on these first)`, numbered, with
  sequencing advice — in **both** independent runs; neither other session did so in either
  run. Reproducible per session, not a property of the prompt.

Cost of the run, tokens primary: 22 calls over 6 generations = **154,283 output tokens**
and **959,550 cache-creation tokens**, at a **0.44% cache hit rate** on the first clean
run. Retroactive distill pays a cache-*write* premium on content it never reads back,
because every chunk is unique. The CLI reported $14.29, which is an API list-price
equivalent and not spend.

## Limits of this result — read before citing it

- **It says nothing about authorship.** Both arms are distill. The surviving claim for
  agent-authored summaries is *input access* (the thinking blocks a rendered transcript
  never contains), and that remains unmeasured for output quality.
- **Shorter is not established as better.** Length and section coverage were measured;
  quality was not. No blind judging was run. Note also that any blind judge would need
  normalization first — `[digest truncated]` markers and heading counts can identify an
  arm without reading a word.
- **n = 3 sessions, one project, one model family, one day.**
- **The harness was run twice.** v1 completed but could not verify its own same-model
  guarantee (it discarded the response envelope), so v2 re-ran everything against the
  branch that records usage, constructing a fresh brain per generation. v1's numbers agree
  directionally.
- **One arm failed and was retried** (`f4d70ea9` compress; `claude -p` exited 1 with empty
  stderr). The retry re-read cached content and inflated the cache hit rate to 9.34%,
  which is why 0.44% from the first clean run is the figure of record.
- **The harness's own verification check was wrong at first** and would have failed every
  pair: it asserted one model per arm, but a `claude -p` call legitimately reports two.
  It now compares per-model call counts against the arm's call count. Rewritten and
  checked against three rejection cases before it was allowed to gate anything.

## Running it

Machine-specific by design — it reads live Claude transcripts and constructs a
`ClaudeCLIBrain` directly, so it never touches `~/.config/neurobase/config.toml` or any
parallel session's backend. The transcript directory, project, and session IDs are
hardcoded to the machine it was written on; change them at the top of the file.

```sh
~/.local/share/uv/tools/neurobase-cli/bin/python \
  docs/notes/spikes/2026-08-04-stance-test.py --cap 25000
```

`--session <8-char prefix>` and `--arm compress|stance` narrow a run. Outputs land in
`./stance-test/` as one frontmatter'd doc per arm, plus `PROMPT-DELTA.diff`.

**Provenance.** The file as it actually ran hashed to
`524af5a0e2d41ebd802d868cfeda414b7c2f1539`. The committed copy differs from it by lint
fixes only, applied because `ruff` runs over the whole repo: `timezone.utc` → the
`datetime.UTC` alias (3×), one `f`-prefix dropped from a placeholder-less string, two long
lines wrapped, and `ruff format`'s blank-line and call-wrapping changes. No logic, prompt,
or output-path differences — the numbers above remain attributable to this file.
