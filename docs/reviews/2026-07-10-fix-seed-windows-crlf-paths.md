---
slug: fix-seed-windows-crlf-paths
status: approved
author: claude
reviewer: codex
branch: fix-seed-windows-crlf-paths
diff: git diff main...HEAD
created: 2026-07-10
---

# Review: fix(seed) — Windows CRLF + path-encoding + config-location bugs

## Brief  _(Author — Claude)_

**Intent.** `main` has been red on `windows-latest` since #6 (workstream B):
6 seed tests fail there while macOS/Linux pass. This is a hotfix for those
pre-existing, platform-specific failures — nothing else. Discovered while
landing workstream C (PR #7), whose own tests are clean on every OS but which
can't merge onto a red `main`.

**Scope.** Branch `fix-seed-windows-crlf-paths`, `git diff main...HEAD`. Two
files:
- `src/neurobase/recommender/seed.py` — CRLF normalization in `_import_tree`;
  `as_posix()` in `claude_memory_dir`.
- `tests/test_cli_seed.py` — isolate `APPDATA` in `_isolate_home`; write the
  extra-redact test's config via `config.config_path()`.

**Three root causes (all in workstream B's code/tests):**
1. **CRLF.** `_split_frontmatter` and the stored body assume `\n`, but the
   importer reads via `read_bytes()` (no universal-newline translation), so a
   `\r\n` file fails the `startswith("---\n")` frontmatter check — dropping the
   frontmatter `name` slug (→ filename fallback) and storing a CRLF body. Fix:
   normalize `\r\n`/`\r` → `\n` right after `decode()`. The dedupe digest still
   hashes the original `raw_bytes`, so idempotency is untouched.
2. **Path encoding.** `claude_memory_dir` did `str(path).replace("/", "-")`,
   which no-ops on Windows backslash paths. Fix: `project_root.as_posix()` so the
   documented "every `/` → `-`" rule (spec §12.3) applies cross-platform.
3. **Config location (test-only).** The extra-redact test wrote config to
   `~/.config/neurobase`, but `config_path()` reads `%APPDATA%` on Windows, so
   the CLI loaded the runner's real config (empty `extra_patterns`) and never
   redacted `zeta-9000`. Fix: `_isolate_home` now also sets `APPDATA` under tmp,
   and the test writes via `config.config_path()` (POSIX path on macOS/Linux,
   APPDATA path on Windows — same file the CLI reads).

**Focus areas.**
- Is normalizing `\r\n`/`\r` → `\n` on the imported body an acceptable reading
  of §12.3's "body verbatim"? (I judged line-ending normalization to be
  content-preserving and clearly intended, since the tests assert `\n` bodies;
  the alternative — fixing only frontmatter detection but keeping CRLF bodies —
  would still fail `test_malformed_frontmatter_falls_back_to_whole_file_as_body`
  on Windows.)
- `as_posix()` on a driveless `Path("/Users/x/...")` → `-Users-x-...` matches the
  test; a real Windows drive path (`C:\...`) would encode as `C:-Users-...`.
  Claude Code's actual on-disk convention on Windows is unverified — this fix is
  faithful to the spec's literal `/`→`-` rule and to the test, not a claim about
  Windows Claude Code layout (`--from-dir` remains the format-agnostic fallback).
- `_isolate_home` now sets `APPDATA` for every test in the file — intended (it
  only tightens isolation; no test relied on the un-isolated APPDATA).

**Known risks / tradeoffs.** CRLF normalization changes stored bodies on Windows
from CRLF to LF — deliberate and consistent with the store's own `\n` document
format. No behavior change on macOS/Linux (files already `\n`, so both fixes are
no-ops there).

**How to verify.**
- `uv run python scripts/ci.py` — green on macOS (393 passed).
- The real confirmation is Windows CI: PR #8's matrix passed all 6 jobs
  (both `windows-latest` py3.11 + py3.13 green), fixing exactly the 6 named
  failures.
- Local Windows-condition simulation: importing explicit `\r\n` bytes yields the
  frontmatter slug + an LF body; `PureWindowsPath("/Users/x/...").as_posix()`
  encodes correctly.

**Out of scope.** Workstream C itself (PR #7, separate review — already approved).
Any broader Windows audit beyond these 6 failures. This PR is a minimal hotfix to
turn `main` green.

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

No findings.

Verification performed:
- Reviewed `git diff main...HEAD` against the brief's three claimed fixes.
- Simulated a CRLF seed source locally: frontmatter slug imported correctly and
  stored body normalized to LF (`'Body\nLine2\n'`).
- Checked the path-encoding behavior the brief calls out:
  `PureWindowsPath(r"C:\Users\x\Projects\neurobase").as_posix().replace("/", "-")`
  yields `C:-Users-x-Projects-neurobase`; the real Claude Code Windows layout
  remains unverified, but this matches the branch's stated scope and the spec's
  literal slash-replacement rule.
- `uv run pytest tests/test_seed.py tests/test_cli_seed.py tests/test_store.py -v`
  → 72 passed
- `uv run python scripts/ci.py` → ruff, format check, mypy, and 393 tests all
  passed

**Verdict:** approve — the hotfix is narrowly scoped, closes the exercised
Windows failure modes, and keeps the full local gate green.
