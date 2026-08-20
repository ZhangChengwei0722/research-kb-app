# R1 Local Operator Guide

> **发布状态：** 本指南适用于已发布的 App `0.1.1b2` Windows-only 公开 beta。它只描述
> 本地操作；beta 支持边界见 [`docs/support-matrix.md`](support-matrix.md)。

## Start

Install `research-kb-app==0.1.1b2` from PyPI into a 64-bit CPython 3.11 or 3.12
environment; its pinned `research-kb-core[pdf]==0.1.1` dependency is resolved
automatically. Ordinary users start the product without a configuration argument:

```powershell
research-kb-app
```

The packaged command chooses an available `127.0.0.1` port, prints the URL, a one-time
startup token and the log path, then opens the default browser after the server becomes
ready. It does not require Node, Vite, a frontend development server or a manually selected
port. Automation may add `--no-browser`; multiple isolated local profiles may use
`--profile <profile-id>`.

The startup token is console-only. It is not written to the URL, log, browser storage or
App configuration. Enter it once in the bootstrap screen. A first run opens the managed
setup surface; a configured profile opens the workspace selector.

## First Run

The managed setup surface supports two explicit routes:

1. create a generic Research KB workspace under a user-selected local NTFS parent; or
2. adopt one existing Shared Core workspace after deterministic validation.

For creation, select an existing source root and `local_inbox`, enter the workspace label
and folder name, review the redacted preview, then approve. The App does not copy the source
root or inbox. Folder paths, ACLs and security descriptors remain server-side; the browser
receives only purpose-bound, session-bound opaque leases and display labels. The App asks
for a restart after a new managed profile revision is committed.

Managed writable roots require local NTFS, reparse-free ancestry and the protected Windows
ACL policy. exFAT, UNC/network paths, junctions, symlinks, unsafe ACLs and unknown storage
identity fail closed. Existing roots are inspected and are never silently repaired.

## App Configuration

The App configuration contract and exact path rules are defined in
`docs/configuration.md`. The default managed profile owns:

- one or more validated Shared Core workspace options after setup;
- App-owned state, log, runtime and receipt roots below the Windows known Local AppData
  location;
- the packaged `research_kb_app/web_dist` frontend when running an installed release;
- bounded request budgets.

Managed workspace creation is a deterministic Core Application Service operation. The App
collects closed user choices, shows the preview and requests explicit approval; it does not
invent scientific content or bypass Core validation. `--config <absolute-path>` remains an
advanced compatibility route for existing automation and does not expose managed setup.

An optional `config@1.1` Obsidian target must point to an existing vault and two separate
relative subtrees. The App writes only the configured managed subtree after an explicit
render preview and sync preview. If a managed file was edited, use the create-only personal
copy action before synchronization unless those edits are intentionally being discarded.
Personal notes outside the managed subtree are never scanned or replaced.

## Normal Workflow

```text
upload PDF or select a stable watched-inbox file
-> deterministic Registry, Parse and Source Adequacy
-> inspect the external Agent payload and copy its prompt
-> import schema-bound JSON
-> preview and approve, revise or reject
-> read the committed Paper Card or Review Memory
-> trace Primary Card Units to exact Evidence and PDF page
-> run report-only Knowledge Query when needed
-> explicitly render and one-way sync generated Obsidian views when needed
```

Review Memory remains background-only. A Knowledge Query report is operational task output
and does not update canonical scientific records, Question Mapping or Research Synthesis.

Restricted Agent payloads can use one-click clipboard copy only when Windows clipboard
history and cloud sync are both deterministically disabled. Enabled, conflicting, missing
or unreadable policy state refuses the copy. Use the create-only local task package route
when clipboard custody is not acceptable.

## Stop And Diagnose

Use the power button labeled `停止服务`. A normal stop waits for the localhost process to
exit cleanly. The console-reported log path is the first diagnostic location; it contains
runtime events but not the one-time token or browser-visible source authority.

If startup fails, keep the error text and check the absolute App config, Core compatibility
pin, profile security, workspace binding and configured state/log/frontend roots. Do not
work around a Core compatibility, NTFS, ACL, reparse, profile-instance or clipboard-policy
refusal by editing the packaged compatibility file or local receipts.
