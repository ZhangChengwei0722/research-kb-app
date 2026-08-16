# Local Research Workspace Manager App Workflow

> **发布状态：** App `0.1.1b2` 已作为 Windows-only 公开 beta 发布。本文描述产品
> workflow；migration、cutover 和生产稳定承诺仍在范围之外。

- status: `public_windows_beta`
- application_service_interface: `1.23`
- current_app_target: `0.1.1b2`
- current_core_target: `0.1.1 published @ tag v0.1.1 -> faf9e6fa9ad9167d86804df996e8cbc69592b539`
- current_core_wheel_sha256: `ddc95cb332217cf4ca7ebd0e4833b79e4c363af8e7a57d3b449629bc025a65e5`
- predecessor_b1_status: `accepted_exact_predecessor_dev_tuple`
- sleep_resume_status: `accepted_beta_limitation`
- r1_release_governance_status: `core_and_app_publication_passed`
- windows_public_beta_status: `validation_evidence_complete_release_hygiene_open`
- app_public_beta_status: `published_validation_complete_release_hygiene_open`
- agent_execution: `external_manual_handoff`
- canonical_scientific_write_without_preview: false

## End-To-End Flow

```text
launch localhost product
-> first run: create or adopt a workspace through managed setup
-> later runs: choose a configured workspace
-> import a local PDF or select one watched-inbox item
-> Registry, Parse and use-specific Source Adequacy
-> choose Primary or Review route; mixed documents use Review
-> create external Codex CLI or Claude Code CLI handoff
-> import candidate JSON
-> escaped App preview
-> explicit user approve, revise or reject
-> Core canonical commit and Guardian
-> read, trace, organize or synthesize from committed records
```

Deterministic operations stay in the App/Core path. Semantic reading, classification when
ambiguous, Paper Card/Review Memory candidate generation, scientific comparison and
Research Synthesis / 科研综合与启发 proposal generation stays with the selected external Agent.

## Managed Setup And Egress

The browser chooses folders through a native helper but receives only a short-lived opaque
lease and a display label. Core prepares a deterministic workspace proposal; the App shows
a redacted preview; only explicit approval may materialize the workspace and append an
immutable managed-profile revision. ACL/path/source identities are revalidated across the
commit boundary. Existing-workspace adoption writes only a profile reference after the
same preview and revalidation pattern.

Agent handoff, Knowledge Query answer copy and task-package export are server-derived from
the current Task state. The browser cannot supply the copied/exported text. Restricted
clipboard copy requires both Windows clipboard history and cloud sync to be explicitly
disabled; otherwise it fails closed. Local task packages are create-only and operation-
receipted.

## Source Routes

Local upload and watched-inbox selection create or resume a Pipeline Job. File identity,
parse identity and Source Adequacy are checked per requested use. A source may support a
basic Card while remaining inadequate for figure, table, formula or supplementary-data
Evidence; only the consuming operation waits for reparse or a missing source.

For the closed `continuous_text_citation` uncertainty case, the App can open the exact
Task/Profile-bound PDF in UPDF or the system reader. After the user checks reading order,
Core appends an immutable accept-or-remediation Source Adequacy successor and refreshes the
Agent Task. This decision never approves a scientific candidate.

Primary candidates must close retained Card Units through canonical Evidence. Review and
mixed candidates must close each retained Review Unit to the review source and remain
`background_only`; they cannot enter canonical Evidence. Zero-Unit low-value Review Memory
is allowed when it records the reason and coverage limits.

## Knowledge Use

- Reading and ordinary Knowledge Query use committed current records and create no
  canonical scientific write.
- Evidence trace-back reopens the exact revision-bound source, parse and locator; a newer
  active parse never substitutes historical provenance.
- Direction, Field Map, Question and screening proposals are staged before dedicated
  approval. Tags are deterministic user-owned operations.
- `Research Synthesis / 科研综合与启发` is an explicit maintenance route with four candidate
  types. Internal `step7-*` names are compatibility identifiers only.

## Discovery, Views And Exchange

Europe PMC search is transient. Selection stores metadata only. OA resolution is zero
write, while explicit acquisition creates one absent PDF in `local_inbox` and stops before
Registry; intake requires a later explicit action.

Obsidian views are Core-rendered and synchronized one-way into a managed subtree. Edited
managed files block overwrite until the user discards the edit or exports a create-only
personal-note copy. Exchange uses four allowlisted scopes, dry-run size/rights preview and
safe staging. Imported records remain external and unreviewed by default.

## Operational Lifecycle

The launcher selects a free loopback port, opens the browser after readiness, records a
discoverable log path and stops through the product power action. Backup uses a writer
barrier; restore remains closed until consistency checks pass. Operational journal archive
and stale-maintenance work preserve receipts. SQLite/FTS can always be rebuilt.

The P0-P11 roadmap and final generated-artifact cleanup are closed. W2 Source Adequacy and
the 2026-08-14 B1/Bootstrap and fresh-Windows C15 predecessor acceptance are historical
predecessor results for their exact dev tuple; physical sleep/resume remains an accepted
beta limitation. Core `0.1.1` is published at tag `v0.1.1` targeting accepted commit
`faf9e6fa9ad9167d86804df996e8cbc69592b539` (accepted build-once wheel SHA
`ddc95cb332217cf4ca7ebd0e4833b79e4c363af8e7a57d3b449629bc025a65e5`), and App `0.1.1b2`
is published as a Windows-only public beta against interface `1.23`. Clean-install,
lifecycle, headless and headed Edge GUI evidence is recorded; strict fresh Windows
profile evidence remains valid through the b1→b2 impact rebind. Release hygiene (b1 yank
and user-document sync) remains before the pre-release marker is lifted. The public beta
does not authorize migration, cutover, topology changes or cleanup execution.
