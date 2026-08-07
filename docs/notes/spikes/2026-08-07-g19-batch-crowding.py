#!/usr/bin/env python
"""G19 harness — does batch composition decide what a curate pass extracts?

The experiment behind `known-gaps.md` G19 and the `plan_max_raws` default. Three
modes, each answering one question:

    crowding   one batch of N vs N batches of 1, over the same raws
    sweep      raws-per-batch 1/2/3/5/10, replicated — where extraction falls off
    synthcost  time `_synthesize` alone, the cost that governs how often to fold

⚠️ **Runs against a COPY of a store, never the live one.** The fixture is rebuilt
from `--source` before EVERY run, so replicates are independent draws rather than
a walk. `--work` must contain "scratch" or the script refuses to start; the live
store is only ever read by `shutil.copytree`.

⚠️ Real brain calls. A sweep run is ~80 plan calls plus synthesis; budget for it.

Usage:

    uv run python docs/notes/spikes/2026-08-07-g19-batch-crowding.py sweep \\
        --source ~/neurobase --project neurobase \\
        --raws <10 raw filenames> --reps 3

Batch size is controlled DIRECTLY (first N of remaining) rather than through the
byte cap, because bytes were only ever a proxy for the variable under test and no
single cap yields a clean size for every batch. The payload is still built by the
engine's own `_plan_user_payload`/`_raw_payload`, so what the model sees is
byte-identical to a real pass — only the selection rule is replaced.
"""

from __future__ import annotations

import argparse
import json
import shutil
import statistics
import time
from pathlib import Path

from neurobase.brain import resolve_brain
from neurobase.core.config import load_config
from neurobase.core.store_handle import StoreMode, open_store
from neurobase.curator import engine as E
from neurobase.curator.engine import curate


def _set_consumed(path: Path, value: bool) -> None:
    """Flip a raw's `consumed` flag in frontmatter only, leaving the body alone."""
    text = path.read_text(encoding="utf-8")
    head, sep, body = text.partition("\n---\n")
    want = f"consumed: {'true' if value else 'false'}"
    other = f"consumed: {'false' if value else 'true'}"
    path.write_text(head.replace(other, want) + sep + body, encoding="utf-8")


def build_fixture(
    source: Path,
    work: Path,
    project: str,
    unconsume: list[str],
    consume: list[str],
    delete_facts: list[str],
) -> None:
    """Rebuild the fixture from scratch. Called before EVERY run so no replicate
    inherits the previous one's curated state."""
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(source, work)
    mem = work / "projects" / project / "memory"
    for name in delete_facts:
        (mem / "curated" / name).unlink(missing_ok=True)
    for name in unconsume:
        _set_consumed(mem / "raw" / name, False)
    for name in consume:
        _set_consumed(mem / "raw" / name, True)


def _fixed_batcher(original, size: int, sink: list[int]):
    """Replace the batching RULE while keeping the engine's own payload builder."""

    def _wrapped(curated, remaining, max_bytes, max_raws=None):
        docs = list(remaining[:size])
        payload = E._plan_user_payload(curated, [E._raw_payload(d) for d in docs])
        sink.append(len(docs))
        return docs, payload

    return _wrapped


def run_once(work: Path, project: str, brain, size: int, config) -> dict:
    """One fold at a fixed batch size. A crash is recorded as a row, not raised:
    a failed replicate is data about variance too."""
    comp: list[int] = []
    original = E._next_plan_batch
    E._next_plan_batch = _fixed_batcher(original, size, comp)
    try:
        handle = open_store(work, StoreMode.READ)
        before = {d.file_path.stem for d in handle.list_curated(project)}
        t0 = time.monotonic()
        try:
            summary = curate(
                work,
                project,
                brain,
                plan_payload_max_bytes=262_144,
                tombstone_grace_days=config.curate.tombstone_grace_days,
                distill="off",  # raw bodies verbatim; removes distill as a variable
                redact_patterns=tuple(config.redact.extra_patterns),
            )
            err = None
        except Exception as exc:  # noqa: BLE001 - a failed replicate is a data point
            summary, err = {}, f"{type(exc).__name__}: {exc}"
        elapsed = round(time.monotonic() - t0, 1)
    finally:
        E._next_plan_batch = original

    handle = open_store(work, StoreMode.READ)
    after = {d.file_path.stem for d in handle.list_curated(project)}
    log = work / "projects" / project / "memory" / ".curator-log.jsonl"
    records = log.read_text(encoding="utf-8").strip().splitlines() if log.exists() else []
    edges = json.loads(records[-1]).get("fold", {}).get("edges", {}) if records else {}
    cited = sorted({r for raws in edges.values() for r in raws})

    return {
        "batch_size": size,
        "error": err,
        "batches": summary.get("batches"),
        "batch_sizes": comp,
        "upserts": summary.get("upserts"),
        "dropped_from_raw": summary.get("dropped_from_raw"),
        # the MEASUREMENT is which raws were cited, not `upserts` — upserts counts
        # writes, and slugs proved too unstable across runs to compare (G20)
        "n_raws_cited": len(cited),
        "raws_cited": cited,
        "new_facts": sorted(after - before),
        "edges": edges,
        "seconds": elapsed,
    }


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("mode", choices=["crowding", "sweep", "synthcost"])
    ap.add_argument("--source", type=Path, required=True, help="store to COPY (never written)")
    ap.add_argument("--work", type=Path, default=Path("/tmp/nb-g19-scratch"))
    ap.add_argument("--project", default="neurobase")
    ap.add_argument("--raws", nargs="*", default=[], help="raw filenames to un-consume")
    ap.add_argument("--consume", nargs="*", default=[], help="raw filenames to force consumed")
    ap.add_argument("--delete-facts", nargs="*", default=[], help="curated files to remove")
    ap.add_argument("--sizes", nargs="*", type=int, default=[1, 2, 3, 5, 10])
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--out", type=Path, help="append JSONL results here")
    args = ap.parse_args()

    if "scratch" not in str(args.work):
        raise SystemExit(f"refusing to work outside a scratch path: {args.work}")

    config = load_config()
    brain, resolution = resolve_brain(config)
    if brain is None:
        raise SystemExit(f"no brain: {resolution.reason}")
    print(f"brain: {resolution.backend} {resolution.version}", flush=True)

    def fixture() -> None:
        build_fixture(
            args.source, args.work, args.project, args.raws, args.consume, args.delete_facts
        )

    if args.mode == "synthcost":
        times = []
        for rep in range(1, args.reps + 1):
            fixture()
            t0 = time.monotonic()
            summary = curate(args.work, args.project, brain, resynth=True)
            times.append(round(time.monotonic() - t0, 1))
            print(
                f"rep{rep}: {times[-1]}s status={summary.get('status')} "
                f"calls={summary.get('budget_calls')}",
                flush=True,
            )
        print(f"\nsynthesis: {times}  mean {statistics.mean(times):.1f}s", flush=True)
        return

    sizes = args.sizes if args.mode == "sweep" else [1, len(args.raws)]
    out = args.out.open("a", encoding="utf-8") if args.out else None
    for size in sizes:
        for rep in range(1, args.reps + 1):
            fixture()
            row = run_once(args.work, args.project, brain, size, config) | {"rep": rep}
            if out:
                out.write(json.dumps(row) + "\n")
                out.flush()
            print(
                f"size={size:>2} rep{rep}: batches={row['batches']} "
                f"upserts={row['upserts']} cited={row['n_raws_cited']}/{len(args.raws)} "
                f"new={row['new_facts']} ({row['seconds']}s)",
                flush=True,
            )
    if out:
        out.close()


if __name__ == "__main__":
    main()
