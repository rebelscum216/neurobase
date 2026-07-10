# Neurobase VS Code Extension — Execution Plan

## 1. Objective

Build an optional VS Code extension that provides a graphical interface for Neurobase while preserving the existing architecture:

- The Python package remains the source of business logic.
- `~/neurobase/` markdown files remain the source of truth.
- The extension introduces no hosted backend, telemetry, cloud requirement, or new database.
- Claude Code, Codex CLI, the CLI, and Obsidian continue to work without VS Code.
- The extension communicates with Neurobase through stable, versioned, machine-readable interfaces.
- Anything that changes files outside Neurobase’s own store remains consent-first and reversible.
- Suggested skills and rules are never installed automatically.

The extension is a client of Neurobase, not a replacement implementation.

---

## 2. Recommended Product Scope

### Extension v0.1

The first release should include:

1. Neurobase Activity Bar container
2. Current-project status sidebar
3. Markdown fact and node browser/inspector
4. “Remember This” command
5. “Curate Now” command
6. “Run Doctor” command
7. Recent memory activity derived from existing authoritative data
8. Clear setup and error states
9. Local VSIX packaging

### Extension v0.2

Add, after a graph prototype proves that the derived relationships are useful:

1. Memory Graph editor tab
2. Recommendation queue
3. Skill proposal details
4. Editable `SKILL.md` preview
5. Accept, reject, and edit workflows
6. Proposed install diff
7. Project-level versus user-level target selection
8. Recommendation metrics

Recommendation features depend on the Phase 8 recommender contracts being complete
and stable. The graph does not.

### Explicitly out of scope for v0.1

- Reimplementing the curator in TypeScript
- Directly parsing all Neurobase markdown formats inside the extension
- Editing raw captures
- Automatic skill installation
- Cloud sync
- Team features
- Telemetry
- Background LLM calls initiated by the extension
- Desktop application packaging
- Support for non-VS Code editors

---

## 3. Architecture

```text
VS Code
│
├── Neurobase Extension Host — TypeScript
│   ├── command registration
│   ├── Activity Bar views
│   ├── webview panels
│   ├── workspace/project resolution
│   ├── CLI process manager
│   └── user notifications
│
├── Shared Webview Frontend — React + TypeScript
│   ├── memory graph
│   ├── fact inspector
│   ├── activity timeline
│   └── skill studio later
│
└── Neurobase Python CLI
    ├── project resolution
    ├── store reads and writes
    ├── curation
    ├── doctor
    ├── recall
    ├── recommendations later
    └── JSON presentation API
         │
         └── ~/neurobase/
             ├── raw/
             ├── curated/
             ├── nodes/
             ├── .tombstones/
             └── index.md
```

### Key rule

The extension should never depend on undocumented markdown parsing behavior.

Instead, the Python package should expose structured JSON specifically for clients.

This ensures:

- one implementation of store rules;
- one implementation of project resolution;
- one implementation of pinned-fact protection;
- one implementation of proposal acceptance;
- easier schema versioning;
- less drift between CLI and UI.

---

## 4. Repository Layout

Add a new top-level `extension/` directory without disturbing the existing Python package:

```text
neurobase/
├── src/neurobase/
├── tests/
├── docs/
├── extension/
│   ├── package.json
│   ├── package-lock.json
│   ├── tsconfig.json
│   ├── esbuild.js
│   ├── .vscodeignore
│   ├── src/
│   │   ├── extension.ts
│   │   ├── commands/
│   │   │   ├── openGraph.ts
│   │   │   ├── remember.ts
│   │   │   ├── curate.ts
│   │   │   ├── doctor.ts
│   │   │   └── openSource.ts
│   │   ├── cli/
│   │   │   ├── NeurobaseClient.ts
│   │   │   ├── executableResolver.ts
│   │   │   ├── processRunner.ts
│   │   │   ├── schemas.ts
│   │   │   └── errors.ts
│   │   ├── views/
│   │   │   ├── ProjectOverviewProvider.ts
│   │   │   ├── MemoryTreeProvider.ts
│   │   │   └── SuggestionsProvider.ts
│   │   ├── panels/
│   │   │   ├── GraphPanel.ts
│   │   │   ├── ActivityPanel.ts
│   │   │   └── SkillPanel.ts
│   │   └── util/
│   │       ├── nonce.ts
│   │       ├── logging.ts
│   │       └── workspace.ts
│   ├── webview/
│   │   ├── vite.config.ts
│   │   └── src/
│   │       ├── main.tsx
│   │       ├── App.tsx
│   │       ├── graph/
│   │       ├── activity/
│   │       ├── skills/
│   │       └── shared/
│   ├── media/
│   │   ├── neurobase.svg
│   │   └── icon.png
│   └── test/
│       ├── suite/
│       ├── fixtures/
│       └── mockedCli/
├── scripts/
│   ├── ci.py
│   └── ci_extension.py
└── .github/workflows/
    ├── ci.yml
    └── extension.yml
```

Do not repurpose the root placeholder `package.json`. Keep the actual extension package under `extension/`.
Use one Node package and one lockfile under `extension/`; the webview is a build
entry, not a separately installed package. Pin supported Node and npm versions.

---

## 5. Phase A — Define the Python Client Contract

### Goal

Expose a stable JSON interface that the extension can consume without reading internal files directly.

### Recommended CLI namespace

The namespace includes optional later capabilities; the handshake is authoritative
about which commands the installed CLI supports.

```bash
neurobase client handshake
neurobase client project
neurobase client status
neurobase client graph
neurobase client activity
neurobase client remember
neurobase client curate
neurobase client doctor
neurobase client open-path
```

A dedicated `client` namespace is safer than gradually attaching inconsistent `--json` flags across commands.

### Transport contract

The client interface is a machine protocol, not a human CLI surface.

- Read-only commands MAY use scalar arguments such as `--cwd` and a slug.
- Mutating commands and commands carrying user content MUST read one UTF-8 JSON
  request from stdin. Remembered text must never appear in argv, process listings,
  shell history, or routine logs.
- Every invocation writes exactly one JSON object to stdout and no presentation
  text. Diagnostics may go to stderr, but must never echo remembered content.
- Success exits `0`. A well-formed domain error envelope exits `2`, protocol
  incompatibility exits `3`, an internal failure exits `1`, and cancellation exits
  `130`. The extension parses a JSON envelope whenever stdout contains one,
  regardless of exit status. Spawn failure and timeout are transport errors.
- Requests and responses have documented byte limits. The initial limits are 1 MiB
  for requests and 10 MiB for responses; oversized input/output fails with a
  stable error code.
- All text is UTF-8. Unknown request fields are rejected for mutating commands and
  ignored for read-only commands; unknown response fields must be ignored by
  clients.
- Cancellation may terminate the child process only before the Python operation's
  documented commit point. After that point, the UI reports completion or an
  indeterminate result and refreshes authoritative state.

Example request:

```json
{"protocol": 1, "cwd": "/path/to/repo", "content": "Prefer uv over pip."}
```

### Protocol evolution

- Protocol versions are integers, versioned independently from the CLI and store.
- The handshake reports `protocol_min`, `protocol_max`, and capabilities. The
  extension selects the highest mutually supported version.
- Additive response fields and capabilities do not require a protocol bump.
- Removing or reinterpreting a field, changing an error code, or changing mutation
  semantics requires a new protocol version.
- Optional features are capability-gated; CLI and extension versions never need
  to match exactly.
- Error codes, exit behavior, request limits, and field optionality are part of the
  documented contract and have fixture tests.

### Required commands

#### `neurobase client handshake`

Purpose:

- verify that the extension found the correct executable;
- report CLI version;
- report client protocol version;
- report store schema version;
- report supported capabilities.

Example:

```json
{
  "ok": true,
  "cli_version": "0.1.0",
  "protocol_min": 1,
  "protocol_max": 1,
  "store_schema": 1,
  "capabilities": {
    "graph": false,
    "activity": true,
    "remember": true,
    "recommendations": false
  }
}
```

#### `neurobase client project --cwd <path>`

Example:

```json
{
  "ok": true,
  "project": {
    "enabled": true,
    "slug": "neurobase",
    "root": "/path/to/repo",
    "branch": "main",
    "memory_path": "/Users/name/neurobase/projects/neurobase/memory"
  }
}
```

#### `neurobase client status --cwd <path>`

Example:

```json
{
  "ok": true,
  "project": "neurobase",
  "health": "healthy",
  "active_facts": 42,
  "raw_unconsumed": 0,
  "nodes": 1,
  "tombstones": 3,
  "last_curated_at": "2026-07-10T13:44:00Z",
  "brain": {
    "backend": "claude-cli",
    "available": true
  },
  "agents": {
    "claude": "configured",
    "codex": "configured"
  }
}
```

#### `neurobase client graph --cwd <path>`

Example:

```json
{
  "ok": true,
  "project": "neurobase",
  "generated_at": "2026-07-10T13:44:00Z",
  "nodes": [
    {
      "id": "fact:markdown-truth",
      "kind": "fact",
      "label": "Markdown truth",
      "summary": "Markdown files remain authoritative.",
      "source_id": "fact:markdown-truth",
      "derivation": "stored",
      "status": "active",
      "pinned": false,
      "updated_at": "2026-07-10T13:40:00Z"
    },
    {
      "id": "node:neurobase-status",
      "kind": "synthesis",
      "label": "Neurobase status",
      "source_id": "node:neurobase-status",
      "derivation": "stored"
    }
  ],
  "edges": [
    {
      "source": "fact:markdown-truth",
      "target": "node:neurobase-status",
      "kind": "included_in_generation",
      "derivation": "inferred"
    }
  ]
}
```

### Graph derivation rules

For the graph prototype and optional v0.2 graph, nodes should represent:

- active curated facts;
- synthesis nodes;
- source agents;
- projects in the all-project view;
- raw evidence files only when explicitly expanded.

Edges should represent only relationships the store can support:

- fact → synthesis node as `included_in_generation`, meaning that the fact was in
  the active input set when the node was regenerated; it MUST NOT be labeled as
  semantic evidence or individual attribution;
- raw capture → fact through provenance;
- fact → fact through supersession;
- fact → project;
- capture → agent;
- proposal → evidence later.

The graph is derived on request and is never written as authoritative state.

Every node and edge must include `derivation: "stored" | "inferred"`. Provenance
and supersession are stored relationships. Synthesis membership and project
membership are inferred presentation relationships. Graph ordering is stable by
kind, then ID. A prototype must demonstrate that this graph is useful before it
becomes a v0.2 release commitment.

### Source references and path resolution

Client responses must expose an opaque `source_id`, not a webview-controlled path,
for actions that open files. `neurobase client open-path` accepts that ID plus the
project identity, resolves it in Python, canonicalizes symlinks, and returns a path
only if it names a recognized file inside that project's memory tree. Unknown,
absolute, traversal, cross-project, and symlink-escape references fail with
`SOURCE_NOT_ALLOWED`.

The extension host may open the validated result. Webviews never supply filesystem
paths and never concatenate relative paths onto `memory_path`.

### Standard error envelope

```json
{
  "ok": false,
  "error": {
    "code": "PROJECT_NOT_ENABLED",
    "message": "This workspace is not registered with Neurobase.",
    "remedy": "Run neurobase enable in the workspace."
  }
}
```

Do not make the extension interpret arbitrary stderr text.

### Python implementation location

```text
src/neurobase/client/
├── protocol.py
├── models.py
├── graph.py
├── activity.py
└── commands.py
```

### Tests

Add Python tests for:

- protocol handshake;
- project resolution;
- empty store;
- disabled project;
- healthy project;
- graph determinism, when the graph capability is implemented;
- pinned fact representation;
- provenance edges;
- supersession edges;
- malformed files being skipped safely;
- error envelope stability;
- protocol version output;
- stdin transport, UTF-8, and size limits;
- remembered content absent from argv, stderr, and routine logs;
- stable exit-code behavior;
- protocol-range negotiation and unknown response fields;
- opaque source resolution, traversal, and symlink escape;
- stored-versus-inferred graph relationship labels, when graph is implemented.

### Phase A completion gate

- All advertised client commands return valid JSON only. Optional graph and
  recommendation commands are advertised only when implemented.
- Graph output is deterministic for the same store when that capability exists.
- No extension-specific logic bypasses the store API or shared Python application
  services.
- Existing CLI behavior is unchanged.
- Existing Python CI remains green.
- Protocol version is documented.

---

## 6. Phase B — Scaffold the VS Code Extension

### Goal

Produce an installable extension that can detect Neurobase and show current-project status.

### Technology

- TypeScript
- VS Code Extension API
- esbuild for extension bundling
- React + Vite for webviews
- Zod for runtime validation of Python responses
- Vitest or Jest for frontend/unit tests
- `@vscode/test-electron` for integration tests
- `@vscode/vsce` for packaging

### Initial commands

Register:

```text
Neurobase: Open Memory Workbench
Neurobase: Remember Selection
Neurobase: Remember…
Neurobase: Curate Now
Neurobase: Run Doctor
Neurobase: Enable Current Project
Neurobase: Open Memory Folder
Neurobase: Refresh
```

### Activity Bar contribution

Create a Neurobase icon and container with:

1. Project Memory
2. Recent Facts
3. Suggestions
4. System Health

For v0.1, Suggestions should show an honest unavailable state if the recommender is not implemented.

### Activation

Use lazy activation:

```json
{
  "activationEvents": [
    "onView:neurobase.project",
    "onCommand:neurobase.openWorkbench",
    "onCommand:neurobase.remember"
  ]
}
```

Avoid `workspaceContains:.git`: it activates in nearly every development
workspace, whether Neurobase is enabled or not. Modern VS Code versions infer
activation for contributed commands and views; keep explicit events only when the
supported minimum VS Code version requires them.

### Phase B completion gate

- Pressing `F5` launches an Extension Development Host.
- Neurobase appears in the Activity Bar.
- The extension resolves or fails to resolve the CLI cleanly.
- Current project status is displayed.
- Disabled projects show an Enable action.
- Missing installations show setup guidance.
- No webview is required yet.

---

## 7. Phase C — Build the CLI Process Bridge

### Goal

Create one robust TypeScript client for all communication with Python.

### `NeurobaseClient` interface

```ts
interface NeurobaseClient {
  handshake(): Promise<Handshake>;
  resolveProject(cwd: string): Promise<ProjectState>;
  getStatus(cwd: string): Promise<ProjectStatus>;
  getGraph(cwd: string): Promise<GraphModel>;
  getActivity(cwd: string): Promise<ActivityModel>;
  remember(cwd: string, content: string): Promise<RememberResult>;
  curate(cwd: string): Promise<CurateResult>;
  doctor(cwd?: string): Promise<DoctorResult>;
  resolveSource(cwd: string, sourceId: string): Promise<ResolvedSource>;
}
```

### Executable resolution order

1. User setting: `neurobase.executablePath`
2. `neurobase` on `PATH`
3. Common uv tool location: `~/.local/bin/neurobase`
4. Windows equivalent
5. Failure with guided setup

Do not invoke `python -m neurobase` as a hidden fallback unless explicitly supported and documented.

### Process requirements

- Use `execFile`, not shell string execution.
- Pass arguments as arrays.
- Never concatenate workspace paths into shell commands.
- Set a timeout.
- Capture stdout and stderr separately.
- Reject oversized output.
- Validate returned JSON.
- Display safe error summaries.
- Log verbose diagnostics only to a local VS Code Output Channel.
- Do not send logs anywhere.

### Cancellation

Long-running operations such as curation should support VS Code progress notifications and cancellation where process termination is safe.

The Python contract must document the commit point for each mutating operation.
Cancellation before that point guarantees no mutation. If termination races with
or follows the commit point, the extension must not claim cancellation succeeded;
it refreshes authoritative state and reports an indeterminate or completed result.

### Compatibility handling

If the CLI protocol is newer than the extension supports:

```text
Your Neurobase CLI is newer than this extension.
Update the Neurobase VS Code extension.
```

If the CLI protocol is older:

```text
This extension requires a newer Neurobase CLI.
```

### Phase C completion gate

- All CLI responses are runtime-validated.
- Missing executable behavior is tested.
- Timeout behavior is tested.
- Malformed JSON is tested.
- Protocol mismatch is tested.
- Paths containing spaces are tested.
- Windows path handling is covered in CI.

---

## 8. Phase D — Project Overview Sidebar

### Goal

Make the extension useful before building the full graph.

### Display

```text
NEUROBASE

Project
  neurobase
  main

Memory
  42 active facts
  0 pending captures
  1 status node
  Last curated 18 minutes ago

Agents
  Claude Code       Ready
  Codex CLI         Ready

Brain
  claude-cli        Available

Actions
  Browse Memory
  Remember Something
  Curate Now
  Run Doctor
```

### Refresh triggers

Refresh when:

- workspace folder changes;
- active workspace changes;
- user presses Refresh;
- a command completes;
- watched Neurobase files change;
- VS Code regains focus, with throttling.

### File watching

Watch only the active project’s known Neurobase memory directory.

Do not recursively watch the entire home directory.

Debounce file events because curation writes multiple files atomically.

### Multi-root workspaces

For v0.1:

- show one expandable section per enabled workspace folder;
- use the active editor’s workspace when executing commands;
- ask the user to choose only when the target workspace is genuinely ambiguous.

### Phase D completion gate

- Single-root workspace works.
- Multi-root workspace works.
- Disabled and missing-project states work.
- Refresh behavior does not loop or spam subprocesses.
- Atomic curation writes produce one coherent refresh.

---

## 9. Phase E — Memory Graph Webview

### Goal

Turn the Graph Explorer mockup into a functional VS Code editor tab.

This is a v0.2 candidate. Before committing to the full implementation, build a
small prototype against real stores and verify that provenance, supersession, and
derived membership produce a useful graph rather than a noisy star. If not, prefer
the fact/node browser and inspector.

### Rendering library

Recommended: React Flow.

Use Cytoscape.js instead if graph sizes are expected to reach thousands of nodes early.

### Webview communication

Extension to webview:

```ts
{
  type: "graph.loaded",
  requestId: "uuid",
  payload: graphModel
}
```

Webview to extension:

```ts
{
  type: "fact.openSource",
  payload: {
    sourceId: "fact:markdown-truth"
  }
}
```

Additional message types:

```text
graph.refresh
graph.filterChanged
node.selected
source.open
fact.remember
curation.run
project.changed
```

### Content Security Policy

Every webview must:

- use a random nonce;
- forbid arbitrary remote scripts;
- load assets only through `webview.asWebviewUri`;
- avoid inline script execution;
- define a strict Content Security Policy;
- never load CDN dependencies.

### Graph filters

Support:

- project;
- node kind;
- agent;
- pinned facts;
- active versus tombstoned;
- date range;
- evidence visibility;
- search text.

### Inspector

Selecting a node should show:

- name;
- kind;
- markdown body or summary;
- source file;
- updated time;
- provenance;
- pinned state;
- supersession chain;
- connected nodes;
- Open Source action.

### Open Source behavior

The extension host first resolves `sourceId` through `neurobase client open-path`.
Only the canonical, Python-approved result is passed to VS Code. The webview never
chooses or constructs a path.

Use:

```ts
vscode.workspace.openTextDocument()
vscode.window.showTextDocument()
```

### Performance targets

- Initial display under 1 second for 500 nodes.
- Smooth navigation for 1,000 nodes.
- Graceful aggregation or warning beyond the supported threshold.
- Raw evidence nodes collapsed by default.

### Phase E completion gate

- Real data renders from the Neurobase store.
- Clicking nodes updates the inspector.
- Markdown source opens in VS Code.
- Filters work without rerunning Python where possible.
- Refresh reloads from the CLI.
- Webview survives hide/show.
- Webview state restores after reload where possible.

---

## 10. Phase F — Remember and Curate Workflows

### Remember Selection

Before adding the client command, extract the existing MCP remember behavior into
a shared Python application service such as `remember_fact(...)`. The MCP tool and
client protocol MUST call that same service so redaction, slug collision handling,
project resolution, provenance, and pinned-fact behavior cannot drift.

Flow:

1. Read the selected editor text.
2. Determine the current workspace.
3. Show an editable confirmation input.
4. Call the Python client remember command.
5. Python writes a pinned `user-directed` fact.
6. Refresh the sidebar and graph.
7. Show the resulting fact slug.

Never send the entire open file unless the user explicitly selects it.
Send selected content in the stdin JSON request, never as a command-line argument.

### Remember Manual Entry

Prompt:

```text
What should Neurobase remember?
```

Include project selection in multi-root workspaces when necessary.

### Curate Now

Flow:

1. Show progress.
2. Run the Python client curation command.
3. Display a structured summary.
4. Refresh views.
5. Offer to open the activity panel.

Example summary:

```text
Curated 3 captures
2 facts updated
1 stale fact superseded
42 active facts remain
```

### Dry-run curation

Add a secondary command:

```text
Neurobase: Preview Curation
```

Display the planned upserts and tombstones in a read-only diff-style webview before any changes are applied.

A normal `curate --dry-run` followed by a separate `curate` is not an exact preview:
the LLM can return a different plan and the store can change between calls. Therefore
v0.1 does not present dry-run output as an applyable exact diff.

An exact preview may be added only with a Python-owned two-step contract:

1. `curation preview` returns the plan, an opaque preview token, and a fingerprint
   of every raw/fact input used.
2. `curation apply-preview` applies that exact plan only if the fingerprint still
   matches; otherwise it fails with `PREVIEW_STALE` and performs no mutation.
3. Tokens are local, short-lived, bounded in size, and contain no unredacted data.
4. Tests prove exact-plan application, stale rejection, one-time use, and the
   unconsumed-on-failure invariant.

Until this contract exists, Preview Curation remains out of scope.

### Phase F completion gate

- Selected text can be remembered.
- Manual memory entry works.
- Pinned facts remain protected by Python rules.
- Curation progress and failure states are clear.
- Curation results refresh all views.
- The extension does not write Neurobase store files directly.

---

## 11. Phase G — Memory Workbench

### Goal

Provide transparent visibility into how memory changes over time.

### Data sources

Use:

- curator pass log;
- raw capture metadata;
- curated fact updates;
- supersession relationships;
- node regeneration timestamps;
- recommendation events later.

For v0.1, the activity view may show only events deterministically derivable from
these sources: capture file creation, completed curator summaries, fact timestamps
and supersession/tombstone state, node timestamps, and existing recommendation
ledger events. It must label inferred timestamps as inferred.

It must not claim that a curator pass started or that recall was injected because
the current store does not record those events. If richer history is later desired,
first define an append-only local event-log contract in the behavioral spec and an
ADR, including schema, redaction, atomicity, retention, failure behavior, and tests.
This log remains local and is not telemetry.

### UI

Timeline events:

```text
Session captured
Curator pass completed
Facts updated or merged
Fact superseded
Fact tombstoned
Status node regenerated
Skill candidate proposed
Skill accepted or rejected
```

### Required distinctions

The UI must distinguish:

- raw observation;
- active durable fact;
- tombstoned fact;
- synthesized node;
- pinned fact;
- proposed skill;
- installed skill.

### Phase G completion gate

- Timeline data comes from Python client output.
- Users can open evidence files from events.
- Curator errors and partial runs are visible.
- The workbench does not imply telemetry or remote history.

---

## 12. Phase H — Recommendation and Skill Studio

### Dependency

Begin only after the Python recommender is implemented and its proposal format is stable.

### Python client commands

```bash
neurobase client recommendations list
neurobase client recommendations show <slug>
neurobase client recommendations preview <slug>
neurobase client recommendations accept <slug>
neurobase client recommendations reject <slug>
neurobase client recommendations edit <slug>
```

### UI capabilities

- proposal queue;
- candidate type;
- score;
- recurrence count;
- project breadth;
- agent breadth;
- evidence links;
- editable generated content;
- target scope;
- output path;
- install diff;
- accept;
- reject;
- edit and accept;
- rejection reason.

### Consent flow

Before installation:

1. Render the proposed target content.
2. Show the exact diff.
3. Show the target path.
4. Require explicit confirmation.
5. Let Python perform the write and backup.
6. Refresh proposal status.

The extension must not write `SKILL.md`, `AGENTS.md`, or `CLAUDE.md` directly.

### Phase H completion gate

- Proposal queue reflects the Python ledger.
- Evidence links open correctly.
- Accept/reject decisions persist.
- Installation always shows a diff.
- No proposal can auto-install.
- User-level and project-level targets are explicit.

---

## 13. Configuration

Recommended settings:

```json
{
  "neurobase.executablePath": "",
  "neurobase.autoRefresh": true,
  "neurobase.refreshDebounceMs": 500,
  "neurobase.graph.maxNodes": 1000,
  "neurobase.graph.showRawEvidence": false,
  "neurobase.outputLogLevel": "info"
}
```

Do not add:

- telemetry settings;
- cloud endpoints;
- API key fields;
- agent subscription credentials.

---

## 14. Security Requirements

### Process security

- Use `execFile`.
- Never use `shell: true`.
- Treat workspace paths as data, not command fragments.
- Limit process output size.
- Set timeouts.
- Validate all responses.

### Webview security

- Strict CSP.
- Nonced scripts.
- No remote resources.
- No arbitrary HTML from markdown.
- Sanitize rendered markdown.
- Validate every incoming message.
- Restrict local resource roots.

### File security

- Resolve source paths through Python or a trusted project root.
- Reject path traversal.
- Do not open files outside expected Neurobase or workspace roots without explicit user confirmation.
- Do not modify raw captures.
- Do not bypass backup and consent logic.

### Privacy

- No telemetry.
- No analytics SDK.
- No crash reporting service.
- No remote fonts.
- No CDN dependencies.
- No network access required for extension UI operation.

---

## 15. Testing Strategy

### Python tests

Test:

- client protocol;
- schema stability;
- graph generation;
- activity generation;
- error envelopes;
- project resolution;
- pinned memory behavior;
- recommendation actions later.

### TypeScript unit tests

Test:

- executable resolution;
- process argument construction;
- timeout handling;
- JSON validation;
- protocol compatibility;
- workspace selection;
- path handling;
- error mapping.

### Webview tests

Test:

- graph rendering;
- node selection;
- filters;
- inspector;
- empty states;
- error states;
- large graph handling;
- message validation.

### Extension integration tests

Test:

- activation;
- Activity Bar registration;
- commands;
- mocked CLI responses;
- missing CLI;
- disabled project;
- single-root workspace;
- multi-root workspace;
- open-source command;
- curate workflow;
- remember-selection workflow.

### Manual smoke matrix

Platforms:

- macOS Apple Silicon
- macOS Intel if available
- Windows
- Linux

Scenarios:

- Neurobase installed and healthy
- Neurobase missing
- CLI older than extension
- CLI newer than extension
- project not enabled
- Claude only
- Codex only
- both agents
- empty store
- malformed store file
- path with spaces
- multi-root workspace

---

## 16. CI and Build

### Extension CI

Add a dedicated workflow that runs:

```bash
npm ci
npm run lint
npm run typecheck
npm run test
npm run build
npx vsce package
```

Every command runs with `extension/` as its working directory. CI uses the
committed `extension/package-lock.json` and pinned Node version. Expose the same
sequence through `npm run ci` so `scripts/ci_extension.py` invokes one stable
entry point rather than duplicating the steps.

Run on:

- Ubuntu
- macOS
- Windows

### Root CI integration

Update the repository’s single-source CI entry point so the full local gate includes the extension once it is no longer experimental.

Recommended transition:

1. Keep extension CI separate while scaffolding.
2. Once stable, call `scripts/ci_extension.py` from `scripts/ci.py`.
3. Preserve one documented full gate for contributors.

### Build outputs

Produce:

```text
extension/dist/extension.js
extension/webview/dist/*
neurobase-<version>.vsix
```

Do not commit generated bundles unless the release strategy explicitly requires it.

---

## 17. Packaging and Release

### Development installation

```bash
cd extension
npm install
npm run build
npx vsce package
```

Then:

```text
VS Code
→ Extensions
→ …
→ Install from VSIX
```

### Marketplace release

Release only after:

- clean-machine CLI install is complete;
- client protocol is stable;
- extension install documentation is complete;
- privacy statement clearly says no telemetry;
- icons and screenshots are ready;
- license notices are correct.

### Versioning

Use separate but compatible versions:

- Neurobase CLI version
- VS Code extension version
- client protocol version

Example:

```text
CLI: 0.2.0
Extension: 0.1.0
Client protocol: 1
Store schema: 1
```

Do not require exact CLI/extension version matches. Require compatible protocol versions.

---

## 18. Documentation

Add:

```text
docs/vscode-extension/
├── README.md
├── architecture.md
├── client-protocol.md
├── development.md
├── security.md
└── release.md
```

Update the main README with:

- extension status;
- install from VSIX;
- Marketplace link later;
- CLI dependency;
- privacy guarantee;
- screenshots;
- troubleshooting.

Add an ADR for:

```text
VS Code extension as a thin client over versioned Python CLI JSON
```

The ADR should lock:

- no direct store ownership in TypeScript;
- no telemetry;
- no remote backend;
- protocol versioning;
- shared webview UI as a future desktop-app seam.

---

## 19. Delivery Sequence

### Milestone 1 — Client protocol

Deliver:

- handshake;
- project;
- status;
- fact/node browsing and opaque source resolution;
- standard errors;
- stdin mutation transport and compatibility rules;
- Python tests.

Result: the CLI is ready for graphical clients.

### Milestone 2 — Extension shell

Deliver:

- extension scaffolding;
- Activity Bar;
- project overview;
- executable detection;
- doctor action;
- mocked integration tests.

Result: installable VSIX with useful project status.

### Milestone 3 — Memory actions and source inspector

Deliver:

- fact/node browser and inspector;
- safe open source;
- remember selection and manual remember;
- curate now;
- truthful activity derived from existing records;
- refresh.

Result: the v0.1 extension supports useful viewing and action workflows.

### Milestone 4 — Graph prototype and optional v0.2 graph

Deliver:

- prototype against real stores;
- validate relationship semantics and usefulness;
- React webview, filters, inspector, and refresh only if validated;
- exact curation preview only after the token/fingerprint contract exists.

Result: evidence supports either shipping the graph or explicitly declining it.

### Milestone 5 — Recommendation Studio

Deliver after the Phase 8 contracts and CLI workflows are stable:

- proposal queue;
- evidence review;
- editable skill preview;
- diff;
- accept/reject;
- metrics.

Result: the extension exposes Neurobase’s headline capability.

### Milestone 6 — Marketplace release

Deliver:

- polished onboarding;
- documentation;
- screenshots;
- VSIX;
- Marketplace listing;
- release workflow.

---

## 20. Recommended First Pull Requests

### PR 1 — Client protocol foundation

Files:

```text
src/neurobase/client/*
tests/test_client_protocol.py
tests/test_client_sources.py
tests/test_client_activity.py
docs/vscode-extension/client-protocol.md
docs/adr/xxxx-vscode-client-protocol.md
```

### PR 2 — Extension scaffold

Files:

```text
extension/package.json
extension/src/extension.ts
extension/src/cli/*
extension/src/views/ProjectOverviewProvider.ts
extension/test/*
.github/workflows/extension.yml
```

### PR 3 — Remember, curate, browser, and source inspector

Files:

```text
extension/src/commands/remember.ts
extension/src/commands/curate.ts
extension/src/commands/openSource.ts
extension/src/views/MemoryTreeProvider.ts
src/neurobase/client/commands.py
tests/*
```

### PR 4 — Activity and hardening

Files:

```text
extension/src/panels/ActivityPanel.ts
extension/webview/src/activity/*
docs/vscode-extension/security.md
scripts/ci_extension.py
```

### PR 5 — Graph prototype, then optional graph webview

Do not commit to the full graph until the prototype gate in Phase E passes.

### PR 6 — Recommendation Studio

Begin only after the recommender contracts are implemented.

---

## 21. Definition of Done for Extension v0.1

The release is done when:

- The extension installs from a `.vsix`.
- It runs on macOS, Windows, and Linux.
- It detects a valid Neurobase CLI.
- It identifies enabled projects correctly.
- It shows current-project memory health.
- It browses facts and synthesis nodes and opens Python-approved sources.
- It opens markdown sources in VS Code.
- It remembers selected or manually entered text.
- It runs curation and shows the structured result.
- It runs doctor and presents actionable failures.
- It handles missing, old, and incompatible CLI versions.
- It supports multi-root workspaces.
- It has no telemetry or remote dependencies.
- It never writes store files directly.
- Remembered content never appears in process arguments or routine logs.
- Activity claims are limited to events supported by authoritative local data.
- All Python and extension CI gates pass.
- The privacy and architecture guarantees are documented.

---

## 22. Final Recommendation

Treat the extension as an independent post-0.1 workstream; canonical Phase 9 is
already the public release, Phase 7 is complete, and Phase 8 is underway. Limit
extension v0.1 to:

1. Versioned Python client protocol
2. Activity Bar overview
3. Fact/node browser and Markdown source inspector
4. Remember Selection
5. Curate Now
6. Truthful recent activity
7. Doctor and setup guidance

Do not block v0.1 on the recommender. Reuse the already-complete MCP remember
behavior through a shared Python application service rather than duplicating it.

Prototype the graph after v0.1 and ship it only if real Neurobase stores produce a
useful visualization. Build the Skill Studio only after Phase 8's proposal, edit,
ledger, evidence, preview, and install contracts are stable.

This sequence produces a useful VS Code experience quickly while keeping the Python CLI, markdown store, local-first guarantees, and future desktop-app path intact.
