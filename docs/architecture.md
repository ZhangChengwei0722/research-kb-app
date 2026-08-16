# Local Research Workspace Manager App Architecture

> **发布状态：** App `0.1.1b1` 是未发布的本地 Windows beta 候选版。本文不构成 public
> release、公开支持或已完成验收的声明；目标 public repository identity 尚未关闭。

- status: `local_windows_beta_preparation`
- current_app_target: `0.1.1b1`
- current_core_target: `0.1.1 release candidate @ protected main faf9e6fa9ad9167d86804df996e8cbc69592b539`
- current_core_wheel_sha256: `ddc95cb332217cf4ca7ebd0e4833b79e4c363af8e7a57d3b449629bc025a65e5`
- application_service_interface: `1.23`
- predecessor_b1_status: `accepted_exact_predecessor_dev_tuple`
- sleep_resume_status: `accepted_beta_limitation`
- r1_release_governance_status: `r1-0_r1-a_not_accepted_external_blockers_open`
- windows_public_beta_status: `not_accepted`
- app_public_beta_status: `not_published_not_accepted`
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
predecessor dev tuple; physical sleep/resume remains an accepted beta limitation. Current
local preparation targets App `0.1.1b1` and exact Core `0.1.1` release candidate at
protected main `faf9e6fa9ad9167d86804df996e8cbc69592b539` with accepted build-once wheel SHA
`ddc95cb332217cf4ca7ebd0e4833b79e4c363af8e7a57d3b449629bc025a65e5`. Core R1-0/R1-A
remain not accepted because PyPI ownership/publisher and primary/independent backup
recovery owners are open. Core is not tagged, released or published; the App and Windows
public beta are not published or accepted. Private-workspace cutover, legacy migration,
layout-v2, embedded Agent execution, second discovery provider, arbitrary vault browsing,
reverse Obsidian sync, semantic Exchange merge and desktop packaging require separate
designs. See `docs/p11-layout-v2-decision.md` and
`docs/p11-operational-acceptance-closure-manifest.md`.
