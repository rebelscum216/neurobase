"""ADR-0015 store chokepoint: ``open_store()`` is the single validated entry to
the store, and the D11 schema guard (spec §10) lives there. These tests pin the
per-mode behavior and the "an unvalidated store is unrepresentable" property.

Step 1 has no callers — these tests exercise the module directly.
"""

from __future__ import annotations

import tomllib
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from pathlib import Path

import pytest

from neurobase.core import projects, store
from neurobase.core.store import STORE_SCHEMA_VERSION, UnsupportedSchemaError
from neurobase.core.store_handle import StoreHandle, StoreMode, open_store

NEWER_SCHEMA = STORE_SCHEMA_VERSION + 1


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "store-root"


def _write_store_toml(root: Path, contents: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "store.toml"
    path.write_text(contents, encoding="utf-8")
    return path


# --- READ ------------------------------------------------------------------


def test_read_uninitialized_is_not_an_error_and_never_writes(root: Path) -> None:
    handle = open_store(root, StoreMode.READ)
    assert handle.mode is StoreMode.READ
    assert handle.schema is None  # None strictly means "no store.toml yet"
    assert not (root / "store.toml").exists()  # READ never writes


def test_read_defaults_to_read_mode(root: Path) -> None:
    store.ensure_store_metadata(root)
    assert open_store(root).mode is StoreMode.READ


def test_read_existing_supported_schema(root: Path) -> None:
    store.ensure_store_metadata(root)  # writes schema = STORE_SCHEMA_VERSION
    handle = open_store(root, StoreMode.READ)
    assert handle.schema == STORE_SCHEMA_VERSION


def test_read_refuses_newer_schema(root: Path) -> None:
    _write_store_toml(root, f'schema = {NEWER_SCHEMA}\ncreated_at = "2020-01-01T00:00:00Z"\n')
    with pytest.raises(UnsupportedSchemaError):
        open_store(root, StoreMode.READ)


def test_read_refuses_non_integer_schema(root: Path) -> None:
    _write_store_toml(root, 'schema = "one"\ncreated_at = "2020-01-01T00:00:00Z"\n')
    with pytest.raises(UnsupportedSchemaError):
        open_store(root, StoreMode.READ)


def test_read_refuses_boolean_schema(root: Path) -> None:
    # bool is an int subclass — `schema = true` must not slip through as schema 1.
    _write_store_toml(root, "schema = true\n")
    with pytest.raises(UnsupportedSchemaError):
        open_store(root, StoreMode.READ)


def test_read_refuses_missing_schema_key(root: Path) -> None:
    _write_store_toml(root, 'created_at = "2020-01-01T00:00:00Z"\n')
    with pytest.raises(UnsupportedSchemaError):
        open_store(root, StoreMode.READ)


def test_read_refuses_corrupt_toml(root: Path) -> None:
    _write_store_toml(root, "this is not = valid = toml ][")
    with pytest.raises(UnsupportedSchemaError):
        open_store(root, StoreMode.READ)


# --- WRITE / MIGRATE -------------------------------------------------------


def test_write_creates_store_toml_when_absent(root: Path) -> None:
    handle = open_store(root, StoreMode.WRITE)
    path = root / "store.toml"
    assert path.exists()
    assert handle.schema == STORE_SCHEMA_VERSION
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    assert data["schema"] == STORE_SCHEMA_VERSION
    assert "created_at" in data


def test_write_does_not_rewrite_an_existing_store(root: Path) -> None:
    open_store(root, StoreMode.WRITE)
    before = (root / "store.toml").read_bytes()
    handle = open_store(root, StoreMode.WRITE)
    assert (root / "store.toml").read_bytes() == before  # no rewrite (created_at kept)
    assert handle.schema == STORE_SCHEMA_VERSION


def test_write_refuses_newer_schema(root: Path) -> None:
    _write_store_toml(root, f'schema = {NEWER_SCHEMA}\ncreated_at = "2020-01-01T00:00:00Z"\n')
    with pytest.raises(UnsupportedSchemaError):
        open_store(root, StoreMode.WRITE)


def test_migrate_creates_like_write(root: Path) -> None:
    handle = open_store(root, StoreMode.MIGRATE)
    assert (root / "store.toml").exists()
    assert handle.mode is StoreMode.MIGRATE
    assert handle.schema == STORE_SCHEMA_VERSION


def test_migrate_refuses_newer_schema(root: Path) -> None:
    _write_store_toml(root, f'schema = {NEWER_SCHEMA}\ncreated_at = "2020-01-01T00:00:00Z"\n')
    with pytest.raises(UnsupportedSchemaError):
        open_store(root, StoreMode.MIGRATE)


# --- DOCTOR ----------------------------------------------------------------


def test_doctor_uninitialized_reports_none_without_writing(root: Path) -> None:
    handle = open_store(root, StoreMode.DOCTOR)
    assert handle.schema is None
    assert not (root / "store.toml").exists()  # DOCTOR never mutates


def test_doctor_reports_supported_schema(root: Path) -> None:
    store.ensure_store_metadata(root)
    assert open_store(root, StoreMode.DOCTOR).schema == STORE_SCHEMA_VERSION


def test_doctor_reports_newer_schema_without_refusing(root: Path) -> None:
    _write_store_toml(root, f'schema = {NEWER_SCHEMA}\ncreated_at = "2020-01-01T00:00:00Z"\n')
    handle = open_store(root, StoreMode.DOCTOR)  # must NOT raise — doctor reports
    assert handle.schema == NEWER_SCHEMA


def test_doctor_does_not_mutate_a_newer_store(root: Path) -> None:
    contents = f'schema = {NEWER_SCHEMA}\ncreated_at = "2020-01-01T00:00:00Z"\n'
    _write_store_toml(root, contents)
    open_store(root, StoreMode.DOCTOR)
    assert (root / "store.toml").read_text(encoding="utf-8") == contents


# --- PURGE -----------------------------------------------------------------


def test_purge_opens_uninitialized(root: Path) -> None:
    handle = open_store(root, StoreMode.PURGE)
    assert handle.mode is StoreMode.PURGE
    assert handle.schema is None
    assert not (root / "store.toml").exists()  # PURGE never writes


def test_purge_opens_newer_schema_without_refusing(root: Path) -> None:
    _write_store_toml(root, f'schema = {NEWER_SCHEMA}\ncreated_at = "2020-01-01T00:00:00Z"\n')
    handle = open_store(root, StoreMode.PURGE)  # must open so it can be deleted (D25)
    assert handle.schema == NEWER_SCHEMA


def test_purge_opens_corrupt_store_without_refusing(root: Path) -> None:
    _write_store_toml(root, "this is not = valid = toml ][")
    handle = open_store(root, StoreMode.PURGE)  # unparseable, still deletable
    assert handle.schema is None


# --- profile qualifier (ADR-0016 D28) --------------------------------------


def test_profile_defaults_to_none(root: Path) -> None:
    assert open_store(root, StoreMode.READ).profile is None


def test_profile_is_carried_through(root: Path) -> None:
    handle = open_store(root, StoreMode.READ, profile="open-source")
    assert handle.profile == "open-source"


# --- enforcement: an unvalidated store is unrepresentable ------------------


def test_direct_construction_is_refused(root: Path) -> None:
    with pytest.raises(TypeError):
        StoreHandle(root=root, mode=StoreMode.READ, schema=None, profile=None)


def test_handle_is_frozen(root: Path) -> None:
    handle = open_store(root, StoreMode.READ)
    with pytest.raises(FrozenInstanceError):
        handle.schema = 999  # type: ignore[misc]


def test_token_field_excluded_from_equality_and_repr(root: Path) -> None:
    store.ensure_store_metadata(root)
    a = open_store(root, StoreMode.READ)
    b = open_store(root, StoreMode.READ)
    assert a == b  # _token is compare=False, so two validated handles compare equal
    assert "_token=" not in repr(a)  # repr=False keeps the token field out of repr


# --- handle method surface (step 2): each delegates to the root-taking store/
# registry function, targeting the handle's validated root ------------------

WHEN = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)


@pytest.fixture
def handle(root: Path) -> StoreHandle:
    return open_store(root, StoreMode.WRITE)  # WRITE so store.toml exists


def test_method_memory_dir_targets_handle_root(handle: StoreHandle, root: Path) -> None:
    assert handle.memory_dir("proj") == store.memory_dir("proj", root)
    assert handle.memory_dir("proj") == root / "projects" / "proj" / "memory"


def test_method_ensure_tree_creates_subdirs(handle: StoreHandle, root: Path) -> None:
    mem = handle.ensure_tree("proj")
    for sub in ("raw", "curated", "nodes", ".tombstones"):
        assert (mem / sub).is_dir()


def test_method_raw_path_matches_store(handle: StoreHandle, root: Path) -> None:
    assert handle.raw_path("proj", WHEN, "claude", "sid123") == store.raw_path(
        root, "proj", WHEN, "claude", "sid123"
    )


def test_method_write_raw_then_list_raw_round_trip(handle: StoreHandle) -> None:
    handle.ensure_tree("proj")
    handle.write_raw(
        "proj",
        agent="claude",
        session_id="sid1",
        cwd="/tmp/repo",
        branch="main",
        captured_at=WHEN,
        body="a captured fact",
    )
    docs = handle.list_raw("proj")
    assert len(docs) == 1
    assert docs[0].body == "a captured fact"
    assert docs[0].get("agent") == "claude"


def test_method_mark_consumed_hides_from_unconsumed_list(handle: StoreHandle) -> None:
    handle.ensure_tree("proj")
    path = handle.write_raw(
        "proj",
        agent="claude",
        session_id="sid1",
        cwd="/tmp/repo",
        branch="main",
        captured_at=WHEN,
        body="fact",
    )
    handle.mark_consumed(path)
    assert handle.list_raw("proj", unconsumed_only=True) == []


def test_method_mark_consumed_rejects_path_outside_the_handle_store(
    handle: StoreHandle, tmp_path: Path
) -> None:
    # F1: a second, independent store B with a real raw file of its own.
    other = open_store(tmp_path / "other-store", StoreMode.WRITE)
    other.ensure_tree("proj")
    foreign = other.write_raw(
        "proj",
        agent="claude",
        session_id="sid1",
        cwd="/tmp/repo",
        branch="main",
        captured_at=WHEN,
        body="fact in store B",
    )
    # Handle A (fixture, rooted elsewhere) must refuse to mutate store B's file.
    with pytest.raises(ValueError, match="outside this handle's store"):
        handle.mark_consumed(foreign)
    # Store B's raw is untouched — still unconsumed.
    assert len(other.list_raw("proj", unconsumed_only=True)) == 1


def test_method_upsert_then_list_curated_round_trip(handle: StoreHandle) -> None:
    handle.ensure_tree("proj")
    handle.upsert_curated("proj", "a-fact", "the body", provenance=["raw/x.md"])
    docs = handle.list_curated("proj")
    assert [d.get("name") for d in docs] == ["a-fact"]
    assert docs[0].body == "the body"


def test_method_soft_delete_and_prune_curated(handle: StoreHandle) -> None:
    handle.ensure_tree("proj")
    handle.upsert_curated("proj", "a-fact", "body")
    handle.soft_delete_curated("proj", "a-fact")
    assert handle.list_curated("proj") == []  # tombstoned, not active
    # nothing is older than the grace period yet, so prune removes nothing
    assert handle.prune_tombstones("proj", older_than_days=14) == []


def test_method_write_node_and_rebuild_index(handle: StoreHandle, root: Path) -> None:
    handle.ensure_tree("proj")
    node_path = handle.write_node("proj", "a-node", "# A node\n\nbody")
    assert node_path.exists()
    index_path = handle.rebuild_index("proj")
    assert "a-node" in index_path.read_text(encoding="utf-8")


def test_method_registry_register_and_resolve(
    handle: StoreHandle, tmp_path: Path, root: Path
) -> None:
    repo = tmp_path / "myrepo"
    repo.mkdir()
    slug = handle.register_project(repo, slug="myrepo")
    assert slug == "myrepo"
    # the handle method reads the same registry the root-taking function writes
    assert handle.load_registry() == projects.load_registry(root)
    assert "myrepo" in handle.load_registry()
    assert handle.resolve_project(repo) == "myrepo"
