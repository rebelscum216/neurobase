"""The markdown store (spec §1): tree layout, document format, atomic writes.

``<root>/projects/<project>/memory/{raw,curated,nodes,.tombstones}`` +
``index.md``. Every file is YAML frontmatter + a markdown body; every write
is atomic (temp file + rename). Nodes and ``index.md`` are pure functions of
``curated/`` — regenerated wholesale, never appended to.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import tomli_w
import yaml

from neurobase.core.config import load_config

SLUG_RE = re.compile(r"^[a-z0-9-]+$")
RAW_SUBDIRS = ("raw", "curated", "nodes", ".tombstones")
STORE_SCHEMA_VERSION = 1

# The per-pass curator journal, one JSON object per line under a project's
# memory dir. Owned by core (not the curator) so the writer (curator) and any
# core reader — e.g. ``core.graph`` reading the fold record for provenance —
# share one canonical name rather than a cross-layer format dependency.
CURATOR_LOG = ".curator-log.jsonl"

_DOC_RE = re.compile(r"\A---\n(?P<frontmatter>.*?)\n---\n\n(?P<body>.*)\Z", re.DOTALL)


class InvalidSlugError(ValueError):
    """A project/fact/node slug doesn't match ``^[a-z0-9-]+$``."""


class RawConsumedError(RuntimeError):
    """A scribe tried to overwrite a raw capture the curator already consumed."""


class UnsupportedSchemaError(RuntimeError):
    """The store's on-disk schema is newer than this binary supports (D11)."""


@dataclass
class Document:
    """A parsed store document: frontmatter fields, ``body``, ``file_path``.

    ``digest`` is the SHA-256 of the file's bytes **as read**. It exists so a
    caller that reads a file, works for a while, and then writes it back can
    prove the file did not change underneath it (see ``mark_consumed``)."""

    frontmatter: dict[str, Any]
    body: str
    file_path: Path
    digest: str | None = None

    def get(self, key: str, default: Any = None) -> Any:
        return self.frontmatter.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.frontmatter[key]


# --- root + tree -----------------------------------------------------------


def resolve_root(explicit: str | Path | None = None) -> Path:
    """``<root>`` precedence (spec §1): explicit arg > ``NEUROBASE_ROOT`` env
    > config value > default ``~/neurobase``."""
    if explicit is not None:
        return Path(explicit).expanduser().resolve()
    env_root = os.environ.get("NEUROBASE_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()
    cfg = load_config()
    return Path(cfg.store.root).expanduser().resolve()


def _require_slug(value: str, what: str) -> str:
    if not SLUG_RE.match(value):
        raise InvalidSlugError(f"invalid {what}: {value!r} (must match ^[a-z0-9-]+$)")
    return value


def memory_dir(project: str, root: Path) -> Path:
    """The path boundary for every store entry point — validates ``project``
    here so an invalid/empty slug can never silently collapse into a bad path
    (e.g. an empty project string joining away to ``<root>/projects/memory``)."""
    _require_slug(project, "project slug")
    return root / "projects" / project / "memory"


def store_toml_path(root: Path) -> Path:
    return root / "store.toml"


def ensure_store_metadata(root: Path) -> Path:
    """Write ``<root>/store.toml`` (``schema = 1``, ``created_at``) on first
    use; on subsequent calls, refuse to operate if the on-disk schema is
    newer than this binary supports (spec §10, decision D11). ``neurobase
    migrate`` owns future schema bumps."""
    path = store_toml_path(root)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        doc = {"schema": STORE_SCHEMA_VERSION, "created_at": _now_iso()}
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(tomli_w.dumps(doc).encode("utf-8"))
        tmp.replace(path)
        return path
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    schema = data.get("schema")
    if not isinstance(schema, int) or schema > STORE_SCHEMA_VERSION:
        raise UnsupportedSchemaError(
            f"{path}: schema {schema!r} is newer than this binary supports "
            f"(max {STORE_SCHEMA_VERSION}) — upgrade neurobase-cli."
        )
    return path


def ensure_tree(project: str, root: Path) -> Path:
    """Create ``raw/ curated/ nodes/ .tombstones/`` under the project's memory
    dir (and the root's ``store.toml`` if this is a fresh store). Idempotent."""
    ensure_store_metadata(root)
    mem = memory_dir(project, root)
    for sub in RAW_SUBDIRS:
        (mem / sub).mkdir(parents=True, exist_ok=True)
    return mem


# --- document format ---------------------------------------------------


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def write_doc(path: Path, frontmatter: dict[str, Any], body: str) -> Path:
    fm_text = yaml.safe_dump(
        frontmatter, sort_keys=False, default_flow_style=False, allow_unicode=True
    )
    _atomic_write_text(path, f"---\n{fm_text}---\n\n{body}")
    return path


def content_digest(text: str) -> str:
    """SHA-256 of a document's text — the change-detection token behind
    ``Document.digest`` and ``mark_consumed``'s defensive guard."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_doc(path: Path) -> Document:
    text = path.read_text(encoding="utf-8")
    match = _DOC_RE.match(text)
    if not match:
        raise ValueError(f"{path}: missing YAML frontmatter block")
    # Normalize a YAML parse failure (unterminated flow sequence, bad indent, …)
    # to ValueError so every caller's `except ValueError` skip-path (list_raw,
    # list_curated, the proposal loaders) treats an unparseable frontmatter block
    # as a malformed-but-skippable document rather than crashing — yaml.YAMLError
    # is not a ValueError subclass, so it would otherwise propagate uncaught.
    try:
        frontmatter = yaml.safe_load(match.group("frontmatter")) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"{path}: invalid YAML frontmatter: {exc}") from exc
    if not isinstance(frontmatter, dict):
        raise ValueError(f"{path}: frontmatter did not parse to a mapping")
    return Document(
        frontmatter=frontmatter,
        body=match.group("body"),
        file_path=path,
        digest=content_digest(text),
    )


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


# --- raw/ --------------------------------------------------------------


def _sid8(session_id: str | None) -> str:
    if not session_id:
        return "nosid"
    cleaned = re.sub(r"[^a-z0-9]", "", session_id.lower())
    return cleaned[:8] or "nosid"


def raw_filename(captured_at: datetime, agent: str, session_id: str | None) -> str:
    # Microsecond precision (ADR-0023 / P1-CORRECTNESS-006): at second resolution
    # two captures in the same second forced a fabricated timestamp to stay
    # unique, which broke corpus-wide capture order. Microseconds make a truthful
    # ``now`` effectively collision-free, so the filename never lies about when
    # the capture happened. Fixed-width ``%f`` keeps the name lexicographically
    # chronological, and it round-trips from the stored ``captured_at``. The
    # session-keyed overwrite trick (spec §5) is unaffected: a scribe reusing a
    # stable ``captured_at`` still resolves to the same filename.
    ts = captured_at.astimezone(UTC).strftime("%Y-%m-%dT%H-%M-%S.%fZ")
    return f"{ts}_{agent}_{_sid8(session_id)}.md"


_GENERATION_RE = re.compile(r"__g\d{4,}(?=\.md$)")


def canonical_raw_name(name: str) -> str:
    """A raw filename with any ``__gNNNN`` generation suffix stripped — i.e. the
    base ``{ts}_{agent}_{sid8}.md`` that ``raw_filename`` produces and that
    round-trips from ``captured_at``. The generation token is a pure uniqueness
    disambiguator for a same-instant collision; it carries no time and no other
    meaning, so the *timestamp/key portion* — not the whole string — is what spec
    §1 requires to round-trip (ADR-0023 / P1-CORRECTNESS-006)."""
    return _GENERATION_RE.sub("", name)


def _raw_generation(name: str) -> int:
    """The integer generation from a raw filename's ``__gNNNN`` suffix, or 0 for a
    bare (holder) name. Used as a **numeric** sort tiebreak so ``__g10000`` orders
    *after* ``__g9999`` — a lexical filename sort inverts them (``'1' < '9'``),
    which would contradict the claim-order tiebreak once the count crosses four
    digits (ADR-0023 / P2-CORRECTNESS-001). ``captured_at`` remains authoritative;
    this only disambiguates within a single instant/key."""
    match = _GENERATION_RE.search(name)
    return int(match.group()[len("__g") :]) if match else 0


def parse_captured_at(value: Any) -> datetime | None:
    """Parse a raw's ``captured_at`` frontmatter to an aware UTC datetime, or
    ``None`` if absent/unparseable. The authoritative capture instant that
    ``list_raw`` orders by — filenames are only a stable tiebreak."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def raw_path(
    root: Path, project: str, captured_at: datetime, agent: str, session_id: str | None
) -> Path:
    return memory_dir(project, root) / "raw" / raw_filename(captured_at, agent, session_id)


def _claim_fresh_raw_path(
    root: Path, project: str, agent: str, session_id: str | None, captured_at: datetime
) -> Path:
    """Atomically claim a raw filename for a **contended** capture, disjoint from
    the ordinary session-keyed namespace and **without ever modifying the capture
    time** (spec §1; ADR-0023 / P1-DATA-INTEGRITY-001, P1-CORRECTNESS-006).

    A contended capture **always** carries a ``__gNNNN`` generation suffix —
    ``__g0001``, ``__g0002``, … — starting at generation 1; it **never** claims
    the bare ``{ts}_{agent}_{sid8}.md`` base name, even when that base is absent.
    That reservation is the P1-DATA-INTEGRITY-001 fix: the bare base is the name
    an ordinary (lock-*holding*) writer produces with ``raw_path`` and overwrites
    atomically (``os.replace``). A contended (lock-*losing*) writer runs
    concurrently with that holder, so if it claimed the bare base while the base
    was still absent, the holder's imminent atomic replace onto the same base
    would destroy the contended capture outright. An ``O_EXCL`` claim on ``base``
    only tests whether it *already* exists — it cannot see a base the holder is
    *about to* create. Keeping every contended name in the ``__gNNNN`` namespace
    makes it provably disjoint from any base a holder may write, in either
    interleaving.

    Uniqueness never comes from advancing ``captured_at``. Three earlier
    revisions advanced the timestamp (by a second, then by a microsecond) to
    force a unique name; every one fabricated a later event time, and a
    microsecond bump at ``…59.999999`` still rolls into the next second and can
    sort a capture past a genuinely-later one under another key. The capture time
    is an event fact and must never move; only the filename disambiguates.

    Ordering does not depend on the suffix: ``list_raw`` sorts by the authoritative
    ``captured_at`` frontmatter (identical for every generation of one instant),
    and the suffix is only the stable tiebreak within that instant — compared
    **numerically** (`__g10000` after `__g9999`; a lexical filename sort would
    invert them), and a holder's bare ``.md`` base sorts ahead of its ``__gNNNN``
    contenders (`.` < `_`). Each candidate is claimed with ``O_CREAT | O_EXCL`` so
    two racing callers never settle on one name."""
    base = raw_path(root, project, captured_at, agent, session_id)
    base.parent.mkdir(parents=True, exist_ok=True)
    generation = 1
    while True:
        candidate = base.with_name(f"{base.stem}__g{generation:04d}{base.suffix}")
        try:
            fd = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            generation += 1
            continue
        os.close(fd)
        return candidate


def write_raw(
    root: Path,
    project: str,
    *,
    agent: str,
    session_id: str,
    cwd: str,
    branch: str,
    captured_at: datetime,
    body: str,
    transcript_path: str | None = None,
    authority: str | None = None,
    unique: bool = False,
) -> Path:
    """Write (or session-keyed-overwrite) a raw capture (spec §1/§5).

    Rewritable by the owning scribe until the curator flips ``consumed:
    true`` — from then on, raises ``RawConsumedError`` so the caller can
    retry with a fresh ``captured_at`` (a new filename), per the mutability
    rule.

    ``transcript_path`` is optional (ADR-0014, D15): when given, the raw is
    written as ``capture_version: 2`` so the curator's distill step (spec
    §2.0) can resolve the transcript later. Omitted ⇒ a v1 raw; every reader
    must tolerate the absence of both keys.

    ``unique=True`` claims a fresh filename instead of session-keyed
    overwriting, so the write cannot touch *any* raw a concurrent holder owns.
    This is the contended scribe path (ADR-0023): when the curator holds the
    project lock it may be mid-consume on this session's raw, and overwriting it
    would destroy a capture the plan never saw. The claimed name **always**
    carries a generation suffix — ``{ts}_{agent}_{sid8}__gNNNN.md`` (``NNNN``
    starts at ``0001``; ``captured_at`` is never moved) — and **never** the bare
    ``{ts}_{agent}_{sid8}.md`` base, which is reserved for the ordinary
    lock-holding writer. Reserving the base is the P1-DATA-INTEGRITY-001 fix: a
    contended writer that claimed the bare base while it was still absent would
    be overwritten by the holder's imminent atomic replace onto that same base.
    ``list_raw``'s oldest-first contract still holds regardless (it orders by the
    authoritative ``captured_at``, with the suffix only a within-instant
    tiebreak).
    """
    if unique:
        # captured_at is written to frontmatter UNCHANGED — the filename may carry
        # a generation suffix for uniqueness, but the event time never moves.
        path = _claim_fresh_raw_path(root, project, agent, session_id, captured_at)
    else:
        path = raw_path(root, project, captured_at, agent, session_id)
        if path.exists() and read_doc(path).get("consumed"):
            raise RawConsumedError(f"{path} is already consumed; retry with captured_at=now")
    frontmatter = {
        "agent": agent,
        "session_id": session_id,
        "cwd": cwd,
        "branch": branch,
        "captured_at": captured_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "consumed": False,
    }
    if transcript_path is not None:
        frontmatter["transcript_path"] = transcript_path
        frontmatter["capture_version"] = 2
    # ADR-0027 D45: optional, and its ABSENCE means `captured` — so the ordinary
    # scribe path writes nothing here and every existing raw stays valid unchanged.
    # D46: additive per-document key on schema 1, the ADR-0014 `capture_version`
    # precedent; it is not part of the store-identity integer D11 compares.
    # D47: a claim about provenance, never a grant of trust — it MUST NOT reach the
    # plan payload (`_raw_payload` passes only `{raw, body}`), ranking, recall, or
    # proposal mining. Its only permitted readers are the fold journal, `doctor`,
    # and a UI naming the rung it shows.
    if authority is not None:
        frontmatter["authority"] = authority
    write_doc(path, frontmatter, body)
    return path


_MIN_CAPTURED = datetime.min.replace(tzinfo=UTC)


def list_raw(root: Path, project: str, unconsumed_only: bool = True) -> list[Document]:
    """Oldest-**capture**-first. Order is by the authoritative ``captured_at``
    frontmatter (ADR-0023 / P1-CORRECTNESS-006), with the filename as a stable
    tiebreak — *not* by filename string sort, which depended on ``agent``/``sid8``
    bytes and inverted whenever one session's contended captures overlapped a
    later capture under a different key. A raw missing/with an unparseable
    ``captured_at`` sorts first (deterministically, by name). Unparseable *or*
    unreadable files are skipped, never fatal."""
    raw_dir = memory_dir(project, root) / "raw"
    if not raw_dir.exists():
        return []
    docs = []
    for path in raw_dir.glob("*.md"):
        try:
            doc = read_doc(path)
        except (ValueError, OSError):
            # ValueError = malformed frontmatter; OSError = unreadable entry
            # (a directory named *.md, a permission error) — read_doc reads bytes,
            # so both are possible on a hostile tree and both are skippable here.
            continue
        if unconsumed_only and doc.get("consumed"):
            continue
        docs.append(doc)
    return sorted(
        docs,
        key=lambda d: (
            parse_captured_at(d.get("captured_at")) or _MIN_CAPTURED,
            canonical_raw_name(d.file_path.name),
            _raw_generation(d.file_path.name),
            d.file_path.name,
        ),
    )


def mark_consumed(path: Path, *, expect_digest: str | None = None) -> Path | None:
    """Flip ``consumed: true`` — the only permitted mutation of an existing
    raw file; every other frontmatter field and the body are preserved.

    **Safety here comes from the project write lock, not from this function.**
    The caller must hold it (ADR-0023): the curator holds it for the whole pass,
    and a scribe that cannot take it writes a *fresh* filename rather than
    overwriting the raw being consumed (``write_raw(unique=True)`` via
    ``core.lock.write_raw_guarded``). That is what makes the read-then-write
    below safe — no concurrent writer can touch this path while the pass owns it.

    ``expect_digest`` is a **defensive guard, not a compare-and-swap.** It
    re-reads the file and refuses to mark it consumed (returning ``None``) if the
    content changed since the caller read it. Because the check and the write are
    two separate syscalls, it cannot *by itself* close the race — an earlier
    revision of the ADR claimed it could, and Codex disproved that by injecting
    a write between the two (the stale body was written back with ``consumed:
    true`` and the newer capture was lost). It is kept as defense-in-depth
    against a writer that bypasses the advisory lock, not as the mechanism.

    Callers must treat ``None`` as "not consumed" and keep it out of any consumed
    journal, so the raw stays retryable for the next pass."""
    doc = read_doc(path)
    if expect_digest is not None and doc.digest != expect_digest:
        return None
    frontmatter = dict(doc.frontmatter)
    frontmatter["consumed"] = True
    return write_doc(path, frontmatter, doc.body)


# --- curated/ ------------------------------------------------------------


def _dedupe_preserve_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def upsert_curated(
    root: Path,
    project: str,
    slug: str,
    body: str,
    *,
    provenance: Iterable[str] = (),
    supersedes: list[str] | None = None,
    agent_last: str = "curator",
    extra_frontmatter: dict[str, Any] | None = None,
) -> Path:
    """Merge provenance (prior + new, order-preserving dedupe); ``supersedes``
    is the new value if given, else kept from the prior file. Body is
    overwritten wholesale — the curator owns curated content.

    ``agent_last`` defaults to ``"curator"`` (unchanged behavior for every
    existing caller); pass an override (e.g. ``"seed"``) for a caller other
    than the curator so this field never silently claims the curator touched
    a fact it never saw (spec §12.3). ``extra_frontmatter`` additively merges
    caller-owned keys (e.g. a seed importer's ``source_digest``) into the
    written frontmatter — the core keys below always win on collision, so a
    caller can never use it to overwrite ``name``/``status``/``provenance``/
    ``supersedes``/``agent_last``/``updated_at``."""
    _require_slug(slug, "fact slug")
    path = memory_dir(project, root) / "curated" / f"{slug}.md"
    prior_provenance: list[str] = []
    prior_supersedes: list[str] = []
    if path.exists():
        existing = read_doc(path)
        prior_provenance = list(existing.get("provenance") or [])
        prior_supersedes = list(existing.get("supersedes") or [])
    frontmatter: dict[str, Any] = {
        **(extra_frontmatter or {}),
        "name": slug,
        "status": "active",
        "supersedes": supersedes if supersedes is not None else prior_supersedes,
        "provenance": _dedupe_preserve_order([*prior_provenance, *provenance]),
        "agent_last": agent_last,
        "updated_at": _now_iso(),
    }
    # NOTE: a same-slug tombstone is deliberately NOT deleted here. Writing
    # curated/<slug> makes the slug active, so any tombstone becomes stale
    # residue — but deleting it cannot be done safely: that same
    # active+tombstoned shape is also the midpoint of an in-flight
    # ``soft_delete_curated``, and without cross-process ownership this write
    # cannot tell the two apart. Deleting an in-flight tombstone would let the
    # concurrent delete unlink the active file and lose the fact outright.
    # Instead the residue is resolved by spec §1's rule (curated/ is
    # authoritative, so readers see the slug as active) and collected by
    # ``prune_tombstones`` once its grace expires. See ADR-0022.
    return write_doc(path, frontmatter, body)


def list_curated(root: Path, project: str, active_only: bool = True) -> list[Document]:
    """Curated facts, sorted by slug (stable order for plan payloads + node
    synthesis). Unparseable files are skipped, never fatal. ``active_only``
    keeps only ``status: active`` (all files in ``curated/`` should be active —
    tombstoned facts live in ``.tombstones/`` — but filter defensively).
    Unparseable *or* unreadable files are skipped, never fatal — this is the read
    behind the MCP ``memory_list_projects``/``memory_search`` tools, which spec §13
    requires to be fail-soft on a hostile tree rather than raising."""
    curated_dir = memory_dir(project, root) / "curated"
    if not curated_dir.exists():
        return []
    docs = []
    for path in sorted(curated_dir.glob("*.md")):
        try:
            doc = read_doc(path)
        except (ValueError, OSError):  # malformed frontmatter or unreadable entry
            continue
        if active_only and doc.get("status") != "active":
            continue
        docs.append(doc)
    return docs


def soft_delete_curated(root: Path, project: str, slug: str) -> Path:
    """Tombstone a curated fact: move it to ``.tombstones/``, recoverable
    until ``prune_tombstones`` hard-deletes it past the grace period.

    Two-file move, so not atomic: the tombstone is written *first*, then the
    active file is unlinked (spec §1).

    This function **never deletes the tombstone path**, not even to undo its own
    failed move. ``.tombstones/<slug>.md`` is shared, and without a project lock
    (`known-gaps.md` G4) this process cannot prove the file there is still the
    one it wrote — a racing soft-delete of the same slug may have replaced it
    with *its* in-flight tombstone, and deleting that copy loses the fact
    outright. So:

    - ``FileNotFoundError`` on the unlink means the active file is already gone
      (a racing soft-delete completed the move); that is success, not failure.
    - any other unlink error propagates, and the tombstone simply stays. The
      caller sees the error; readers are unaffected because ``curated/`` is
      authoritative while both copies exist; the residue is reclaimed by
      ``prune_tombstones`` once its grace expires."""
    _require_slug(slug, "fact slug")
    mem = memory_dir(project, root)
    src = mem / "curated" / f"{slug}.md"
    doc = read_doc(src)
    frontmatter = dict(doc.frontmatter)
    frontmatter["status"] = "tombstoned"
    frontmatter["tombstoned_at"] = _now_iso()
    dest = mem / ".tombstones" / f"{slug}.md"
    write_doc(dest, frontmatter, doc.body)
    with contextlib.suppress(FileNotFoundError):
        src.unlink()
    return dest


def list_tombstoned(root: Path, project: str) -> list[Document]:
    """Tombstoned facts still inside the grace period, sorted by slug.

    The ``.tombstones/`` counterpart to ``list_curated`` — no store API enumerated
    that directory before, and a lineage/graph reader needs it to render a fact
    that has since been folded away. Unparseable *or* unreadable files are
    skipped, never fatal (same posture as ``list_curated``).

    **Implements spec §1's resolution rule:** a slug present in *both* ``curated/``
    and ``.tombstones/`` is **active**, and the tombstone is residue —
    ``upsert_curated`` deliberately leaves a same-slug tombstone in place because
    deleting it cannot be done safely. §1 makes excluding those slugs a MUST for
    any reader of ``.tombstones/``, so the exclusion lives here rather than in each
    caller: this is the only enumerator of the directory, and a caller that had to
    remember the rule would eventually forget it."""
    mem = memory_dir(project, root)
    tomb_dir = mem / ".tombstones"
    if not tomb_dir.exists():
        return []
    curated_dir = mem / "curated"
    active = {path.stem for path in curated_dir.glob("*.md")} if curated_dir.exists() else set()
    docs = []
    for path in sorted(tomb_dir.glob("*.md")):
        if path.stem in active:  # §1: curated/ wins; this tombstone is residue
            continue
        try:
            doc = read_doc(path)
        except (ValueError, OSError):  # malformed frontmatter or unreadable entry
            continue
        docs.append(doc)
    return docs


def prune_tombstones(root: Path, project: str, older_than_days: int = 14) -> list[str]:
    """Hard-delete tombstones past the grace period. Returns pruned slugs."""
    tomb_dir = memory_dir(project, root) / ".tombstones"
    if not tomb_dir.exists():
        return []
    cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
    pruned = []
    for path in sorted(tomb_dir.glob("*.md")):
        try:
            doc = read_doc(path)
        except (ValueError, OSError):  # malformed frontmatter or unreadable entry
            continue
        tombstoned_at = doc.get("tombstoned_at")
        if not tombstoned_at:
            continue
        try:
            when = datetime.fromisoformat(str(tombstoned_at).replace("Z", "+00:00"))
        except ValueError:
            continue
        if when < cutoff:
            path.unlink()
            pruned.append(path.stem)
    return pruned


# --- nodes/ + index.md ------------------------------------------------------


def write_node(root: Path, project: str, name: str, body: str) -> Path:
    """Nodes are regenerated wholesale, never appended to (no-drift guarantee)."""
    _require_slug(name, "node name")
    path = memory_dir(project, root) / "nodes" / f"{name}.md"
    frontmatter = {"name": name, "generated_at": _now_iso()}
    return write_doc(path, frontmatter, body)


def _first_body_line(body: str, max_chars: int = 120) -> str:
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if line:
            return line.lstrip("#").strip()[:max_chars]
    return ""


def rebuild_index(root: Path, project: str) -> Path:
    """Regenerate ``index.md`` from ``nodes/`` + active ``curated/`` — a pure
    function of on-disk state, run after every curate. A malformed or unreadable
    node/fact is skipped, never fatal (both loops), so one corrupt file cannot
    crash the index rebuild that every curate depends on."""
    mem = memory_dir(project, root)
    lines = [f"# Memory index — {project}", ""]
    nodes_dir = mem / "nodes"
    for node_path in sorted(nodes_dir.glob("*.md")) if nodes_dir.exists() else []:
        try:
            doc = read_doc(node_path)
        except (ValueError, OSError):  # malformed frontmatter or unreadable entry
            continue
        name = doc.get("name", node_path.stem)
        lines.append(f"- [{name}](nodes/{node_path.name}) — {_first_body_line(doc.body)}")
    lines.append("")
    active_count = 0
    curated_dir = mem / "curated"
    for path in curated_dir.glob("*.md") if curated_dir.exists() else []:
        try:
            doc = read_doc(path)
        except (ValueError, OSError):  # malformed frontmatter or unreadable entry
            continue
        if doc.get("status") == "active":
            active_count += 1
    lines.append(f"_{active_count} active curated facts._")
    index_path = mem / "index.md"
    _atomic_write_text(index_path, "\n".join(lines) + "\n")
    return index_path
