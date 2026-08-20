# App Configuration

> **发布状态：** 本文适用于已发布的 App `0.1.1b2` Windows-only 公开 beta
> （[GitHub Releases](https://github.com/ZhangChengwei0722/research-kb-app/releases/tag/v0.1.1b2) /
> [PyPI](https://pypi.org/project/research-kb-app/0.1.1b2/)）。安装步骤见
> [`docs/installation.md`](installation.md)；beta 不授权 migration、cutover 或真实生产
> workspace 切换。

## Managed Product Profile

The default Windows product route does not require a hand-written configuration file:

```powershell
research-kb-app
```

The first-run UI creates an immutable managed profile revision under the Windows known
Local AppData location. The profile stores validated workspace config references, bounded
request budgets and optional Obsidian target references. It does not store startup/session
tokens, source text, Agent payloads or browser-visible local paths. `current.json` is an
atomic pointer to an immutable revision; recovery selects an existing valid revision rather
than editing one in place.

Use `--profile <profile-id>` for another isolated local profile. Profile IDs use lower-case
ASCII letters, digits, `_` or `-`, start with a letter and are at most 64 characters.

## Explicit Config Compatibility Route

`--config <absolute-path>` remains available for existing automation and controlled test
environments. It bypasses the managed first-run UI but retains runtime compatibility,
storage, Host/Origin/session/CSRF and workspace validation. The JSON contract follows.

The launcher accepts one untracked JSON configuration. The contract is strict: unknown
keys fail closed, all filesystem paths are absolute, and browser requests use only the
configured option IDs.

```json
{
  "contract_version": "research-kb-app-config@1.1",
  "workspaces": [
    {
      "option_id": "p2-small",
      "label": "P2 Small Synthetic",
      "config_path": "<absolute workspace.yaml path>"
    }
  ],
  "state_root": "<absolute operation-owned App state root>",
  "log_root": "<absolute child of state_root>",
  "frontend_root": "<absolute web/release path>",
  "request_budgets": {
    "max_body_bytes": 16384,
    "max_query_bytes": 2048,
    "max_page_size": 100,
    "request_timeout_seconds": 30
  },
  "obsidian_targets": [
    {
      "target_id": "primary-vault",
      "label": "Primary Vault",
      "workspace_option_id": "p2-small",
      "vault_root": "<absolute existing Obsidian vault path>",
      "managed_subtree": "Research KB/Generated",
      "personal_notes_subtree": "Research KB/Personal"
    }
  ]
}
```

The older `research-kb-app-config@1.0` contract remains readable and defines zero Obsidian
targets. In `@1.1`, target IDs and workspace bindings are explicit. Both subtree values are
confined relative POSIX paths, must not overlap each other, App state, the frontend or the
configured workspace root, and may not traverse a symlink, junction or reparse point.
`managed_subtree` is replaceable App-owned output; `personal_notes_subtree` receives only
explicit create-only exports of edited managed files. Neither subtree is a reverse-sync or
Markdown-import source.

The Core session service validates each workspace option. The Core projection service
also verifies that `state_root` does not overlap the workspace, knowledge, inbox, or
source roots. `log_root` must remain inside the validated App state root. Neither paths
nor source text are returned by the HTTP API.

Use the conventional layout in which `workspace.yaml`, its canonical knowledge root and
source roots live beneath the same workspace directory. P9-B rejects Obsidian target
subtrees that overlap that configured workspace directory. A nonstandard workspace whose
canonical roots are outside the config directory requires a separate path-separation review
before a target is enabled.

## External Agent policy

The selected Core workspace owns its Agent privacy policy. P7-D2B uses the additive
`p7d-v1` registry and an explicit allowlist that covers semantic processing, Knowledge
Query, organization and Question-screening proposal context permitted by the workspace
owner:

```yaml
agent_policy:
  registry_version: p7d-v1
  allowed_content_classes:
    - metadata
    - operational_context
    - parsed_excerpt
    - review_background
    - canonical_evidence
    - paper_card_content
    - research_routing_context
  execution_scope: cloud_allowed
  max_prompt_bytes: 1048576
  max_result_bytes: 1048576
```

The browser cannot broaden this policy. A Query Task uses the intersection of its registered
content classes, this workspace allowlist, the selected external executor and the user's
per-task choices. Review background, Paper Card content and research-routing context remain
bounded by the selected Task kind; the App cannot launch an Agent or broaden the policy.
