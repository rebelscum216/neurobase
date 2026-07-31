/* A dependency-free DOM/rAF stub good enough to run the *shipped* graph script.
 *
 * Round 2's lesson (Codex P2-TEST-GAP-006): a source assertion about this file
 * is worthless — one stayed green while the O(n) claim it "guarded" was false.
 * So these tests execute the real `templates/graph.html` script block, with the
 * payload the server would have embedded, and assert on behaviour.
 *
 * No npm install, no dependencies, no node_modules: `node --test` and this file.
 * (The repo does carry a `package.json` — an npm name reservation, not a manifest
 * anything installs from.) The
 * stub implements only what the renderer actually touches; anything it reaches
 * for that is missing throws loudly rather than silently returning undefined,
 * so the harness cannot drift into pretending.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import vm from "node:vm";

const HERE = dirname(fileURLToPath(import.meta.url));
export const TEMPLATE = join(HERE, "..", "..", "src", "neurobase", "webui", "templates", "graph.html");

/** The renderer's own <script> body, lifted verbatim from the shipped template. */
export function graphScript() {
  const html = readFileSync(TEMPLATE, "utf8");
  // The last <script>...</script> in the file is the renderer; the first is the
  // application/json payload block.
  const blocks = [...html.matchAll(/<script>\n([\s\S]*?)\n<\/script>/g)];
  if (blocks.length !== 1) {
    throw new Error(`expected exactly 1 renderer <script> block, found ${blocks.length}`);
  }
  return blocks[0][1];
}

function el(tag, id) {
  const node = {
    tagName: tag,
    id: id || "",
    className: "",
    style: {},
    dataset: {},
    children: [],
    _listeners: Object.create(null),
    _text: "",
    inert: false,
    get textContent() { return this._text; },
    set textContent(v) { this._text = String(v); },
    get innerHTML() { return this._html || ""; },
    set innerHTML(v) { this._html = String(v); },
    setAttribute() {},
    removeAttribute() {},
    getAttribute() { return null; },
    appendChild(c) { this.children.push(c); return c; },
    contains() { return false; },
    focus() {},
    addEventListener(type, fn) { (this._listeners[type] || (this._listeners[type] = [])).push(fn); },
    removeEventListener() {},
    dispatch(type, ev) {
      for (const fn of this._listeners[type] || []) fn(ev || {});
    },
    getBoundingClientRect() { return { width: 900, height: 600, left: 0, top: 0 }; },
    querySelector() { return el("div"); },
    closest() { return null; },
    classList: { add() {}, remove() {}, contains() { return false; } },
    getContext() {
      const noop = () => {};
      return new Proxy({}, {
        get(_t, prop) {
          if (prop === "canvas") return node;
          if (prop === "measureText") return () => ({ width: 10 });
          return noop;
        },
        set() { return true; },
      });
    },
  };
  node.parentElement = node.parentElement || null;
  return node;
}

/**
 * Run the shipped renderer against `payload`.
 * Returns { probe, tick, frames, wake } — `probe` is `window.__nbGraphProbe`.
 */
export function runGraph(payload, opts = {}) {
  const reduced = opts.reduced === true;
  const canvas = el("canvas", "graph");
  const wrap = el("div");
  wrap.children.push(canvas);
  canvas.parentElement = wrap;

  const byId = Object.create(null);
  for (const id of ["graph", "graph-data", "tip", "inspector", "insp-in"]) {
    byId[id] = id === "graph" ? canvas : el("div", id);
  }
  byId["graph-data"].textContent = JSON.stringify(payload);

  let now = 0;
  const queue = [];
  let nextRaf = 1;

  // Document listeners were discarded here, and the ResizeObserver callback
  // below — so `visibilitychange` and resize could not be driven at all, and the
  // suite's claim to cover "interaction" rested on one `mousemove` (Codex
  // P2-TEST-GAP-006, round 3). A harness that swallows the events it is meant to
  // deliver is the same false confidence the source assertions gave.
  const documentListeners = Object.create(null);
  const document_ = {
    hidden: false,
    documentElement: el("html"),
    getElementById: (id) => byId[id] || null,
    createElement: (tag) => el(tag),
    addEventListener(type, fn) {
      (documentListeners[type] || (documentListeners[type] = [])).push(fn);
    },
    removeEventListener() {},
    activeElement: null,
    body: el("body"),
  };
  let resizeCallback = null;

  const sandbox = {
    console,
    JSON,
    Math,
    Object,
    Array,
    String,
    Number,
    isNaN,
    parseFloat,
    parseInt,
    URL,
    document: document_,
    performance: { now: () => now },
    devicePixelRatio: 1,
    requestAnimationFrame(fn) {
      const id = nextRaf++;
      queue.push({ id, fn });
      return id;
    },
    cancelAnimationFrame(id) {
      const i = queue.findIndex((q) => q.id === id);
      if (i >= 0) queue.splice(i, 1);
    },
    getComputedStyle: () => ({ getPropertyValue: () => "#000" }),
    matchMedia: () => ({ matches: reduced, addEventListener: () => {} }),
    ResizeObserver: class {
      constructor(fn) { resizeCallback = fn; }
      observe() {}
      disconnect() {}
    },
    history: { pushState() {}, replaceState() {} },
    location: { href: opts.href || "http://127.0.0.1:8765/graph" },
    setTimeout: (fn) => { fn(); return 0; },
    clearTimeout: () => {},
  };
  const windowListeners = Object.create(null);
  sandbox.addEventListener = (type, fn) => {
    (windowListeners[type] || (windowListeners[type] = [])).push(fn);
  };
  sandbox.removeEventListener = () => {};
  sandbox.dispatchWindow = (type, ev) => {
    for (const fn of windowListeners[type] || []) fn(ev || {});
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;

  vm.createContext(sandbox);
  vm.runInContext(graphScript(), sandbox, { filename: "graph.html:<script>" });

  /** Advance `n` animation frames at 60Hz. Returns frames actually executed. */
  function tick(n) {
    let ran = 0;
    for (let i = 0; i < n; i++) {
      const pending = queue.splice(0, queue.length);
      if (!pending.length) break;
      now += 1000 / 60;
      for (const { fn } of pending) { fn(now); ran++; }
    }
    return ran;
  }

  /* Every driver below throws when the renderer never registered the thing it is
   * meant to drive, rather than no-oping: a test that silently drives nothing is
   * the exact vacuity this suite exists to close. */
  function dispatchDocument(type, ev) {
    const fns = documentListeners[type] || [];
    if (!fns.length) throw new Error(`renderer registered no document "${type}" listener`);
    for (const fn of fns) fn(ev || {});
  }

  return {
    probe: sandbox.window.__nbGraphProbe,
    window: sandbox.window,
    canvas,
    tick,
    pending: () => queue.length,
    advanceClock: (ms) => { now += ms; },
    dispatchDocument,
    /** Flip `document.hidden` and fire the visibilitychange the browser would. */
    setHidden(hidden) {
      document_.hidden = hidden;
      dispatchDocument("visibilitychange", {});
    },
    /** Fire the ResizeObserver callback the renderer registered on the wrapper. */
    resize() {
      if (!resizeCallback) throw new Error("renderer never constructed a ResizeObserver");
      resizeCallback([{ target: wrap }]);
    },
    /** A keyboard event shaped like the real one — `preventDefault` included. */
    key(name) {
      canvas.dispatch("keydown", { key: name, preventDefault() {} });
    },
  };
}

/** A synthetic payload of `n` nodes. `spread` controls how clustered they are. */
export function makePayload(n, { projects = 1, edges = 0, spread = "normal" } = {}) {
  // The renderer collapses `rad` to 0 for a single project, so the DEFAULT
  // payload is already one centre — asking for `single-cell` at `projects: 1`
  // produces a byte-identical fixture, which is how the "pathological cluster"
  // test came to be a duplicate of the plain bounded-work one (Codex
  // P2-TEST-GAP-006, round 3). Refuse it rather than let it look like coverage.
  if (spread === "single-cell" && projects === 1) {
    throw new Error(
      "single-cell with projects: 1 is identical to the default payload — " +
        "pass projects > 1 so the collapse is a real one",
    );
  }
  const nodes = [];
  const names = [];
  for (let p = 0; p < projects; p++) names.push("proj" + p);
  for (let i = 0; i < n; i++) {
    nodes.push({
      id: "s:" + names[i % projects] + ":sess-" + i,
      kind: "session",
      label: "session " + i,
      sub: "Claude · " + names[i % projects],
      project: spread === "single-cell" ? names[0] : names[i % projects],
      agent: "claude",
      captured_at: "2026-07-01T09:00:00Z",
      prompts: 3,
      resolved: true,
      file: "f" + i + ".md",
    });
  }
  const edgeList = [];
  for (let i = 0; i < edges && i + 1 < n; i++) {
    edgeList.push({ a: nodes[i].id, b: nodes[i + 1].id, kind: "session", source: "journal" });
  }
  return {
    nodes,
    edges: edgeList,
    counts: { sessions: n, facts: 0, proposals: 0, edges: edgeList.length, hidden_sessions: 0 },
    projects: spread === "single-cell" ? [names[0]] : names,
  };
}
