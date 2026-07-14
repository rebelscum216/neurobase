# Security policy

## Trust boundary — read this first

Neurobase is **local-first with zero cloud dependency and zero telemetry,
permanently.** Everything it writes lives under your store root (default
`~/neurobase`), your agent's own config files, or — only when you explicitly
run `neurobase recommend accept` — an accepted `SKILL.md`/`AGENTS.md`/
`CLAUDE.md` artifact at the target you chose: `--target project` writes into
the project's own repo, `--target user` writes to your user-scoped
`~/.claude/skills/`, outside any repo. In every case it's on disk, in local,
inspectable files: markdown for facts/nodes/accepted artifacts, TOML for
config/registry, and JSON/JSONL for backup manifests, the recommender
ledger, and agent hook configs. There is no Neurobase-run server, no
analytics call, and no phone-home of any kind — this isn't a policy promise
layered on top of the code, it's the absence of any network client in the
codebase outside the pieces below.

The only network calls Neurobase's own code can make are the ones you already
made possible yourself:

- The **brain backend** (`claude-cli`, `codex-cli`, or `anthropic-api`) runs
  as *your* logged-in CLI or *your* API key — auto-detected in that order,
  overridable in config. For the API backend, Neurobase's own code *does*
  read your key in memory to authenticate the request (spec §10's
  key-sourcing order: `NEUROBASE_API_KEY` → provider env var → OS keychain →
  none, fail-open to the next backend) — `resolve_api_key()` in
  `brain/anthropic_api.py` is the one place this happens. It is never
  written to the store, logged, or sent anywhere except directly to the
  official Anthropic SDK call it authenticates. The CLI backends
  (`claude-cli`/`codex-cli`) don't even go this far — Neurobase shells out to
  a CLI you're already authenticated with and never touches its credentials
  at all.
- The **MCP server** (`neurobase mcp serve`) speaks **stdio only** — it is
  not a network listener. It's exposed to whatever MCP client (Claude Code,
  Codex CLI, or another tool) launches it as a subprocess, on your machine.

Hooks are **fail-soft, not fail-secure**: every capture/recall hook path is
wrapped so any internal error results in silently capturing or injecting
nothing, then exiting 0. This is a deliberate trade-off — the priority is
"never wedge an agent's session teardown or startup" over "never fail
silently." If you need to confirm a hook actually ran, `neurobase doctor` and
`neurobase status` report real state; don't infer it from the absence of an
error.

## Redaction

Secrets are stripped **before** anything touches disk — not as a later pass,
and not optionally. This applies at every write path that takes free-form
text: Claude/Codex session capture, `neurobase seed` imports, and a
recommender proposal's draft body (redacted again at accept-time, since an
edited draft is a second opportunity to reintroduce something). The patterns
(spec appendix §10, decision D13) are a closed, versioned table — currently:
PEM private keys, AWS access keys, common API-key/token shapes (`sk-`/`rk-`
prefixed, Slack `xox*`, GitHub `ghp_`/`github_pat_`), bearer tokens, and any
`KEY=`/`TOKEN=`/`SECRET=`/`PASSWORD=`/`CREDENTIAL=`-shaped environment line
(name kept, value replaced). You can extend the table via
`[redact] extra_patterns` in config; you cannot narrow it.

Two things worth being explicit about:

- Redaction is **pattern-based, not semantic.** It catches secrets that look
  like the patterns above. It will not catch a password pasted as plain
  prose with no recognizable shape, or a secret embedded in a format the
  table doesn't cover yet. Don't rely on it as your only line of defense if
  you're pasting genuinely sensitive material into a session.
- The redaction table itself is public (it's in this repo) — by design.
  Security through an unpublished pattern list isn't security; the goal is a
  correct, auditable list, not a secret one.

## Consent for anything outside Neurobase's own files

Neurobase never edits your agent config (`settings.json`, `hooks.json`,
`config.toml`) or writes an accepted recommendation — to a project repo or to
your user-scoped `~/.claude/skills/` — without showing the exact diff first,
asking, and backing up the original under
`<store root>/backups/<timestamp>/` with a manifest. Every hook entry
Neurobase creates is **fenced** — tagged as Neurobase-owned so `uninstall`
can remove exactly those entries and nothing else you or another tool wrote.
Recommender output is emitted, never auto-installed: a proposal becomes a
`SKILL.md` or an `AGENTS.md`/`CLAUDE.md` rule block only when you explicitly
run `neurobase recommend accept`.

## Known gap

`G1` in [docs/known-gaps.md](docs/known-gaps.md) documents a real
schema-guard gap: the check that refuses to operate on a store newer than
the running binary is enforced per call site today, not at the store
boundary, and at least one path (`init`'s guided flow) can mutate
`registry.toml` before the guard runs. It's tracked there rather than fixed
silently because the right fix is architectural (see that entry for the
options under consideration) — flagging it here since it's the kind of thing
a security-conscious reader would want to know about up front rather than
discover later.

## Reporting a vulnerability

If you find a security issue, please use
[GitHub's private vulnerability reporting](https://github.com/rebelscum216/neurobase/security/advisories/new)
for this repository rather than opening a public issue. Include what you
found, how to reproduce it, and its impact; we'll acknowledge and follow up
from there. There's no bug bounty — this is a solo-maintained open source
project — but real reports are taken seriously and credited.
