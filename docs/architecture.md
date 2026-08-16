# Local Research Workspace Manager App Architecture

> **发布状态：** App `0.1.1b2` 已作为 Windows-only 公开 beta 发布到 GitHub Releases
> 与 PyPI。`0.1.1b1` 已被取代。公开 beta 不等于最终验收：全新 Windows 账户安装与
> headed GUI 观察完成前，不宣称 `Windows public beta accepted`。

- status: `public_windows_beta`
- current_app_target: `0.1.1b2`
- current_core_target: `0.1.1 published @ tag v0.1.1 -> faf9e6fa9ad9167d86804df996e8cbc69592b539`
- current_core_wheel_sha256: `ddc95cb332217cf4ca7ebd0e4833b79e4c363af8e7a57d3b449629bc025a65e5`
- application_service_interface: `1.23`
- predecessor_b1_status: `accepted_exact_predecessor_dev_tuple`
- sleep_resume_status: `accepted_beta_limitation`
- r1_release_governance_status: `core_and_app_publication_passed`
- windows_public_beta_status: `evidence_recorded_acceptance_pending`
- app_public_beta_status: `published_acceptance_pending`
- current_layout_decision: `retain_current_layout`
- private_workspace_cutover: false

## Product Boundary

```text
Browser UI
-> loopback-only FastAPI adapter
-> pinned public research-kb-core services
-> workspace selected by an immutable managed profile or explicit server-side configuration
```

The App owns interaction, session security, bounded upload custody, disposable Catalog
projection, PDF rendering, local process lifecycle and explicit user approval. Core owns
workspace identity, canonical and operational records, schemas, IDs, provenance,
transactions, recovery, Guardian and all deterministic writes. The browser sends option
IDs and record IDs, never server paths or Core authority objects.

Codex CLI and Claude Code CLI remain external executors. Core creates one schema-bound
handoff; the App previews the bounded payload, lets the user transfer it to the selected
executor, imports one candidate JSON object and requires explicit preview plus approval.
The App does not launch an Agent, store model credentials or allow Agent output to write
canonical records directly.

## Storage Layers

```text
canonical scientific records   Core authority, append-only revisions
operational records             Core authority, Jobs/Tasks/events/receipts
SQLite/FTS Catalog              App-owned disposable projection
generated Obsidian Markdown     Core-rendered, App-synchronized one-way view
Exchange staging               bounded App custody, Core validation and settlement
```

Deleting or corrupting SQLite cannot remove scientific or operational truth. Projection
rebuild, workspace switching and P11 multi-workspace acceptance prove that boundary.
Generated Markdown is never canonical input. External Exchange records remain immutable
`external_unreviewed` inventory until a separately designed local-promotion workflow.

## Security Boundary

- bind only an OS-selected `127.0.0.1` socket;
- keep startup/session/CSRF tokens in memory and out of URLs, logs and browser storage;
- enforce exact Host, Origin, session and CSRF checks;
- render source and Agent content as escaped untrusted data under restrictive CSP;
- keep source paths, refs, fingerprints, leases and writable targets server-side;
- fail closed on Core interface, wheel digest, workspace or projection incompatibility.
- require local NTFS, reparse-free ancestry and protected Windows ACLs for App-managed
  writable roots;
- derive clipboard and task-package payloads on the server from the current Task state;
- serialize one App instance per managed profile and retain append-only lifecycle receipts.

## Current Delivery

Application Service interface `1.23` is the current contract target for the local Windows
beta-preparation train. It supports deterministic intake, semantic handoff, reading and
Evidence PDF trace-back, report-only Knowledge Query, Europe PMC discovery and explicit OA
acquisition, research organization, Tags, optional Question Screening, Research Synthesis / 科研综合与启发,
Obsidian generated views, Exchange, P11 operational acceptance and the bounded Source
Adequacy reading-order resolution workflow. It additionally exposes read-only workspace
adoption, transaction-integrity diagnostics, C17 deterministic workspace materialization
and C18 trusted supervised Parse. The App adds no-config managed profiles, native folder
selection, Windows ACL/mutex enforcement, setup/recovery UI and server-owned egress custody.

The 2026-08-14 B1/Bootstrap and fresh-Windows C15 predecessor was accepted for the exact
predecessor dev tuple; physical sleep/resume remains an accepted beta limitation. Core
`0.1.1` is published: tag `v0.1.1` targets accepted commit
`faf9e6fa9ad9167d86804df996e8cbc69592b539`, and the accepted build-once wheel SHA is
`ddc95cb332217cf4ca7ebd0e4833b79e4c363af8e7a57d3b449629bc025a65e5`. App `0.1.1b2` is
published on GitHub Releases and PyPI as a Windows-only public beta and supersedes
`0.1.1b1`. Clean-install, lifecycle and headless GUI evidence is recorded; strict
brand-new Windows account installation and headed GUI observation remain before the
`Windows public beta accepted` claim. Private-workspace cutover, legacy migration,
layout-v2, embedded Agent execution, second discovery provider, arbitrary vault browsing,
reverse Obsidian sync, semantic Exchange merge and desktop packaging require separate
designs outside the current public beta.
