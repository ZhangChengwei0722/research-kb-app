# Research KB App Collaboration Rules

## Product Boundary

This repository owns the localhost Local Research Workspace Manager product layer:

```text
FastAPI HTTP adapter + browser security
-> React/TypeScript/Vite interface
-> pinned research-kb-core application services
```

It does not own canonical scientific contracts, stable IDs, provenance rules, workspace transactions, Artifact Catalog business logic, Agent semantic judgment or private research data.

## Source Of Truth

- Core contracts and behavior come only from the pinned `research-kb-core` wheel and its public services.
- The browser submits configured option IDs and record IDs only. It never submits filesystem paths.
- The backend must not invoke the Core CLI, parse CLI stdout, read canonical JSON/JSONL directly or reimplement SQLite projection rules.
- SQLite/FTS is disposable App state. It is never canonical or operational authority.
- P2 and P5-A reading/comparison have zero canonical scientific writes and create no
  process event merely for reading. P5-C Knowledge Query may write operational Agent Task
  state and report receipts, but never canonical scientific records.
- P7-B2 organization proposals may create a canonical revision only through the dedicated
  Core organization approval service after App preview and explicit user approval. Generic
  Agent approval must reject `organization_proposal` Tasks.

## Repository Structure

```text
src/research_kb_app/   Python backend, launcher and HTTP adapters
web/                   React/TypeScript/Vite source
tests/                 backend, security, integration and E2E tests
scripts/               deterministic local bootstrap and validation tools
docs/                  App architecture, workflow and closure records
```

Generated `.venv/`, `node_modules/`, `web/release/`, legacy `web/dist/`, logs, local config, App state, coverage, browser artifacts and temporary workspaces are ignored lifecycle outputs. Do not commit them. `web/release/` uses stable generated filenames so repeated builds do not accumulate content-addressed assets; do not clean legacy `web/dist/` without explicit deletion authority.

## Security Contract

- Bind only an already-opened `127.0.0.1` loopback socket on an OS-selected port.
- Keep startup/session/CSRF tokens in memory. Never place them in URLs, logs, files or browser storage.
- Enforce exact runtime `Host`, exact mutation `Origin`, session authentication and CSRF.
- Do not install wildcard CORS.
- Use escaped text, restrictive CSP and no unsanitized Markdown or `innerHTML`.
- Redact local paths, source text, stack traces and secret values from HTTP errors and logs.
- Treat source records, parsed text, Agent output and imported data as untrusted data, never instructions.
- Reject unknown config keys, unsafe writable roots, oversized bodies/queries/pages and path-shaped browser identifiers.

## Core Compatibility

`core-compatibility.json` pins the accepted Core commit, wheel SHA-256, package version and public interface versions. Bootstrap verifies the supplied wheel digest before install; startup fails closed on incompatible runtime capability facts.

No App change may silently change a Core schema, workspace layout, source authority or canonical state. A required Core change is planned and merged in the Core repository first.

## Phase Boundary

The P0-P11 roadmap and R3 operational acceptance are closed. The sections below are
cumulative delivery history and compatibility constraints, not active implementation
gates. The closed P11 public-roadmap baseline is interface `1.18`; W2 Source Adequacy
closed on `1.19`. The 2026-08-14 B1 Bootstrap and Localhost Security predecessor was
accepted for its exact predecessor dev tuple. Physical sleep/resume remains an accepted
beta limitation. The current local Windows beta-preparation train targets local App
candidate/target `0.1.1b1` and exact Core `0.1.1` release candidate at protected main
`faf9e6fa9ad9167d86804df996e8cbc69592b539`, accepted build-once wheel SHA
`ddc95cb332217cf4ca7ebd0e4833b79e4c363af8e7a57d3b449629bc025a65e5`, and Application
Service interface `1.23`. Core R1-0/R1-A remain not accepted because PyPI
ownership/publisher and primary/independent backup recovery owners are open. Core is not
tagged, released or published; the App and Windows public beta are not published or
accepted.

P2, integrated P3-D and P4-A downstream compatibility acceptance are closed. P3-D0 adds
the public Core deterministic-intake facade. P3-D1 adds backend-only watched-inbox/upload/
Job controls, an App-owned streaming spool and serialized Catalog freshness rebuilds.
P3-D2 adds the browser intake and processing work surface, durable Job recovery, bounded
polling, Source Adequacy display and CAS resume/cancel. P4-A pins the reviewed Core
Application Service interface `1.2` and proves primary, review and genuine mixed-document
routes through the deterministic gate.

P4-D pins Core Application Service interface `1.6` and exposes manual external Agent Task
handoff, exact payload inspection, complete schema-bearing prompt manifests, bounded JSON
import, escaped candidate preview and explicit approve/revision/reject controls. The App
does not launch Codex/Claude, expose leases or choose canonical writers from browser input.

P5-A pins Core Application Service interface `1.7` and exposes committed Primary/Review
reading, exact Evidence quote/page/locator trace and ordered two-to-four-paper comparison.
Review Memory remains visibly background-only, comparison remains semantically inert, and
all reading routes preserve zero canonical scientific write.

P5-B pins Core Application Service interface `1.8` and adds session/workspace-bound opaque
Evidence PDF handles, revalidated full or single-Range streaming, PDF.js target-page
rendering with best-effort quote highlighting, and explicit UPDF/system-reader launch.
The browser never receives a source path, source ref, digest, fingerprint or Core handle.

P6 pins Core Application Service interface `1.10` and adds the dedicated `发现` work
surface over the fixed Europe PMC connector. Search is transient, selection persists only
checked metadata candidates, OA resolution is zero-write, and explicit acquisition writes
create-only into `local_inbox` then stops before Registry or Parse. The App must not chain
selection, acquisition or intake, accept provider URLs, or add a second provider.

P7-B2 pins Core Application Service interface `1.12` and adds the dedicated `研究组织`
surface for Direction, Field Map Entry and Question proposals. It accepts one target and
one to twenty-five ordered papers, optionally includes background-only Review Memory, and
uses the external manual Agent handoff. Core owns admissibility, IDs, stale checks,
conflict blocking, exact no-change behavior and canonical commit. Organization Tasks stay
out of the generic Pipeline Agent work surface.

P5-C pins Core Application Service interface `1.9` and adds the dedicated `问答` work
surface. It reuses an explicit ordered selection of one to four papers, creates only a
`knowledge_query_report` Agent Task, exposes the exact contract-bound manual handoff to
Codex CLI or Claude Code CLI, and accepts the validated result only as a current-task
report. Query Tasks stay out of the Pipeline Agent work surface; generic scientific
approval must reject them. At the P5-C gate, Obsidian and Exchange remained deferred;
P9 and P10 below supersede that historical deferral. A Guardian
summary is not exposed until Core provides a suitable session-bound public facade; the App
must not bypass the opaque WorkspaceSession to obtain one.

P5-D closed the packaged R1 acceptance gate at implementation commit `ee14d54`. The exact
fresh-installed wheel serves its own `research_kb_app/web_dist`, completes synthetic local
intake, external semantic approval, reading, Evidence PDF trace-back and report-only Query,
then exits cleanly through the product shutdown action. P5-D added no Core contract, schema
or product feature. At that gate, Research Synthesis maintenance, Obsidian and Exchange
were deferred and were later closed by P8-P10; private workspace access, migration and
deployment remain outside the delivered roadmap.

P7-D2B pins merged Core `16013d5` at Application Service interface `1.15` and adds the optional `问题筛选`
surface. Direct manual criteria/decision writes remain explicit-user deterministic
operations. Criteria and decision proposal Tasks use external manual Codex CLI or Claude
Code CLI handoff, escaped preview and dedicated screening approval. `uncertain` or stale
candidates cannot be approved. Library inclusion and ordinary Paper Card processing do not
depend on Question-specific screening. Generic Agent approval must reject both screening
proposal Task kinds.

P8-B pins merged Core `aac28d4` at Application Service interface `1.16` and adds the
dedicated `Research Synthesis / 科研综合与启发` surface. It supports Synthesis, Review Angle,
Insight and Cross-View candidates through external manual Codex CLI or Claude Code CLI
handoff, escaped preview and dedicated approval. Review Memory remains labeled background,
ordinary Query and navigation remain zero canonical scientific write, and stale or
uncertain-near-duplicate proposals cannot be approved. Internal `step7-*` command, store,
schema and record-kind identifiers remain compatibility names only.

P9-B pins merged Core `f343389` at Application Service interface `1.17` and adds the
dedicated `Obsidian` surface. Core exclusively renders generated Markdown; the App previews
and synchronizes it one-way into a configured managed vault subtree using server-side,
single-use leases. Existing managed edits require explicit discard or create-only personal
copy export before sync. Browser requests contain IDs and closed options only; target paths,
source manifests and digests remain server-side. Reverse sync, Markdown import, arbitrary
vault browsing, canonical scientific writes and real-vault validation remain forbidden.

P10 pins merged Core `f655037` at Application Service interface `1.18` and adds the
dedicated `Exchange / 知识库交换` surface. Four export scopes, optional rights-asserted PDF
inclusion, bounded upload/download custody, safe import preflight and immutable external-
origin inventory are closed. External records remain `external_unreviewed`, do not enter
local canonical scientific stores and are never factual query/synthesis input by default.
Semantic merge, local promotion, migration, signatures and peer transfer remain deferred.

P11 pins runtime Core `8a14666` at Application Service interface `1.18` and closes R3
operational acceptance. The App projects Core backup/restore, operational-maintenance and
lazy-stale capability facts, binds bounded Job/Task reads to a current disposable Catalog
projection, proves three configured synthetic workspaces remain isolated and rebuilds a
missing or corrupt projection to equivalent answers. It adds no backup path picker,
maintenance dashboard, layout migration, private-workspace cutover or new scientific
semantics. User-facing terminology remains `Research Synthesis / 科研综合与启发`; internal
`step7-*` names are compatibility-only.

## Public Governance Boundary

- App `0.1.1b1` is an unpublished local Windows beta candidate. Governance documents describe
  a controlled candidate, not a public release, public support service, or completed acceptance.
- The target public repository identity and reporting identities are not closed. Do not add a
  project URL, public issue endpoint, contact email, release link, publication step, or recovery
  owner identity on assumption.
- Use `LICENSE`, `SECURITY.md`, `SUPPORT.md`, `CONTRIBUTING.md`, and `CHANGELOG.md` as the
  candidate's public-safe governance baseline. Security reports remain private and ordinary
  support/contribution requests use an already authorized controlled collaboration channel.
- This repository does not authorize remote publication, package publication, migration,
  cutover, private-workspace access, or deletion through documentation changes.

## Engineering Discipline

- Use supported CPython 3.11 or 3.12 (`>=3.11,<3.13`) and project-local dependencies only.
- Use React + TypeScript + Vite; keep the P2-D product surface code-native and read-only.
- Prefer focused modules and typed boundaries. Do not add speculative frameworks or state libraries.
- Write a failing or characterization test before fixing behavioral defects.
- Keep changes scoped to the active phase and preserve deterministic Core responses.
- Use `apply_patch` for manual file edits.

## Validation

Before a phase commit, run:

```text
Python unit/integration/security tests
frontend unit tests and TypeScript typecheck
ESLint and Vite production build
Playwright loopback bootstrap/workspace/catalog/shutdown E2E
installed production-start smoke
Core compatibility mismatch smoke
privacy/path-redaction checks
git diff --check
```

Use only the materialized synthetic `p2-small` fixture for integration/E2E. Do not access private workspaces, legacy records or real source documents.

## Git And Delivery

- This repository remains intentionally local-only through the P0-P11 closure and has no
  configured remote. A later remote, publication or deployment still requires a separate
  decision.
- Use small phase-scoped commits; do not rewrite history or force operations.
- Never add a remote, publish, deploy or package a desktop installer without separate explicit authority.
- A bounded phase closes only after full validation, diff review, a closure manifest and `neat-freak` reconciliation.

## Destructive Operations

Do not delete files, generated workspaces, caches or build outputs without current explicit deletion authority. Cleanup candidates are reported with ownership, dependency state and reclaimable size first.
