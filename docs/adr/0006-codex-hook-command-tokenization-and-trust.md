# ADR-0006: Codex hooks — string-with-args `command`, stdin JSON payload, trust re-fires on edit

- **Status:** Accepted
- **Date:** 2026-07-08
- **Resolves:** Phase 5-init installer open question (how `init --agent codex`
  must shape a `hooks.json` handler + what the user must be told)
- **Supersedes:** none

## Context

Phase 5-init writes Codex's hook config (spec §7). ADR-0001 (S1) and ADR-0005
(S2) pinned rollout capture and injection, and confirmed two installer-critical
facts: a project-scoped `hooks.json` is only discovered if `~/.codex/config.toml`
has `[projects."<repo>"] hooks = ".codex/hooks.json"` (+ `trust_level =
"trusted"`), and Codex's canonical on-disk event casing is CamelCase
(`SessionStart`, `Stop`). Three things those ADRs did **not** nail down were
needed before writing the installer:

1. **The `command` shape.** Claude Code takes a `command` *string* and tokenizes
   it (`<shim>/neurobase hook claude session-end`). Does Codex do the same, or
   does it want an argv **array** (`["<shim>", "hook", "codex", "stop"]`)? Guess
   wrong and the hook either never runs or runs with the wrong argv.
2. **How the payload is delivered** to the handler (stdin vs argv) and its exact
   fields — the scribe reads `transcript_path` as the rollout path, so this must
   be confirmed, not assumed.
3. **Trust-gate behavior on re-install.** Does editing an already-trusted
   `hooks.json` silently keep running the old (now-modified) command, or does
   Codex re-gate it? This determines whether the installer must warn the user.

Exit criterion: run a real interactive `codex` session against a hand-written
`hooks.json` in a trusted scratch repo, with the handler pointing at a logger
shim, and read the raw rollout + `~/.codex/config.toml` afterward.

## Decision

Observed in the live spike (2026-07-08, user ran interactive `codex` in the
surviving S2 probe repo; logger shim in place of `neurobase`):

- **Codex tokenizes a `command` STRING with its args**, exactly like Claude. A
  handler `"command": "<abs>/logger.sh hook codex session-start"` fired the
  logger with `arg1=hook arg2=codex arg3=session-start`. **No argv array or
  wrapper is needed.** So the installer writes the same string form as the
  Claude installer: `"command": "<abs-shim>/neurobase hook codex session-start"`
  (and `... stop`), absolute shim path (D4).
- **The payload arrives as stdin JSON** with the spec §7 fields:
  `{session_id, transcript_path, cwd, hook_event_name, model, permission_mode,
  source}`. `transcript_path` **is the rollout path** — confirming
  `_hook_codex_stop` reading `payload["transcript_path"]` (spec §5) is correct.
- **The trust gate re-fires on any `hooks.json` edit.** Editing the file changed
  its `trusted_hash`; Codex re-prompted for approval on next launch, and
  approving wrote a fresh `trusted_hash` under `[hooks.state]`. A re-`init` (or
  any hand-edit) therefore does **not** silently take effect — the user must
  re-approve. The installer MUST tell the user this; `doctor`'s untrusted-hook
  check is deferred to Phase 6 (spec §7).

This is consistent with ADR-0001 (capture wiring / notify argv) and ADR-0005
(injection reaches the model, CamelCase canonical casing, `hooks=` discovery
requirement). Together they fully specify what `init --agent codex` writes.

## Consequences

- `adapters/codex/install.py` writes `hooks.json` with the Claude-mirrored
  string-command form, CamelCase events, and absolute shim; ownership is fenced
  by a path-anchored regex requiring the `hook codex` subcommand (so it never
  claims a prose mention or the Claude `hook claude` handler).
- For **project** scope the installer surgically merges the
  `[projects."<repo-abs-path>"]` table in `~/.codex/config.toml`
  (`trust_level = "trusted"` + `hooks = ".codex/hooks.json"`), preserving
  comments/other tables/other keys — a full `tomllib`→`tomli_w` round-trip was
  rejected because it strips comments and reorders the user's real Codex config.
  **User** scope (`~/.codex/hooks.json`) is global and auto-discovered, so it
  writes no config table.
- `init --agent codex` prints a **trust-gate reminder** ("approve the hook in
  Codex before it takes effect") after writing, because the edit invalidates the
  trust hash.
- The `notify` legacy fallback and the `AGENTS.override.md` injection fallback
  remain **documented only** (spec §5) — not auto-installed.
- The live `init --agent codex` run + real cross-agent demo against the user's
  actual `~/.codex` config is outward-facing and deferred to the user.

## Alternatives considered

- **Write an argv-array `command`** (`["<shim>", "hook", "codex", "stop"]`) —
  rejected; the spike showed Codex tokenizes the string form and fires the args
  correctly, and the string form mirrors the proven Claude installer (one code
  shape for both adapters).
- **Full TOML round-trip via `tomli_w` to add the projects table** — rejected;
  it would strip the comments and reorder the tables in a user's real
  `config.toml`, violating the consent-first "least surprise" rule (spec §7).
  A surgical, line-level text edit (validated by re-parsing the result) keeps
  everything else byte-for-byte.
- **Skip the trust-gate warning and rely on the user noticing Codex's prompt** —
  rejected; the whole point of consent-first install is no surprises. If the
  hook silently didn't fire because the user dismissed an unexplained trust
  prompt, the tool would look broken.
