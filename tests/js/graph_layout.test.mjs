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

/* Stated startup budgets. These are wall-clock, so they are the softest guard
 * here — the scan-count and linearity assertions above are the real ones. They
 * are set at roughly 2x the measured time on this host so slower CI hardware has
 * headroom, while a return to quadratic (which was 1,256ms at 4,000) still fails.
 *
 * Measured on the review host, in this Node sandbox:
 *              before this fix    now
 *   2,100      346ms              145ms
 *   4,000      1,256ms            264ms
 */
test("startup layout stays inside its budget at 2,100 nodes (the real store size)", () => {
  const started = process.hrtime.bigint();
  runGraph(makePayload(2100));
  const ms = Number(process.hrtime.bigint() - started) / 1e6;
  assert.ok(ms < 350, `layout took ${ms.toFixed(1)}ms at 2,100 nodes (budget 350ms)`);
});

test("startup layout stays inside its budget at 4,000 nodes", () => {
  const started = process.hrtime.bigint();
  runGraph(makePayload(4000));
  const ms = Number(process.hrtime.bigint() - started) / 1e6;
  assert.ok(ms < 650, `layout took ${ms.toFixed(1)}ms at 4,000 nodes (budget 650ms)`);
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
