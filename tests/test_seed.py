"""Tests for the seed importer core logic (spec §12.3, execution plan
workstream B) — `recommender/seed.py`'s `import_from_dir` /
`import_from_claude_memory`, exercised directly against a real store root."""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import pytest

from neurobase.core import store
from neurobase.recommender import seed


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "store-root"
    store.ensure_tree("proj", r)
    return r


def _curated_body(root: Path, project: str, slug: str) -> str:
    return store.read_doc(store.memory_dir(project, root) / "curated" / f"{slug}.md").body


def _curated_doc(root: Path, project: str, slug: str) -> store.Document:
    return store.read_doc(store.memory_dir(project, root) / "curated" / f"{slug}.md")


# --- directory recursion -----------------------------------------------------


def test_directory_recursion_imports_a_nested_file(root: Path, tmp_path: Path) -> None:
    """Workstream B: 'directory recursion imports a nested file (e.g.
    notes/sub/file.md)'."""
    src = tmp_path / "notes"
    (src / "sub").mkdir(parents=True)
    (src / "top.md").write_text("Top-level note body.", encoding="utf-8")
    (src / "sub" / "nested.md").write_text("Nested note body.", encoding="utf-8")

    result = seed.import_from_dir(root, "proj", src)

    assert set(result.imported) == {"top", "nested"}
    assert _curated_body(root, "proj", "nested") == "Nested note body."
    doc = _curated_doc(root, "proj", "nested")
    assert doc["provenance"] == ["seed:notes/sub/nested.md"]


def test_memory_md_index_file_is_skipped(root: Path, tmp_path: Path) -> None:
    src = tmp_path / "notes"
    src.mkdir()
    (src / "MEMORY.md").write_text("# index\n\n- [topic](topic.md)", encoding="utf-8")
    (src / "topic.md").write_text("Real content.", encoding="utf-8")

    result = seed.import_from_dir(root, "proj", src)

    assert result.imported == ["topic"]
    curated_dir = store.memory_dir("proj", root) / "curated"
    assert sorted(p.stem for p in curated_dir.glob("*.md")) == ["topic"]


# --- bad directory / unreadable file fail-soft -------------------------------


def test_missing_top_level_directory_is_a_hard_error(root: Path, tmp_path: Path) -> None:
    """Workstream B: 'bad directory / unreadable file fail-soft' — the
    top-level-missing half: a wholly bad target raises, nothing is written."""
    missing = tmp_path / "does-not-exist"
    with pytest.raises(seed.BadSeedSourceError):
        seed.import_from_dir(root, "proj", missing)
    curated_dir = store.memory_dir("proj", root) / "curated"
    assert not curated_dir.exists() or not list(curated_dir.glob("*.md"))


def test_file_target_instead_of_directory_is_a_hard_error(root: Path, tmp_path: Path) -> None:
    not_a_dir = tmp_path / "plain.md"
    not_a_dir.write_text("hi", encoding="utf-8")
    with pytest.raises(seed.BadSeedSourceError):
        seed.import_from_dir(root, "proj", not_a_dir)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits don't apply on Windows")
def test_unreadable_top_level_directory_is_a_hard_error(root: Path, tmp_path: Path) -> None:
    """Workstream B / §12.3: an *unreadable* named top-level target is a hard
    error, not a silent empty import — is_dir() is true for a chmod-000 dir,
    but there is nothing importable and the run must fail loudly (distinct from
    an unreadable *nested* file, which is skipped-and-counted)."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root ignores POSIX permission bits")

    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "note.md").write_text("A note behind a locked door.", encoding="utf-8")
    locked.chmod(0o000)

    try:
        with pytest.raises(seed.BadSeedSourceError):
            seed.import_from_dir(root, "proj", locked)
    finally:
        locked.chmod(0o755)  # restore so pytest's tmp_path cleanup can remove it

    curated_dir = store.memory_dir("proj", root) / "curated"
    assert not curated_dir.exists() or not list(curated_dir.glob("*.md"))


def test_oversized_file_is_skipped_but_run_continues(root: Path, tmp_path: Path) -> None:
    """Workstream B: 'bad directory / unreadable file fail-soft' — the
    individual-file half, oversized case: skipped and counted, run continues,
    the other file in the same tree still imports."""
    src = tmp_path / "notes"
    src.mkdir()
    big = src / "big.md"
    big.write_text("x" * (seed.MAX_SOURCE_BYTES + 1), encoding="utf-8")
    (src / "small.md").write_text("A small, real note.", encoding="utf-8")

    result = seed.import_from_dir(root, "proj", src)

    assert result.imported == ["small"]
    assert len(result.skipped) == 1
    skipped_path, reason = result.skipped[0]
    assert skipped_path == str(big)
    assert "oversized" in reason
    curated_dir = store.memory_dir("proj", root) / "curated"
    assert sorted(p.stem for p in curated_dir.glob("*.md")) == ["small"]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits don't apply on Windows")
def test_unreadable_file_is_skipped_but_run_continues(root: Path, tmp_path: Path) -> None:
    """Workstream B: 'bad directory / unreadable file fail-soft' — a
    genuinely permission-denied file is skipped (counted), the run continues
    and still imports the other file in the same tree."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root ignores POSIX permission bits")

    src = tmp_path / "notes"
    src.mkdir()
    secret_perm_file = src / "no-read.md"
    secret_perm_file.write_text("you can't read this.", encoding="utf-8")
    secret_perm_file.chmod(0o000)
    (src / "ok.md").write_text("A readable note.", encoding="utf-8")

    try:
        result = seed.import_from_dir(root, "proj", src)
    finally:
        secret_perm_file.chmod(0o644)  # restore so pytest's tmp_path cleanup can remove it

    assert result.imported == ["ok"]
    assert len(result.skipped) == 1
    skipped_path, reason = result.skipped[0]
    assert skipped_path == str(secret_perm_file)
    assert "unreadable" in reason
    curated_dir = store.memory_dir("proj", root) / "curated"
    assert sorted(p.stem for p in curated_dir.glob("*.md")) == ["ok"]


def test_undecodable_file_is_skipped_but_run_continues(root: Path, tmp_path: Path) -> None:
    src = tmp_path / "notes"
    src.mkdir()
    bad_bytes = src / "bad-bytes.md"
    bad_bytes.write_bytes(b"\xff\xfe not valid utf-8 \x80\x81")
    (src / "ok.md").write_text("A readable note.", encoding="utf-8")

    result = seed.import_from_dir(root, "proj", src)

    assert result.imported == ["ok"]
    assert len(result.skipped) == 1
    skipped_path, reason = result.skipped[0]
    assert skipped_path == str(bad_bytes)
    assert "undecodable" in reason


def test_empty_file_is_skipped_but_run_continues(root: Path, tmp_path: Path) -> None:
    """Workstream B behavior: 'skip empty/unreadable files' — the empty half.
    A 0-byte and a whitespace-only file are both skipped; a real file in the
    same tree still imports."""
    src = tmp_path / "notes"
    src.mkdir()
    empty = src / "empty.md"
    empty.write_text("", encoding="utf-8")
    whitespace_only = src / "whitespace.md"
    whitespace_only.write_text("   \n\n\t\n", encoding="utf-8")
    (src / "ok.md").write_text("A readable note.", encoding="utf-8")

    result = seed.import_from_dir(root, "proj", src)

    assert result.imported == ["ok"]
    skipped_paths = {p for p, _reason in result.skipped}
    assert skipped_paths == {str(empty), str(whitespace_only)}
    for _path, reason in result.skipped:
        assert "empty" in reason


def test_empty_source_directory_yields_empty_result(root: Path, tmp_path: Path) -> None:
    src = tmp_path / "notes"
    src.mkdir()

    result = seed.import_from_dir(root, "proj", src)

    assert result.imported == []
    assert result.unchanged == []
    assert result.skipped == []
    curated_dir = store.memory_dir("proj", root) / "curated"
    assert not curated_dir.exists() or not list(curated_dir.glob("*.md"))


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need elevated perms on Windows")
def test_symlinked_file_is_skipped_not_followed(root: Path, tmp_path: Path) -> None:
    """A .md-suffixed symlink inside --from-dir pointing outside the tree
    (e.g. at something like ~/.ssh/id_rsa) must never be read."""
    outside = tmp_path / "outside-secret.md"
    outside.write_text("super secret content that lives outside the source tree", encoding="utf-8")
    src = tmp_path / "notes"
    src.mkdir()
    link = src / "linked-note.md"
    link.symlink_to(outside)
    (src / "ok.md").write_text("A readable note.", encoding="utf-8")

    result = seed.import_from_dir(root, "proj", src)

    assert result.imported == ["ok"]
    skipped_path, reason = result.skipped[0]
    assert skipped_path == str(link)
    assert "symlink" in reason
    curated_dir = store.memory_dir("proj", root) / "curated"
    assert sorted(p.stem for p in curated_dir.glob("*.md")) == ["ok"]


def test_malformed_frontmatter_falls_back_to_whole_file_as_body(root: Path, tmp_path: Path) -> None:
    """An unterminated frontmatter block (no closing `---`) is treated as if
    there were no frontmatter at all — the whole file, delimiter included,
    becomes the body."""
    src = tmp_path / "notes"
    src.mkdir()
    text = "---\nname: incomplete\ndescription: no closing delimiter\n\nBody-ish text.\n"
    (src / "broken.md").write_text(text, encoding="utf-8")

    result = seed.import_from_dir(root, "proj", src)

    assert result.imported == ["broken"]
    assert _curated_body(root, "proj", "broken") == text


# --- redaction ---------------------------------------------------------------


def test_redaction_before_curated_write(root: Path, tmp_path: Path) -> None:
    """Workstream B: 'redaction before curated write' — a real secret-shaped
    string must never land in curated/ unredacted."""
    src = tmp_path / "notes"
    src.mkdir()
    secret = "AKIAABCDEFGHIJKLMNOP"
    (src / "creds.md").write_text(f"our aws key is {secret} — don't share it.", encoding="utf-8")

    result = seed.import_from_dir(root, "proj", src)

    assert result.imported == ["creds"]
    body = _curated_body(root, "proj", "creds")
    assert secret not in body
    assert "[REDACTED:aws-key]" in body


def test_redaction_uses_configured_extra_patterns(root: Path, tmp_path: Path) -> None:
    src = tmp_path / "notes"
    src.mkdir()
    (src / "note.md").write_text("internal codeword: zeta-9000 is secret", encoding="utf-8")

    result = seed.import_from_dir(root, "proj", src, extra_patterns=[r"zeta-9000"])

    assert result.imported == ["note"]
    body = _curated_body(root, "proj", "note")
    assert "zeta-9000" not in body
    assert "[REDACTED:custom]" in body


# --- idempotent import --------------------------------------------------------


def test_idempotent_rerun_no_duplicate_facts_or_provenance(root: Path, tmp_path: Path) -> None:
    """Workstream B: 'idempotent import' — a rerun over an unchanged source
    tree must not create duplicate curated facts or duplicate provenance
    entries."""
    src = tmp_path / "notes"
    src.mkdir()
    (src / "note.md").write_text("stable content, never changes.", encoding="utf-8")

    first = seed.import_from_dir(root, "proj", src)
    second = seed.import_from_dir(root, "proj", src)

    assert first.imported == ["note"]
    assert second.imported == []
    assert second.unchanged == ["note"]

    curated_dir = store.memory_dir("proj", root) / "curated"
    assert [p.stem for p in curated_dir.glob("*.md")] == ["note"]
    doc = _curated_doc(root, "proj", "note")
    assert doc["provenance"] == ["seed:notes/note.md"]


def test_changed_source_reimports_as_update_to_same_slug(root: Path, tmp_path: Path) -> None:
    src = tmp_path / "notes"
    src.mkdir()
    note = src / "note.md"
    note.write_text("version one.", encoding="utf-8")
    seed.import_from_dir(root, "proj", src)

    note.write_text("version two, changed.", encoding="utf-8")
    result = seed.import_from_dir(root, "proj", src)

    assert result.imported == ["note"]
    assert result.unchanged == []
    doc = _curated_doc(root, "proj", "note")
    assert doc.body == "version two, changed."
    # Still exactly one provenance entry — same source path both times.
    assert doc["provenance"] == ["seed:notes/note.md"]
    assert doc["source_digest"] == hashlib.sha256(b"version two, changed.").hexdigest()


# --- provenance and source metadata -------------------------------------------


def test_provenance_and_source_metadata(root: Path, tmp_path: Path) -> None:
    """Workstream B: 'provenance and source metadata' — the source path is
    preserved in provenance and evidence-adjacent frontmatter bookkeeping."""
    src = tmp_path / "my-notes"
    (src / "sub").mkdir(parents=True)
    raw = b"content for digest check"
    (src / "sub" / "deep.md").write_bytes(raw)

    result = seed.import_from_dir(root, "proj", src)

    assert result.imported == ["deep"]
    doc = _curated_doc(root, "proj", "deep")
    assert doc["provenance"] == ["seed:my-notes/sub/deep.md"]
    assert doc["source_path"] == "seed:my-notes/sub/deep.md"
    assert doc["source_digest"] == hashlib.sha256(raw).hexdigest()


# --- agent_last / slug rules ---------------------------------------------------


def test_seed_import_stamps_agent_last_seed_not_curator(root: Path, tmp_path: Path) -> None:
    src = tmp_path / "notes"
    src.mkdir()
    (src / "note.md").write_text("body text", encoding="utf-8")

    seed.import_from_dir(root, "proj", src)

    doc = _curated_doc(root, "proj", "note")
    assert doc["agent_last"] == "seed"


def test_slug_from_frontmatter_name_when_valid(root: Path, tmp_path: Path) -> None:
    src = tmp_path / "notes"
    src.mkdir()
    (src / "some-file.md").write_text(
        "---\nname: custom-slug\ndescription: x\n---\n\nBody text here.",
        encoding="utf-8",
    )

    result = seed.import_from_dir(root, "proj", src)

    assert result.imported == ["custom-slug"]
    assert _curated_body(root, "proj", "custom-slug") == "Body text here."


def test_slug_falls_back_to_filename_when_no_frontmatter_name(root: Path, tmp_path: Path) -> None:
    src = tmp_path / "notes"
    src.mkdir()
    (src / "My Cool Note.md").write_text("plain body, no frontmatter.", encoding="utf-8")

    result = seed.import_from_dir(root, "proj", src)

    assert result.imported == ["my-cool-note"]


def test_slug_falls_back_to_filename_when_frontmatter_name_invalid(
    root: Path, tmp_path: Path
) -> None:
    src = tmp_path / "notes"
    src.mkdir()
    (src / "topic.md").write_text(
        "---\nname: Not A Valid Slug!\n---\n\nBody text.",
        encoding="utf-8",
    )

    result = seed.import_from_dir(root, "proj", src)

    assert result.imported == ["topic"]


def test_secret_shaped_filename_does_not_become_slug_or_name(root: Path, tmp_path: Path) -> None:
    """A filename that itself looks like a secret (e.g. an AWS access-key
    shape) must never become the persisted slug/curated filename/`name:`
    frontmatter field verbatim — only the body is redacted by default, so
    slug derivation has to run its own check."""
    src = tmp_path / "notes"
    src.mkdir()
    secret_name = "AKIAABCDEFGHIJKLMNOP"
    (src / f"{secret_name}.md").write_text("unrelated body text.", encoding="utf-8")

    result = seed.import_from_dir(root, "proj", src)

    assert len(result.imported) == 1
    slug = result.imported[0]
    assert secret_name.lower() not in slug
    assert secret_name not in slug
    doc = _curated_doc(root, "proj", slug)
    assert secret_name not in doc["name"]
    assert secret_name.lower() not in doc["name"]
    curated_dir = store.memory_dir("proj", root) / "curated"
    for path in curated_dir.glob("*.md"):
        assert secret_name.lower() not in path.stem


def test_secret_shaped_frontmatter_name_does_not_become_slug(root: Path, tmp_path: Path) -> None:
    src = tmp_path / "notes"
    src.mkdir()
    secret_name = "sk-abcdefghijklmnopqrstuvwxyz012345"
    (src / "note.md").write_text(
        f"---\nname: {secret_name}\n---\n\nBody text.",
        encoding="utf-8",
    )

    result = seed.import_from_dir(root, "proj", src)

    assert len(result.imported) == 1
    slug = result.imported[0]
    assert slug != secret_name
    assert secret_name not in slug


def test_seed_refuses_to_clobber_slug_touched_by_other_agent(root: Path, tmp_path: Path) -> None:
    """If a slug the seed importer previously wrote is later touched by an
    ordinary (non-seed) `upsert_curated` call — e.g. the curator's normal
    apply-upserts path, which doesn't pass `extra_frontmatter` and so drops
    `source_digest` — a subsequent rerun over the byte-identical source file
    must not silently overwrite the curator's refined content back to the
    stale raw seed text."""
    src = tmp_path / "notes"
    src.mkdir()
    (src / "note.md").write_text("original seed content.", encoding="utf-8")

    first = seed.import_from_dir(root, "proj", src)
    assert first.imported == ["note"]

    # Simulate a normal curator upsert touching the same slug.
    store.upsert_curated(root, "proj", "note", "curator-refined content.")
    assert _curated_body(root, "proj", "note") == "curator-refined content."

    result = seed.import_from_dir(root, "proj", src)

    assert result.imported == []
    assert result.unchanged == []
    assert len(result.skipped) == 1
    _path, reason = result.skipped[0]
    assert "note" in reason
    assert "curator" in reason
    # Curator's content must survive untouched.
    assert _curated_body(root, "proj", "note") == "curator-refined content."


# --- --from-claude-memory ------------------------------------------------------


def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """HOME is honored by Path.home() on POSIX; Windows reads USERPROFILE.
    Set both so `claude_memory_dir`'s `Path.home()` isolates to tmp on every
    platform (mirrors `tests/test_cli_init.py`'s `env` fixture convention)."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


def test_claude_memory_dir_path_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _isolate_home(tmp_path, monkeypatch)
    project_root = Path("/Users/x/Projects/neurobase")
    expected = home / ".claude" / "projects" / "-Users-x-Projects-neurobase" / "memory"
    assert seed.claude_memory_dir(project_root) == expected


def test_import_from_claude_memory_missing_dir_is_not_an_error(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_home(tmp_path, monkeypatch)

    result = seed.import_from_claude_memory(root, "proj", tmp_path / "some-project")

    assert result.imported == []
    assert result.skipped == []


def test_import_from_claude_memory_imports_topic_files_skips_index(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_home(tmp_path, monkeypatch)

    project_root = tmp_path / "myproject"
    mem_dir = seed.claude_memory_dir(project_root)
    mem_dir.mkdir(parents=True)
    (mem_dir / "MEMORY.md").write_text("# index", encoding="utf-8")
    (mem_dir / "conventions.md").write_text(
        "---\nname: conventions\ndescription: house style\n---\n\nUse uv, not pip.",
        encoding="utf-8",
    )

    result = seed.import_from_claude_memory(root, "proj", project_root)

    assert result.imported == ["conventions"]
    doc = _curated_doc(root, "proj", "conventions")
    assert doc.body == "Use uv, not pip."
    assert doc["provenance"] == ["seed:claude-memory/conventions.md"]
