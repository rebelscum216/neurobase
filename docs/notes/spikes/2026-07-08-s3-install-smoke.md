# S3 install smoke — isolated local tool dirs

Date: 2026-07-08

## Question

Can the Phase 6 install path create a working persistent `neurobase` shim via
`uv tool install`, and does the installed command run the lifecycle surfaces?

## Environment

- Host: existing macOS developer account
- `uv`: `uv 0.11.27 (19fc8b03b 2026-07-06 aarch64-apple-darwin)`
- No Docker or Podman available, so this is **not** the final S3 clean-machine
  proof. It isolates the tool install into `/private/tmp` directories but cannot
  prove "no Python preinstalled" on a fresh account/container.

## Local checkout commands

```bash
mkdir -p /private/tmp/neurobase-s3-tool \
  /private/tmp/neurobase-s3-bin \
  /private/tmp/neurobase-s3-cache

env \
  UV_TOOL_DIR=/private/tmp/neurobase-s3-tool \
  UV_TOOL_BIN_DIR=/private/tmp/neurobase-s3-bin \
  UV_CACHE_DIR=/private/tmp/neurobase-s3-cache \
  uv tool install . --force --no-cache

env \
  HOME=/private/tmp/neurobase-s3-home \
  NEUROBASE_ROOT=/private/tmp/neurobase-s3-store \
  /private/tmp/neurobase-s3-bin/neurobase --help

env \
  HOME=/private/tmp/neurobase-s3-home \
  NEUROBASE_ROOT=/private/tmp/neurobase-s3-store \
  PATH=/private/tmp/neurobase-s3-bin:/usr/bin:/bin:/usr/sbin:/sbin \
  /private/tmp/neurobase-s3-bin/neurobase doctor

mkdir -p /private/tmp/neurobase-s3-repo

env \
  HOME=/private/tmp/neurobase-s3-home \
  NEUROBASE_ROOT=/private/tmp/neurobase-s3-store \
  PATH=/private/tmp/neurobase-s3-bin:/usr/bin:/bin:/usr/sbin:/sbin \
  /private/tmp/neurobase-s3-bin/neurobase init --yes \
    --cwd /private/tmp/neurobase-s3-repo
```

## GitHub URL commands

This approximates the pre-PyPI one-liner from a clean temp tool install, using
the pushed `main` merge commit.

```bash
mkdir -p /private/tmp/neurobase-s3-git-tool \
  /private/tmp/neurobase-s3-git-bin \
  /private/tmp/neurobase-s3-git-cache \
  /private/tmp/neurobase-s3-git-home \
  /private/tmp/neurobase-s3-git-store \
  /private/tmp/neurobase-s3-git-repo

/usr/bin/time -p env \
  UV_TOOL_DIR=/private/tmp/neurobase-s3-git-tool \
  UV_TOOL_BIN_DIR=/private/tmp/neurobase-s3-git-bin \
  UV_CACHE_DIR=/private/tmp/neurobase-s3-git-cache \
  uv tool install git+https://github.com/rebelscum216/neurobase.git \
    --force --no-cache

env \
  HOME=/private/tmp/neurobase-s3-git-home \
  NEUROBASE_ROOT=/private/tmp/neurobase-s3-git-store \
  /private/tmp/neurobase-s3-git-bin/neurobase --help

env \
  HOME=/private/tmp/neurobase-s3-git-home \
  NEUROBASE_ROOT=/private/tmp/neurobase-s3-git-store \
  PATH=/private/tmp/neurobase-s3-git-bin:/usr/bin:/bin:/usr/sbin:/sbin \
  /private/tmp/neurobase-s3-git-bin/neurobase doctor

env \
  HOME=/private/tmp/neurobase-s3-git-home \
  NEUROBASE_ROOT=/private/tmp/neurobase-s3-git-store \
  PATH=/private/tmp/neurobase-s3-git-bin:/usr/bin:/bin:/usr/sbin:/sbin \
  /private/tmp/neurobase-s3-git-bin/neurobase init --yes \
    --cwd /private/tmp/neurobase-s3-git-repo
```

## Observed

- `uv tool install . --force --no-cache` resolved 31 packages, downloaded
  uncached dependencies, built `neurobase-cli==0.1.0.dev0`, and installed one
  executable: `neurobase`.
- `uv tool install git+https://github.com/rebelscum216/neurobase.git --force
  --no-cache` resolved the same 31 packages, built from
  `1caf5e6a92b831f3f6d254093a440696980bfc89`, and installed one executable:
  `neurobase`.
- The GitHub URL install completed in `real 2.33` seconds on this machine, below
  the S3 `< 60s` cold-start target for this non-clean environment.
- Installed `neurobase --help` listed the live Phase 6 command surface:
  `doctor`, `init`, `uninstall`, and `hook` alongside prior commands.
- Installed `neurobase doctor` found the isolated shim and returned actionable
  diagnostics for a blank environment: uninitialized store, unenabled project,
  no brain backend, missing Claude/Codex binaries, missing hooks, and missing
  Codex config.
- Installed guided `neurobase init --yes` enabled a scratch repo in the isolated
  store and then reported that no supported agents were on `PATH`, with the
  expected remedy to install agents or run `init --agent <agent>` explicitly.

## Result

Local isolated install smoke: **passed**.

S3 final exit criterion: **still open**. A true fresh macOS user account or clean
container must still run the one-liner and record cold-start time under 60s,
ideally against the published package once `neurobase-cli` is on PyPI or against
the GitHub URL before publication.
