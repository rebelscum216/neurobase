"""ADR-0015 store chokepoint: ``open_store()`` is the single validated entry to
the store, and the D11 schema guard (spec §10) lives there. These tests pin the
per-mode behavior and the "an unvalidated store is unrepresentable" property.

Step 1 has no callers — these tests exercise the module directly.
"""

from __future__ import annotations

import tomllib
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from neurobase.core import store
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
