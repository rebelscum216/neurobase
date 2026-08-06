# Authorial stance vs. compression framing — 2026-08-04

> ⛔ **PARTLY RETRACTED 2026-08-06.** The headline length result (13–22% shorter) **did not
> replicate** and must not be cited. Section coverage did. See
> [the retraction](#-retraction-2026-08-06-the-length-result-did-not-replicate) before
> reading anything below it.

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

## ⛔ RETRACTION (2026-08-06): the length result did not replicate

**Do not cite the 13–22% figure.** The harness was re-run on 2026-08-06 against the same
three sessions, the same frozen-render path, and the same model set. Section coverage
replicated exactly; the length effect did not:

| session | Δ on 2026-08-04 | Δ on 2026-08-06 |
|---|---|---|
| `f4d70ea9` | −13% | **−5%** |
| `d53ef8b6` | −17% | **+2%** (sign reversed) |
| `9822fafc` | −22% | **−31%** |

The rerun also supplies the control this write-up never had — the **same arm** on the
**same frozen input**, twice:

| | `f4d70ea9` | `d53ef8b6` | `9822fafc` |
|---|---|---|---|
| `COMPRESS` drift | +1.6% | **−17.1%** | +1.9% |
| `STANCE` drift | **+11.1%** | +1.8% | −9.3% |

Same-arm variation spans **−17.1% to +11.1%**, which is the size of the effect this
write-up reported. **A between-arm difference of 13–22% is therefore not established by
these data.**

**What was ruled out.** The harness imports `DISTILL_SYSTEM`, `_chunk` and `_fence` from
shipped code, so a change there would have made `COMPRESS` a different arm. Checked: the
only commit touching `curator/distill.py` in the window is `618f7e9` (G11), and its hunks
land on the section-bounding logic and `_distill_one`/`distill_docs` — none of which this
harness calls. The prompt body and the chunking path are byte-identical across the window.
The model set is the same by name in both runs and verified per pair.

**What is NOT ruled out, and matters.** The two 2026-08-04 runs (v1 and v2) agreed with
each other *within that day*, while the 2026-08-06 run disagrees with both. So the
variation may be **between-day drift in how a same-named model is served**, rather than
within-day sampling noise. With two timepoints these cannot be separated. Either reading
kills the same conclusion — a result that moves this much across two days is not a stable
property of the prompt — but the mechanism is open, and a third run on a *third* day would
be the cheap way to tell them apart.

**Consequence.** The line "the cheap win is a prompt change, not an authoring subsystem"
does not follow from this experiment. Length was the only quantitative support for it, and
quality was never measured here or in the rerun. The blind judge remains unrun; at n = 3
with this spread it would be equally underpowered.

## What was observed

Three paired sessions from one project, six generations total:

| session | `COMPRESS` chars | `STANCE` chars | Δ | `STANCE` leads with |
|---|---|---|---|---|
| `f4d70ea9` | 19,790 | 17,156 | **−13%** | Decisions |
| `d53ef8b6` | 30,032 | 25,045 | **−17%** | **Unresolved** |
| `9822fafc` | 32,998 | 25,643 | **−22%** | Decisions |

- ⛔ ~~**Successor-framing produced 13–22% shorter output at identical section coverage.**~~
  **Retracted — see the block above; this did not replicate.** The half that DID replicate
  is the coverage: all six generations emitted 4/4 headings here, and all six did again on
  2026-08-06, so completeness was never the differentiator in either run (12/12 total).
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
- ⛔ **No noise floor was measured, and that is what invalidated the length result.** The
  design specified a distill-vs-distill control precisely to establish run-to-run variance;
  it was never run, so nothing here could distinguish a 13–22% effect from ordinary
  variation. **The 2026-08-06 rerun measured it at −17.1% to +11.1%.** ⭐ The general
  lesson: before believing a measured difference, measure the thing against *itself*.
- **The harness was run twice.** v1 completed but could not verify its own same-model
  guarantee (it discarded the response envelope), so v2 re-ran everything against the
  branch that records usage, constructing a fresh brain per generation. v1's numbers agree
  directionally. ⚠️ **Both were same-day.** Agreement between two runs hours apart was read
  at the time as stability; the 2026-08-06 rerun shows it was not, and leaves open whether
  the variation is within-day noise or between-day drift.
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
