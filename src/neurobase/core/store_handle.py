"""The store chokepoint (ADR-0015): a validated ``StoreHandle`` every store
path must obtain before touching the store.

``open_store()`` is the single place the D11 schema guard lives (spec §10:
*"refuse to operate on a schema newer than the binary"*). It reads and validates
``<root>/store.toml`` once and hands back a ``StoreHandle``. Because holding a
handle is proof the schema was already checked, the guard can no longer be
forgotten at an individual call site — the defect recorded as G1
(``docs/known-gaps.md``).

**Migration step 1 (ADR-0015).** This module ships *alongside* the existing
``root: Path`` store API in :mod:`neurobase.core.store` and has **no callers
yet**. Later steps convert ``core/store.py``, ``core/projects.py``, the curator,
the adapters, the MCP server, the recommender, and the CLI to require a handle,
then remove the raw-``Path`` store signatures and add a CI AST check that forbids
constructing store paths outside the store module. Introducing the handle first,
with no callers, keeps that sweep reviewable on its own.

**The ``profile`` qualifier (ADR-0016 D28).** Profiles are logical partitions
under one visible store root. A handle is profile-qualified from this first
commit so the signature is never reworked. Profile *resolution* (a ``None``
profile → the store's ``default_profile``) and profile-addressed artifacts
(proposal stores, recommender ledgers) arrive with the schema-2 migration
(ADR-0016 D31); under schema 1 there is no profile registry, so the handle simply
carries the profile string it was opened with.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from neurobase.core.store import (
    STORE_SCHEMA_VERSION,
    UnsupportedSchemaError,
    ensure_store_metadata,
    store_toml_path,
)


class StoreMode(Enum):
    """How a caller intends to touch the store — this governs whether
    ``open_store()`` may create ``store.toml`` and how it treats a schema it
    does not support (ADR-0015 D23).

    - ``READ`` — validate an existing ``store.toml``; never write. An absent
      ``store.toml`` is *uninitialized* (``schema is None``), not an error, and
      readers behave as on an empty store.
    - ``WRITE`` — validate as ``READ`` and create ``store.toml`` on first use.
      Requiring this mode is what closes G1's ``init --guided`` mutate-before-guard
      hole: a write path cannot obtain a handle without the guard having run.
    - ``DOCTOR`` — inspect any schema, *including one newer than supported*,
      without mutating. The caller reports rather than refuses (D26).
    - ``MIGRATE`` — like ``WRITE``; reserved as the seam for the schema-2 migration
      (ADR-0016 D31), which adds migration-lock and partial-transaction detection.
      No migration logic lives here yet.
    - ``PURGE`` — open even an unparseable or newer-schema store so
      ``uninstall --purge-store`` can delete it (D25); deleting a store you cannot
      parse is the safe escape hatch *from* one.
    """

    READ = "read"
    WRITE = "write"
    DOCTOR = "doctor"
    MIGRATE = "migrate"
    PURGE = "purge"


# Modes permitted to create ``store.toml`` when it does not yet exist.
_CREATING_MODES = (StoreMode.WRITE, StoreMode.MIGRATE)

# Only ``open_store()`` holds this token, so it is the only caller that can
# construct a ``StoreHandle`` — every other construction path raises. This is the
# "unvalidated store is unrepresentable" property from ADR-0015: you cannot
# fabricate a handle that skipped the schema check.
_CONSTRUCTOR_TOKEN = object()


@dataclass(frozen=True)
class StoreHandle:
    """A store proven to be at a schema this binary supports (or, for ``DOCTOR``/
    ``PURGE``, one deliberately opened despite an unsupported schema).

    Construct one only via :func:`open_store`. ``schema is None`` means strictly
    *no ``store.toml`` exists yet* (uninitialized); any integer is a parsed
    on-disk schema, which can exceed :data:`STORE_SCHEMA_VERSION` only for a
    ``DOCTOR`` or ``PURGE`` handle.
    """

    root: Path
    mode: StoreMode
    schema: int | None
    profile: str | None
    _token: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._token is not _CONSTRUCTOR_TOKEN:
            raise TypeError("StoreHandle cannot be constructed directly — call open_store()")


def _make(root: Path, mode: StoreMode, schema: int | None, profile: str | None) -> StoreHandle:
    return StoreHandle(
        root=root, mode=mode, schema=schema, profile=profile, _token=_CONSTRUCTOR_TOKEN
    )


def _parse_schema(path: Path) -> int:
    """Return the ``schema`` integer from an existing ``store.toml``.

    Fail closed: a file we cannot read, that is not valid TOML, or whose
    ``schema`` is missing or not an integer, raises :class:`UnsupportedSchemaError`
    — a store whose own metadata is unreadable is one we must refuse to operate
    on, exactly like one whose schema is too new. (``bool`` is an ``int``
    subclass, so ``schema = true`` is rejected too.)
    """
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise UnsupportedSchemaError(f"{path}: store metadata is unreadable: {exc}") from exc
    schema = data.get("schema")
    if isinstance(schema, bool) or not isinstance(schema, int):
        raise UnsupportedSchemaError(
            f"{path}: store schema {schema!r} is missing or not an integer"
        )
    return schema


def open_store(
    root: Path,
    mode: StoreMode = StoreMode.READ,
    profile: str | None = None,
) -> StoreHandle:
    """Validate ``<root>/store.toml`` once and return a :class:`StoreHandle`.

    The D11 schema comparison lives here and only here. Behavior by ``mode``:

    - ``READ`` / ``DOCTOR`` never write ``store.toml``. ``WRITE`` / ``MIGRATE``
      create it (``schema = STORE_SCHEMA_VERSION``, ``created_at``) when absent,
      via :func:`neurobase.core.store.ensure_store_metadata` so the on-disk format
      stays single-sourced.
    - An existing schema newer than :data:`STORE_SCHEMA_VERSION` raises
      :class:`UnsupportedSchemaError` for ``READ`` / ``WRITE`` / ``MIGRATE``; a
      ``DOCTOR`` handle carries the newer integer instead so the caller can report
      it. ``PURGE`` opens regardless — even unreadable metadata — so the store can
      be deleted.

    ``profile`` is carried onto the handle unchanged (ADR-0016 D28). ``None``
    means "the store's default profile"; under schema 1 there is no profile
    registry to resolve against, so it is simply recorded. This validates only the
    store's own ``store.toml`` identity — ``registry.toml`` parseability is a
    separate, fail-soft concern handled by the registry accessors, not folded in
    here (ADR-0015, review finding F1).
    """
    root = Path(root)
    path = store_toml_path(root)

    # PURGE opens anything: a corrupt or newer store must still be deletable (D25).
    # It never validates and never writes; schema is best-effort (None if we can't
    # parse it), because purge does not care what the schema is.
    if mode is StoreMode.PURGE:
        schema: int | None = None
        if path.exists():
            try:
                schema = _parse_schema(path)
            except UnsupportedSchemaError:
                schema = None
        return _make(root, mode, schema, profile)

    if not path.exists():
        if mode in _CREATING_MODES:
            # ensure_store_metadata writes store.toml (schema = current) when absent.
            ensure_store_metadata(root)
            return _make(root, mode, STORE_SCHEMA_VERSION, profile)
        # READ / DOCTOR: an absent store.toml is an uninitialized store, not an error.
        return _make(root, mode, None, profile)

    # store.toml is present. _parse_schema fails closed on unreadable metadata for
    # every remaining mode (only PURGE, handled above, tolerates that).
    schema = _parse_schema(path)
    if schema > STORE_SCHEMA_VERSION and mode is not StoreMode.DOCTOR:
        raise UnsupportedSchemaError(
            f"{path}: schema {schema} is newer than this binary supports "
            f"(max {STORE_SCHEMA_VERSION}) — upgrade neurobase-cli."
        )
    # DOCTOR keeps the (possibly newer) integer to report on; READ/WRITE/MIGRATE
    # have a confirmed supported schema. WRITE/MIGRATE create nothing here — the
    # store.toml already exists — matching ensure_store_metadata's write-if-absent.
    return _make(root, mode, schema, profile)
