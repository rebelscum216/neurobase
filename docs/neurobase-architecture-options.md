# Neurobase — how to build it: researched options & recommendation

Prepared 2026-07-06. Synthesis of a 12-agent research sweep (integration surfaces,
MCP, agent-memory prior art, storage substrates, packaging, scheduling, secrets,
skill-generation prior art, market gap). Sources cited inline. Discovery-phase
input — not a build plan.

> **Product:** a portable, local-first, cross-agent (Claude Code + Codex) memory
> layer that captures sessions, curates a small durable fact set, builds a
> browsable wiki, injects memory into future sessions, and — the differentiator —
> recommends new skills/instructions that improve as it's used. Productizes a
> proven private memory loop whose behavioral contracts are captured in
> `neurobase-spec-appendix.md`.

---

## TL;DR — the recommended stack

| Axis | Recommendation | Why |
|---|---|---|
| **Integration** | **Hooks + MCP (both)** — hooks for *automatic* capture & injection, an MCP **tools** server for *on-demand* recall/wiki/skill-suggestions | They're complementary, not competing: hooks fire automatically, MCP fires when the agent chooses |
| **The "brain"** | **The user's own logged-in CLI, run locally** (`claude -p` / `codex exec`), BYO API key as fallback | Subscription works locally today — but **do not have Neurobase broker anyone's subscription** (ToS) |
| **Packaging** | **Python, installed via `uv tool install neurobase-cli`** | uv is npx-grade *and* auto-bootstraps Python; persistent shim (not ephemeral uvx) because hooks need it; PyPI name `neurobase` is taken |
| **Storage** | **Markdown-on-disk first**; optional rebuildable SQLite (FTS5 + sqlite-vec) index only if/when semantic recall is needed | 2026 benchmarks: grep/keyword rivals or beats vector RAG at this corpus size |
| **Scheduling** | **Schedulerless by default** (curate on session-start hook + explicit `neurobase curate`); optional native scheduler install | No mature cross-platform scheduler lib exists; launchd is the bug budget |
| **Secrets** | `env var → OS keychain → 600 file`, fail-open | The `gh auth` pattern; keychainless machines must not break |
| **Differentiator** | Skill/instruction **recommender** that emits **SKILL.md / AGENTS.md** artifacts, human-in-the-loop | The atomic mechanisms are prior art; the *cross-session aggregation + recommendation into standard formats* is the white space |

---

## Five reframes the research forced (read these first)

1. **It's not hooks *vs* MCP — it's both.** Hooks fire *automatically* on lifecycle
   events (capture at end, inject at start). MCP tools fire only when the agent
   *chooses* to call them (on-demand recall mid-session, the wiki, skill
   suggestions). The official MCP memory server's own weakness is exactly this:
   "no automatic capture — persistence only happens when the model calls the write
   tool" ([modelcontextprotocol/servers memory README]). So use hooks for the
   automatic spine and MCP for the on-demand surface.

2. **The subscription-as-brain idea has a Terms-of-Service wall for a *product*.**
   Technically, `claude -p` still bills to the Pro/Max subscription today (a planned
   2026-06-15 move to separate "Agent SDK credits" was **paused**, per
   [support.claude.com/articles/15036540]). **But** Anthropic's Agent SDK terms
   *"do not allow third party developers to offer claude.ai login or rate limits
   for their products… use the API key authentication methods."* Codex is softer
   but similar — OpenAI *"recommends API key authentication for programmatic…
   workflows."* **Resolution:** Neurobase invokes *the user's own already-logged-in
   CLI on the user's own machine* — it never holds or brokers subscription
   credentials. Framed that way the "free brain" is fine; framed as "Neurobase logs
   into Claude for you" it's a ToS violation. Also flag: the paused billing change
   could return, so don't hard-depend on the subscription economics.

3. **Neither Node nor Python is guaranteed present** just because someone runs a
   coding agent. Claude Code now ships as a native binary (curl installer); Codex is
   a native Rust binary. So a hook that shells out to `node`/`python3` can fail. This
   makes the runtime a real design constraint — and makes **`uv` (which bootstraps
   its own Python)** the cleanest answer, or a compiled binary.

4. **The skill-recommender's atomic parts are already prior art — the aggregation
   is the moat.** Voyager (skill library), AWM (workflow induction), ExpeL (rule
   distillation) all turn experience into reusable artifacts automatically. Worse:
   *Claude Code auto-memory already ships "offer to save a rule when it detects a
   repeated correction."* So the novelty is **not** "learn a rule from a session."
   It's **mining accumulated, cross-session, cross-agent memory to recommend which
   recurring patterns to promote into portable, standardized SKILL.md / AGENTS.md
   artifacts, human-in-the-loop** — which no one ships and Anthropic has only
   telegraphed. Build there.

5. **At your scale, markdown + grep genuinely beats a vector DB — with 2026
   benchmarks behind it.** Amazon Science's "Keyword Search Is All You Need" (arXiv
   2602.23368) hits ~91% of RAG answer-correctness with *zero* vector store and
   beats RAG on FinanceBench; "Is Grep All You Need?" (arXiv 2605.15184) finds
   lexical search beats vector retrieval on long-memory QA for every harness tested.
   ANN indexes buy nothing under ~1M vectors. Don't start with a vector/graph DB.

---

## Option-by-option

### A. Integration mechanism — **recommend: hybrid (hooks + MCP tools)**

| Option | What it is | Pros | Cons |
|---|---|---|---|
| **A1. Hooks only** | Per-agent lifecycle hooks (your current design, generalized) | Automatic capture + injection; deterministic; no model cooperation needed | Per-agent adapters; no on-demand mid-session recall; config is JSON (Claude) vs TOML/`hooks.json` (Codex) |
| **A2. MCP server only** | One server exposing memory as tools | One artifact, broad client reach (both agents + Cursor/Copilot/etc.); on-demand recall | **Not automatic** — model must choose to call; capture is unreliable; this is the official memory server's core flaw |
| **A3. Hybrid ✅** | Hooks do automatic capture (SessionEnd/Stop) + injection (SessionStart); MCP tools expose recall/search/wiki/skill-suggest on demand | Automatic spine + rich on-demand surface; degrades gracefully | Two integration paths to build & maintain |

Facts that decide it:
- **Both agents now have near-identical hook systems** (2026): `SessionStart`,
  `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop`, `PreCompact`… Codex
  reached near-parity ([developers.openai.com/codex/hooks]). Capture + inject are
  viable and roughly symmetric on both.
- **Two real asymmetries to design around:**
  - **Codex has no `SessionEnd`.** Capture on `Stop` / `agent-turn-complete`
    (per-turn, fires repeatedly) and finalize/dedupe. Claude has a clean
    `SessionEnd`.
  - **Context-injection return contract:** Claude's `SessionStart.additionalContext`
    is documented and robust; Codex's equivalent is unconfirmed — but Codex reads
    **AGENTS.md** natively, so on Codex you can inject by writing memory into an
    `AGENTS.md`/`AGENTS.override.md` (a file route Claude lacks — Claude uses
    CLAUDE.md, and does **not** read AGENTS.md natively).
- **Hooks can't be hot-installed.** Claude Code snapshots hooks at session startup;
  editing settings mid-session requires `/hooks` review and takes effect next launch.
  So the installer writes config for the *next* session — reinforcing the
  one-command-installer model, not silent agent self-install.
- **MCP primitive support is uneven** — model everything as **tools** (universal
  across Claude Code, Codex, Cursor, Copilot…); optionally *also* expose resources
  (Claude `@`-mention) and prompts (Claude `/mcp__…` slash command) for nicer UX,
  but never let cross-agent behavior depend on them (Codex is tools-only and can
  even mark a resources/prompts-only server unavailable).

### B. The "brain" (curation/synthesis/recommendation LLM) — **recommend: user-local CLI first, BYO key fallback**

| Option | Pros | Cons |
|---|---|---|
| **B1. User's own CLI, run locally ✅** (`claude -p` / `codex exec`) | No extra cost for the user (rides the sub they already pay); no key to manage | ToS: only OK when it's *the user's* login on *their* machine — Neurobase must not broker it; paused billing change could return; counts against sub rate limits |
| **B2. BYO API key** (default Anthropic; OpenAI/Azure/local) | Officially blessed for automation; guaranteed throughput; unattended-safe | Metered cost; a key to store |
| **B3. Local model** (Ollama) | Fully private, no key, no cloud | Weaker curation quality; setup burden |

Design it as an **execution-backend abstraction** with precedence: *user-CLI →
BYO-key → local model*. Ship B1 + B2; leave B3 as a seam. Crucial guardrail from the
research: **the subscription path must run as the user, locally — Neurobase never
stores or brokers a claude.ai/ChatGPT credential.**

### C. Packaging — **recommend: Python via uv (`uv tool install neurobase-cli`)**

`uvx` is the decisive finding: it's **npx-grade for a Python tool and stronger** —
`uv` installs as a standalone binary that *auto-downloads its own Python* if none is
present ([docs.astral.sh/uv]). So staying in Python costs you **nothing** on install
friction *and* lets you keep the proven implementation contracts (spec appendix)
instead of a TS rewrite.

- **vs Node/npx:** npx presupposes Node; would force a from-scratch rewrite. No.
- **vs a compiled binary** (PyInstaller/Nuitka): the only way to need *zero* runtime
  (not even uv), but per-OS builds, manual distribution, Windows AV false-positives.
  Good as an *optional* later channel, not the primary.
- Install-UX exemplars to copy: `create-next-app`/`shadcn init` (interactive +
  `--yes`), `flutter doctor` (a `neurobase doctor` health check), `pre-commit`
  (idempotent install + real `uninstall` that restores prior hooks). clig.dev's rule
  is law here: *"if you modify configuration that is not your program's, ask for
  consent and tell them exactly what you're doing"* — which is precisely the
  hook-install flow.

### D. Storage — **recommend: markdown-first, optional rebuildable index**

- **Keep your markdown-on-disk substrate** (greppable, git-friendly,
  Obsidian-readable, human-auditable). It *is* the source of truth.
- **Don't add a vector/graph DB** — unjustified at hundreds–low-thousands of facts
  (see reframe #5). ANN earns its keep only near ~1M vectors.
- **Upgrade path when/if semantic recall is wanted:** the "markdown is truth, index
  is a disposable shadow" pattern — a rebuildable **SQLite** file with **FTS5**
  (BM25 keyword) and optionally **sqlite-vec** (brute-force vectors), all in one
  portable file. Add it behind a flag; never make it authoritative.
- **Real cost to budget for:** curation/staleness burden is the documented failure
  mode of markdown memory (index drift, orphaned files, MEMORY.md clipping at 200
  lines). Your curator already fights this — keep "optimize for deletion" central.

### E. Scheduling — **recommend: schedulerless default, optional native install**

- **No mature cross-platform "install a native job" library exists.** You'd hand-roll
  a 3-backend layer (launchd/systemd/schtasks) + fallback; launchd's
  bootstrap/bootout lifecycle is where real tools report breakage; systemd's trap is
  `enable-linger`; Windows is cron→schtasks mapping.
- **What portable tools actually ship:** opportunistic/on-invocation by default,
  optional native scheduler for users who want unattended cadence. So: **curate on a
  SessionStart hook (shelling out) when raw is stale, plus explicit `neurobase
  curate`;** offer `neurobase schedule --install` for the native path. Accept mild
  staleness as the price of zero-permission portability.
- Note: Claude Code SessionStart hooks *inject context* but can't *auto-run an agent
  turn* — so "curate on session start" means the hook invokes your curator process,
  not the model.

### F. Secrets — **recommend: env → keychain → 600 file, fail-open**

The `gh auth` pattern: try `NEUROBASE_API_KEY` env first (CI/containers), then OS
keychain (`keyring` in Python — macOS Keychain / Windows Credential Locker / Linux
Secret Service), then a `~/.config/neurobase/` file at `chmod 600` when no keychain
(headless Linux/WSL). Since the key is *optional*, a missing keychain must never hard-fail.

---

## The differentiator — making the skill-recommender defensible

Prior art (borrow the mechanisms):
- **Voyager** — embedding-indexed skill library, skills composed from simpler ones.
- **AWM** — induces reusable workflows from past trajectories (offline + online).
- **ExpeL** — distills natural-language insights from experience that transfer.
- **DSPy/TextGrad** — metric-driven refinement to *validate* a candidate before promoting it.

What's already shipped (so don't claim it): **Claude Code auto-memory** offers to
save a rule on repeated corrections — single-session, single-agent, correction-triggered.

**Where Neurobase is genuinely novel (build the moat here):**
1. **Cross-session, cross-agent aggregation.** Analyze the *accumulated* memory
   corpus across many sessions and both agents to find recurring patterns — not one
   session's corrections.
2. **Recommendation as a first-class, human-in-the-loop deliverable** — rank
   candidate skills/rules by recurrence × impact and *propose* them for approval
   (research explicitly names "which experiences deserve to become a skill vs rule
   vs stay episodic" as unsolved).
3. **Emit into the emerging standards** — output accepted recommendations as
   **SKILL.md** (Anthropic Agent Skills) and **AGENTS.md** (Linux-Foundation
   cross-agent standard) files, so they're immediately usable, portable, and not
   trapped in a bespoke store.
4. **Define "improves as it's used"** concretely: track whether a promoted
   skill/rule gets used, corrected, or reverted, and feed that signal back into
   future recommendation ranking. That measurable loop is the product's spine.

---

## Competitive positioning — a crowded base, a narrow moat (honest read)

**Demand is proven** (81% of developers report AI data-privacy concern — Stack
Overflow 2025; enterprise code-leak bans; universal "goldfish memory" / re-explaining
pain). **But the memory-and-wiki layer is NOT a green field — it is already
commoditized in free OSS.** Correcting my own earlier framing:

- **basic-memory** is the closest thing to Neurobase's memory+wiki layer *already
  shipped*: local-first, cross-agent via MCP (Claude Code, Codex, Cursor, VS Code,
  ChatGPT), plain **markdown with `[[wikilinks]]`, Obsidian-compatible**, knowledge
  graph. AGPL-3.0 free + a **$15/mo sync tier** (someone already monetizes exactly
  this).
- **Memorix** (~524★) does cross-agent memory *and* auto-generates
  CLAUDE.md/AGENTS.md; **threadctx** does local-first Claude Code+Cursor memory with
  a $9/seat team tier; **mem0 OpenMemory** is the funded incumbent; **Pieces**
  ($19/mo) is local + cross-agent but SQLite, not markdown; a free
  **obsidian-second-brain** skill already spans Claude Code/Codex/Gemini.

Competitive map (paid? · local? · cross-agent? · markdown/Obsidian?):

| Product | Paid | Local-first | Cross-agent | Markdown/Obsidian |
|---|---|---|---|---|
| basic-memory | free + $15/mo sync | ✅ | ✅ (MCP) | ✅ |
| Memorix | free OSS | ✅ | ✅ (MCP) | ✅ (+writes config files) |
| threadctx | free + $9/seat | ✅ | ✅ (MCP) | partial |
| mem0 OpenMemory | free OSS + cloud | ✅ | ✅ (MCP) | ❌ |
| Pieces | free + ~$19/mo | ✅ (SQLite) | ✅ (MCP) | ❌ |
| Mem0 / Zep / Supermemory | $19–$399/mo | mostly cloud | via API | ❌ |

**So what's actually left to own** — this is where to concentrate the build:

1. **The skill/instruction recommender.** Nobody ships cross-session, cross-agent
   pattern-mining that *recommends* promotions into **SKILL.md / AGENTS.md**.
   Claude Code/Cursor/Codex all learn rules *within one session/project*; none
   aggregates across the corpus into portable skills. **This is the wedge.**
2. **Curation quality.** Most OSS memory is append-only raw logs (their documented
   failure = bloat/drift). Your **"optimize for deletion" curator** (ADD/UPDATE/
   DELETE/NOOP, supersession, regenerated nodes) is genuinely better than the
   commodity — but you must *show* it, because the substrate itself is free.
3. **Config-fragmentation normalization** as a bonus (CLAUDE.md ⟷ AGENTS.md ⟷
   .cursorrules drift is a real, heavily-documented pain) — though Memorix already
   partially does this, so it's a feature, not a moat.

**Honest strategic implication:** if this is a *personal* tool, the crowded base
doesn't matter — build the memory loop you want and the recommender on top. If it's
ever a *product*, the raw "markdown memory over MCP" layer is already free, so it must
sell *above* storage — on the **recommender + curation quality** (or sync/mobile like
basic-memory's proven $15/mo). Don't invest the differentiation budget in the memory
substrate; invest it in the recommender.

- **Positioning line (recommender-led, not memory-led):** *"Your coding agents forget
  everything. Neurobase doesn't just remember — it watches how you work across every
  agent and turns your recurring patterns into skills your agents actually use.
  Local-first; your data never leaves your machine."*

---

## Recommended architecture (the whole picture)

```
neurobase/  (Python; `uv tool install neurobase-cli` — persistent shim for hooks)
├── core/            markdown store (spec appendix §1) — raw→curated→nodes→index
│                    optional: rebuildable SQLite (FTS5 + sqlite-vec) shadow index
├── brain/           execution-backend abstraction: user-CLI → BYO-key → local
├── curator/         scheduled/opportunistic curation (spec appendix §2);
│                    "optimize for deletion" — ADD/UPDATE/DELETE/NOOP
├── recommender/     cross-session pattern mining → SKILL.md / AGENTS.md proposals
├── adapters/
│   ├── claude/      SessionEnd capture · SessionStart inject · settings.json (JSON)
│   └── codex/       Stop/notify capture · AGENTS.md inject · config.toml/hooks.json
├── mcp/             MCP *tools* server: recall/search/read-node/recommend-skill
│                    (+ optional resources/prompts for Claude Code's richer UX)
└── cli/             init · doctor · curate · recommend · schedule · uninstall
                     (interactive + --yes; idempotent; reversible; consent-first)
```

Flow: **hooks capture** (auto) → **curator** folds raw into a small durable fact set
(scheduled or opportunistic) → **nodes/wiki** regenerate → **hooks inject** relevant
memory at session start (auto) → **MCP tools** answer on-demand recall + surface
**skill recommendations** → user approves → emitted as **SKILL.md/AGENTS.md**.

---

## Key risks to hold

1. **Subscription-brain ToS** (biggest): safe only as the user's own local CLI login;
   never broker credentials; the paused Anthropic billing change may return.
2. **Cross-agent capture asymmetry:** Codex has no SessionEnd and an unconfirmed
   context-injection contract — verify hands-on; AGENTS.md is the Codex-side inject
   fallback.
3. **Hook install is next-session, consent-gated** — the "ask Claude to set it up"
   UX must route through a user-run, idempotent, reversible installer.
4. **Skill-recommender novelty is narrow** — the defensible part is cross-corpus
   aggregation + standard-format output, not per-session rule learning (already
   shipped). Keep the build focused there.
5. **Curation staleness** — markdown memory silently rots without disciplined
   dedup/supersession; the curator is load-bearing, not optional.

_Sources: official docs (code.claude.com/hooks, developers.openai.com/codex,
modelcontextprotocol.io, docs.astral.sh/uv, keyring, clig.dev); arXiv 2602.23368,
2605.15184, 2305.16291 (Voyager), 2409.07429 (AWM), 2308.10144 (ExpeL); Stack
Overflow 2025 survey; mem0/Letta/Zep/cognee docs. Full source list in the research
transcripts._
