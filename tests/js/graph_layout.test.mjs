/* Behavioural tests for the shipped graph renderer (`templates/graph.html`).
 *
 * These exist because round 2 proved a source assertion cannot guard this file:
 * the committed regex test was green while the O(n) complexity claim it named was
 * false at 66.7M scans / 4,000 nodes (Codex P2-TEST-GAP-006, P2-UX-API-CONTRACT-005).
 * Every test here executes the real script.
 *
 * Run: node --test 'tests/js/*.test.mjs'   (wired into scripts/ci.py, so it
 * cannot skip). The glob is not decoration: a bare `node --test tests/js/`
 * resolves the directory as a module and dies, and expanding the pattern needs
 * Node 21+ — the gate enforces 22, matching CI's pin.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { runGraph, makePayload, graphScript } from "./harness.mjs";

const ITER = 30; // the relaxation's iteration count, mirrored from the renderer

// --- P2-UX-API-CONTRACT-005: work must be bounded, at any density ------------

test("layout work is bounded by nodes x iterations x MAX_SCAN, not by density", () => {
  const { probe } = runGraph(makePayload(2100));
  const ceiling = probe.nodeCount * ITER * probe.maxScan;
  assert.ok(
    probe.relaxScans <= ceiling,
    `scanned ${probe.relaxScans} grid entries, hard ceiling is ${ceiling}`,
  );
});

test("scans grow linearly with node count (2100 -> 4000), not superlinearly", () => {
  const small = runGraph(makePayload(2100)).probe;
  const large = runGraph(makePayload(4000)).probe;

  const nodeRatio = large.nodeCount / small.nodeCount;       // ~1.90
  const scanRatio = large.relaxScans / small.relaxScans;

  // The round-2 implementation measured nodes x1.90 -> work x3.15. Linear means
  // the work ratio tracks the node ratio; allow 15% for grid-occupancy effects.
  assert.ok(
    scanRatio <= nodeRatio * 1.15,
    `scans grew ${scanRatio.toFixed(2)}x while nodes grew ${nodeRatio.toFixed(2)}x ` +
      `(${small.relaxScans} -> ${large.relaxScans}) — that is superlinear`,
  );
});

test("a pathological single-cell cluster stays bounded", () => {
  // Every node in one project => one centre => the worst case for a uniform grid.
  // This must be measured against a payload that would OTHERWISE be spread: the
  // renderer sets each project's orbit radius to 0 when there is only one, so the
  // default `makePayload` is already single-centre and the old fixture — which
  // passed `projects: 1` — was byte-identical to the plain bounded-work test
  // above (Codex P2-TEST-GAP-006, round 3).
  const spreadOut = runGraph(makePayload(2100, { projects: 8 })).probe;
  const oneCell = runGraph(makePayload(2100, { projects: 8, spread: "single-cell" })).probe;

  assert.equal(new Set(spreadOut.nodes.map((n) => n.project)).size, 8);
  assert.equal(
    new Set(oneCell.nodes.map((n) => n.project)).size,
    1,
    "the collapse must be real, or this is just the spread-out case again",
  );

  const ceiling = oneCell.nodeCount * ITER * oneCell.maxScan;
  assert.ok(
    oneCell.relaxScans <= ceiling,
    `single-cell input scanned ${oneCell.relaxScans}, ceiling ${ceiling}`,
  );

  // Scan COUNT is not the discriminator — measured, the collapsed payload scans
  // slightly *fewer* entries (3.90M vs 3.95M), because a node in a saturated cell
  // exhausts MAX_SCAN sooner than one walking a sparse neighbourhood. What makes
  // it the worst case is density, so that is what is asserted: the layout is
  // deterministic (no clock, no RNG), so this comparison is stable everywhere.
  assert.ok(
    rmsRadius(oneCell.nodes) < rmsRadius(spreadOut.nodes),
    "the single-centre payload must lay out more concentrated than the 8-centre one",
  );
});

/** RMS distance from the centroid — how tightly a layout is packed. */
function rmsRadius(nodes) {
  const cx = nodes.reduce((a, n) => a + n.x, 0) / nodes.length;
  const cy = nodes.reduce((a, n) => a + n.y, 0) / nodes.length;
  const sum = nodes.reduce((a, n) => a + (n.x - cx) ** 2 + (n.y - cy) ** 2, 0);
  return Math.sqrt(sum / nodes.length);
}

/* Wall-clock perf, measured SCALE-FREE.
 *
 * The previous version of this asserted absolute budgets — 350ms at 2,100 nodes
 * and 650ms at 4,000 — derived as "roughly 2x the time measured on this host".
 * That cannot work as a cross-hardware gate, and CI proved it: a macOS runner is
 * ~3.5x slower than the author's laptop, so healthy code measured 926ms there and
 * failed the 650ms line. It failed on the py3.13 cell and passed on py3.11 of the
 * SAME runner image — and the Python version cannot affect a `node --test` run,
 * so the test was measuring runner contention, not the renderer.
 *
 * Its stated justification had silently inverted, too. The comment claimed "a
 * return to quadratic (1,256ms at 4,000) still fails" — but 1,256ms was also a
 * laptop number. On that CI runner a quadratic regression would land near
 * 3,200ms, so a 650ms line says nothing about quadratic there; it only says the
 * machine was busy.
 *
 * What IS hardware-independent is the RATIO between two sizes timed in the same
 * process on the same machine: contention and CPU speed scale both ends. Each
 * size is timed best-of-3, because timing noise only ever ADDS time, which makes
 * the minimum the robust estimator.
 *
 * The threshold is derived from measurement, not from the 3.63x you would predict
 * for quadratic — because wall-clock is a DAMPED signal. Per-run fixed costs
 * (building the payload, constructing 4,000 node objects, harness setup) scale
 * linearly and dilute the superlinear component. Removing BOTH scan bounds to
 * restore the uncapped loop measured 3.29x in scans but only 2.64x in time.
 *
 *     healthy      1.77 - 1.91   best-of-3, six runs on this host
 *     uncapped     2.64          (scans 3.29x, damped to 2.64x in time)
 *     ceiling      2.30          20% above healthy, 13% below the real regression
 *
 * THREE GUARDS, EACH CATCHING WHAT THE OTHERS STRUCTURALLY CANNOT — do not
 * collapse them:
 *
 *   - "scans grow linearly" is the PRIMARY guard: deterministic, hardware-free,
 *     and more sensitive than this one. It fired at 2.30x scans on a partial
 *     mutation (inner bound removed) that this timing test did not catch at all.
 *     If you are tempted to tighten the ratio below, tighten that instead.
 *   - This test catches superlinear work OUTSIDE the instrumented relaxation
 *     loop — a new O(n^2) pass elsewhere in layout would not move `relaxScans`
 *     by one, and no scan-count assertion could ever see it.
 *   - The absolute backstop after it catches a UNIFORM slowdown — expensive work
 *     per scan leaves both the scan count and this ratio unchanged while every
 *     size gets slower together.
 */
const RATIO_CEILING = 2.3;

/** Best-of-`runs` wall-clock ms for `fn`. Min, because noise only adds time. */
function fastestMs(fn, runs = 3) {
  let best = Infinity;
  for (let i = 0; i < runs; i++) {
    const started = process.hrtime.bigint();
    fn();
    best = Math.min(best, Number(process.hrtime.bigint() - started) / 1e6);
  }
  return best;
}

test("layout TIME scales linearly with node count, not quadratically", () => {
  runGraph(makePayload(500)); // warm-up: the first run pays JIT compilation
  const small = fastestMs(() => runGraph(makePayload(2100)));
  const large = fastestMs(() => runGraph(makePayload(4000)));
  const ratio = large / small;

  assert.ok(
    ratio <= RATIO_CEILING,
    `layout time grew ${ratio.toFixed(2)}x (${small.toFixed(0)}ms -> ${large.toFixed(0)}ms) ` +
      `while nodes grew 1.90x — healthy is 1.77-1.91, the uncapped loop was 2.64, ` +
      `ceiling ${RATIO_CEILING}`,
  );
});

test("layout is not catastrophically slow in absolute terms", () => {
  // Deliberately NOT a perf budget — the ratio above is the perf guard. This
  // catches only what a ratio structurally cannot see: a UNIFORM slowdown, where
  // some expensive per-node work scales with everything else and leaves the ratio
  // at ~1.9 while every size gets 10x slower.
  //
  // So it is set from the slowest healthy measurement ever OBSERVED (926ms on a
  // contended macOS CI runner), not from a multiple of this laptop's number —
  // ~5x that, which no amount of runner contention has approached. If this ever
  // fires, the answer is to investigate, not to raise the number.
  const ms = fastestMs(() => runGraph(makePayload(4000)));
  assert.ok(ms < 5000, `layout took ${ms.toFixed(0)}ms at 4,000 nodes — something is badly wrong`);
});

test("bounding the work did not clump the graph", () => {
  // Round 2's push-counting cap produced ~1,869 overlapping-radius pairs at 2,100
  // nodes against 572 for the original uncapped loop — it bought speed with
  // legibility. Quality is part of this fix, not a side effect to trade away, so
  // the budget is the ORIGINAL's number: the bounded layout must be at least as
  // well separated as the unbounded one it replaced.
  const { probe } = runGraph(makePayload(2100));
  const nodes = probe.nodes;
  let overlaps = 0;
  for (let i = 0; i < nodes.length; i++) {
    for (let j = i + 1; j < nodes.length; j++) {
      const dx = nodes[i].x - nodes[j].x, dy = nodes[i].y - nodes[j].y;
      if (Math.hypot(dx, dy) < 0.006) overlaps++;   // ~ two node radii in layout units
    }
  }
  assert.ok(
    overlaps <= 572,
    `${overlaps} overlapping pairs at 2,100 nodes — the uncapped loop it replaced had 572`,
  );
});

test("the same payload lays out identically twice (determinism)", () => {
  const a = runGraph(makePayload(400)).probe.nodes.map((n) => [n.x, n.y]);
  const b = runGraph(makePayload(400)).probe.nodes.map((n) => [n.x, n.y]);
  assert.deepEqual(a, b, "same store must draw the same picture on every reload");
});

// --- P2-UX-API-CONTRACT-005: the idle graph must actually park ---------------

test("an untouched visible graph parks instead of repainting forever", () => {
  const g = runGraph(makePayload(60, { edges: 20 }));
  g.tick(2000);   // far beyond the 12s (720-frame) drift budget
  assert.equal(g.pending(), 0, "still scheduling frames after the drift budget");
  assert.equal(g.probe.isParked(), true);
});

test("it keeps drifting for the budget before parking", () => {
  const g = runGraph(makePayload(60, { edges: 20 }));
  g.tick(120);    // 2s at 60Hz — well inside the budget
  assert.ok(g.pending() > 0, "parked early: ambient motion should still be running");
});

test("interaction wakes a parked graph and re-arms the full drift budget", () => {
  const g = runGraph(makePayload(60, { edges: 20 }));
  g.tick(2000);
  assert.equal(g.probe.isParked(), true);

  g.canvas.dispatch("mousemove", { clientX: 100, clientY: 100 });
  assert.equal(g.probe.isParked(), false, "hover must wake the renderer");

  // Waking is not enough: the interaction has to spend a FRESH budget, or the
  // graph parks again on the next settled frame and the wake is a single
  // repaint. Two seconds of frames must still be running.
  g.tick(120);
  assert.ok(
    g.pending() > 0,
    "parked again immediately after waking — interaction did not re-arm the drift budget",
  );
});

/* Round 3 found the suite's one interaction test was the whole of its coverage:
 * a faithful mutation — deleting `wake()` from the keyboard handler — left all 15
 * tests green, and resize/visibility could not be driven at all because the
 * harness discarded document listeners and the ResizeObserver callback (Codex
 * P2-TEST-GAP-006). Each wake path now gets its own driven test. */

test("arrow-key navigation wakes a parked graph and re-arms the budget", () => {
  const g = runGraph(makePayload(60, { edges: 20 }));
  g.tick(2000);
  assert.equal(g.probe.isParked(), true);

  g.key("ArrowRight");
  assert.equal(g.probe.isParked(), false, "keyboard navigation must wake the renderer");

  g.tick(120);
  assert.ok(
    g.pending() > 0,
    "parked again immediately — the keyboard path did not re-arm the drift budget",
  );
});

test("Enter on the keyboard selection wakes the graph too", () => {
  const g = runGraph(makePayload(60, { edges: 20 }));
  g.key("ArrowRight"); // establish a keyboard index; Enter is a no-op without one
  g.tick(2000);
  assert.equal(g.probe.isParked(), true);

  g.key("Enter");
  assert.equal(g.probe.isParked(), false, "selecting via Enter must wake the renderer");
});

test("a resize wakes a parked graph", () => {
  const g = runGraph(makePayload(60, { edges: 20 }));
  g.tick(2000);
  assert.equal(g.probe.isParked(), true);

  g.resize();
  assert.equal(g.probe.isParked(), false, "resizing the canvas must wake the renderer");
});

test("hiding the document sleeps the loop and revealing it wakes it", () => {
  const g = runGraph(makePayload(60, { edges: 20 }));
  assert.ok(g.pending() > 0, "a fresh graph should be animating");

  g.setHidden(true);
  assert.equal(g.probe.isParked(), true, "a hidden tab must not keep painting");
  assert.equal(g.pending(), 0, "and must cancel the frame it had queued");

  g.setHidden(false);
  assert.equal(g.probe.isParked(), false, "returning to the tab must resume");
});

test("a wake while the document is hidden schedules nothing", () => {
  // `wake()` re-arms the drift budget unconditionally but must not schedule a
  // frame behind a hidden tab — otherwise a background tab paints forever.
  const g = runGraph(makePayload(60, { edges: 20 }));
  g.setHidden(true);
  g.probe.wake();
  assert.equal(g.probe.isParked(), true);
  assert.equal(g.pending(), 0);
});

// --- XSS / prototype safety, executed rather than regexed --------------------

const DANGEROUS = ["constructor", "__proto__", "prototype", "toString", "hasOwnProperty"];

test("a dangerous key that is NOT a node looks absent, not inherited", () => {
  // This is the failure the null-prototype maps exist to prevent, and the case a
  // fixture of real dangerous-id nodes cannot detect: on a plain `{}`,
  // `byId["constructor"]` answers with an inherited function, so the renderer's
  // `if (!byId[id]) return` existence guard PASSES for a node that does not
  // exist — the camera then frames `undefined`, goes NaN, and the canvas stays
  // blank for the life of the page.
  const { probe } = runGraph(makePayload(5)); // ordinary ids only
  for (const key of DANGEROUS) {
    assert.equal(probe.byId[key], undefined, `byId[${key}] must be absent, not inherited`);
    assert.equal(probe.adj[key], undefined, `adj[${key}] must be absent, not inherited`);
  }
});

test("dangerous JS object keys work as real node ids", () => {
  const payload = makePayload(DANGEROUS.length);
  DANGEROUS.forEach((id, i) => { payload.nodes[i].id = id; });
  payload.edges = [{ a: DANGEROUS[0], b: DANGEROUS[1], kind: "session", source: "journal" }];

  const { probe } = runGraph(payload);
  for (const id of DANGEROUS) {
    assert.equal(typeof probe.byId[id], "object", `byId[${id}] must be the node, not inherited`);
    assert.equal(probe.byId[id].id, id);
  }
  assert.equal({}.polluted, undefined, "Object.prototype must not be polluted");
  assert.equal(Object.prototype.hasOwnProperty.call({}, "x"), false, "prototype chain intact");
  // Every node still laid out — the failure mode was a NaN camera and a blank canvas.
  for (const n of probe.nodes) {
    assert.ok(Number.isFinite(n.x) && Number.isFinite(n.y), `${n.id} has a NaN position`);
  }
  // The adjacency map keyed by a dangerous id must be a real edge record.
  assert.equal(probe.adj[DANGEROUS[0]][DANGEROUS[1]], true);
});

test("a ?node= id that matches nothing is inert", () => {
  const g = runGraph(makePayload(5), { href: "http://127.0.0.1:8765/graph?node=__proto__" });
  for (const n of g.probe.nodes) {
    assert.ok(Number.isFinite(n.x) && Number.isFinite(n.y));
  }
});

// --- P2-CORRECTNESS-004: proposal labels, executed ---------------------------

test("proposal labels never claim a kind or an install state they do not have", () => {
  const { probe } = runGraph(makePayload(1));
  const label = probe.kindLabel;
  assert.equal(label({ kind: "proposal", status: "proposed", type: "rule" }), "proposed rule");
  assert.equal(label({ kind: "proposal", status: "proposed", type: "skill" }), "proposed skill");
  assert.equal(
    label({ kind: "proposal", status: "accepted", type: "skill", artifact: "live" }),
    "installed skill",
  );
  assert.equal(
    label({ kind: "proposal", status: "accepted", type: "skill", artifact: "missing" }),
    "accepted, artifact missing skill",
  );
  assert.equal(
    label({ kind: "proposal", status: "accepted", type: "rule", artifact: "orphaned" }),
    "accepted, artifact edited away rule",
  );
  // unresolvable / unknown must claim nothing about liveness
  assert.equal(
    label({ kind: "proposal", status: "accepted", type: "rule", artifact: "unresolvable" }),
    "accepted rule",
  );
  assert.equal(
    label({ kind: "proposal", status: "accepted", type: null, artifact: null }),
    "accepted proposal",
  );
});

// --- zero cloud dependency, permanently --------------------------------------

test("the renderer makes no outbound request of any kind", () => {
  const source = graphScript();
  for (const forbidden of ["fetch(", "XMLHttpRequest", "WebSocket", "importScripts", "//cdn", "https://"]) {
    assert.ok(!source.includes(forbidden), `renderer references ${forbidden}`);
  }
});
