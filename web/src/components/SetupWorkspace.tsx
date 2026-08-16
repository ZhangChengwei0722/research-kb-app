import { FormEvent, useEffect, useState, type ReactNode } from "react";
import {
  Check,
  Database,
  FolderOpen,
  HardDrive,
  Link,
  Plus,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  Trash2,
} from "lucide-react";
import {
  ApiError,
  commitWorkspaceAdoption,
  commitWorkspaceSetup,
  getSetupRecovery,
  prepareWorkspaceSetup,
  previewWorkspaceAdoption,
  runSetupRecoveryAction,
  selectSetupFolder,
  type SetupFolderPurpose,
  type SetupRecovery,
  type SetupSelection,
  type SetupStatus,
  type WorkspaceAdoptionPreview,
  type WorkspaceSetupPreview,
  type WorkspaceSetupResult,
} from "../api";

type SetupWorkspaceProps = {
  initialStatus: SetupStatus;
  onComplete: () => Promise<void>;
};

type SetupRoute = "create" | "adopt";

const RECOVERY_ACTION_LABELS = {
  resume_workspace_setup: "继续创建",
  discard_workspace_staging: "放弃未完成内容",
  restart_workspace_setup: "重新开始",
} as const;

export function SetupWorkspace({ initialStatus, onComplete }: SetupWorkspaceProps) {
  const [route, setRoute] = useState<SetupRoute>("create");
  const [workspaceLabel, setWorkspaceLabel] = useState("");
  const [workspaceName, setWorkspaceName] = useState("");
  const [workspaceParent, setWorkspaceParent] = useState<SetupSelection | null>(null);
  const [sourceRoots, setSourceRoots] = useState<SetupSelection[]>([]);
  const [localInbox, setLocalInbox] = useState<SetupSelection | null>(null);
  const [adoptionSelection, setAdoptionSelection] = useState<SetupSelection | null>(null);
  const [adoptionLabel, setAdoptionLabel] = useState("");
  const [createPreview, setCreatePreview] = useState<WorkspaceSetupPreview | null>(null);
  const [adoptionPreview, setAdoptionPreview] = useState<WorkspaceAdoptionPreview | null>(null);
  const [recovery, setRecovery] = useState<SetupRecovery | null>(null);
  const [restartRequired, setRestartRequired] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (initialStatus.mode !== "recovery") return;
    let active = true;
    getSetupRecovery()
      .then((result) => {
        if (active) setRecovery(result);
      })
      .catch((caught: unknown) => {
        if (active) setError(errorMessage(caught));
      });
    return () => {
      active = false;
    };
  }, [initialStatus.mode]);

  async function chooseFolder(
    purpose: SetupFolderPurpose,
    apply: (selection: SetupSelection) => void,
    allowNewChild = false,
  ) {
    setBusy(true);
    setError("");
    try {
      const result = await selectSetupFolder(purpose, {
        allowNewChild,
        initialLocationId: purpose === "workspace_parent" ? "documents" : undefined,
      });
      if (result.status === "success" && result.selection) apply(result.selection);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function handlePrepare(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!workspaceParent || !localInbox || sourceRoots.length === 0) return;
    setBusy(true);
    setError("");
    try {
      const preview = await prepareWorkspaceSetup({
        workspace_parent_lease_id: workspaceParent.lease_id,
        source_roots: sourceRoots.map((selection, index) => ({
          root_id: `source-${index + 1}`,
          selection_lease_id: selection.lease_id,
        })),
        local_inbox_lease_id: localInbox.lease_id,
        workspace_name: workspaceName,
        workspace_label: workspaceLabel,
        idempotency_key: `setup-${crypto.randomUUID()}`,
      });
      setCreatePreview(preview);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function handleCreateCommit() {
    if (!createPreview) return;
    setBusy(true);
    setError("");
    try {
      await finish(
        await commitWorkspaceSetup(createPreview.proposal_token, createPreview.preview_digest),
      );
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function handleAdoptionPreview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!adoptionSelection) return;
    setBusy(true);
    setError("");
    try {
      setAdoptionPreview(await previewWorkspaceAdoption(adoptionSelection.lease_id));
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function handleAdoptionCommit() {
    if (!adoptionPreview) return;
    setBusy(true);
    setError("");
    try {
      await finish(
        await commitWorkspaceAdoption(
          adoptionPreview.adoption_token,
          adoptionPreview.preview_digest,
          adoptionLabel,
        ),
      );
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function handleRecoveryAction(payload: {
    action: "select_profile_revision" | "resume_workspace_setup" | "discard_workspace_staging" | "restart_workspace_setup";
    revision_id?: string;
    operation_id?: string;
  }) {
    setBusy(true);
    setError("");
    try {
      await finish(await runSetupRecoveryAction(payload));
      if (!payload.action.startsWith("select_")) setRecovery(await getSetupRecovery());
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function finish(result: WorkspaceSetupResult) {
    if (result.restart_required) {
      setRestartRequired(true);
      return;
    }
    await onComplete();
  }

  if (restartRequired) {
    return (
      <SetupFrame status="RESTART">
        <section className="setup-complete" aria-live="polite">
          <RotateCcw size={28} aria-hidden="true" />
          <p className="section-kicker">WORKSPACE READY</p>
          <h2>重新打开应用</h2>
        </section>
      </SetupFrame>
    );
  }

  if (initialStatus.mode === "recovery") {
    return (
      <SetupFrame status="RECOVERY">
        <section className="setup-surface" aria-labelledby="recovery-title">
          <header className="setup-heading">
            <div><p className="section-kicker">RECOVERY</p><h2 id="recovery-title">恢复工作区设置</h2></div>
            <RefreshCw size={22} aria-hidden="true" />
          </header>
          {!recovery ? (
            <div className="setup-loading"><RefreshCw className="spin" size={20} />正在检查</div>
          ) : (
            <div className="recovery-list">
              {recovery.recoverable_revision_ids.map((revisionId, index) => (
                <div className="recovery-row" key={revisionId}>
                  <div><strong>配置版本 {index + 1}</strong><span>可恢复</span></div>
                  <button type="button" disabled={busy} onClick={() => handleRecoveryAction({ action: "select_profile_revision", revision_id: revisionId })}>
                    <RotateCcw size={16} />恢复
                  </button>
                </div>
              ))}
              {recovery.workspace_setup_operations.map((operation) => (
                <div className="recovery-row" key={operation.operation_id}>
                  <div><strong>{operation.workspace_label}</strong><span>{recoveryStateLabel(operation.state)}</span></div>
                  <div className="recovery-actions">
                    {operation.actions.map((action) => (
                      <button type="button" className={action === "discard_workspace_staging" ? "secondary-button" : ""} disabled={busy} key={action} onClick={() => handleRecoveryAction({ action, operation_id: operation.operation_id })}>
                        {action === "discard_workspace_staging" ? <Trash2 size={16} /> : <RotateCcw size={16} />}
                        {RECOVERY_ACTION_LABELS[action]}
                      </button>
                    ))}
                  </div>
                </div>
              ))}
              {recovery.recoverable_revision_ids.length === 0 && recovery.workspace_setup_operations.length === 0 ? (
                <div className="setup-empty">没有可恢复的设置</div>
              ) : null}
            </div>
          )}
          {error ? <div className="error-banner" role="alert">{error}</div> : null}
        </section>
      </SetupFrame>
    );
  }

  return (
    <SetupFrame status="SETUP">
      <section className="setup-surface" aria-labelledby="setup-title">
        <header className="setup-heading">
          <div><p className="section-kicker">FIRST RUN</p><h2 id="setup-title">工作区设置</h2></div>
          <ShieldCheck size={22} aria-hidden="true" />
        </header>

        <div className="setup-route-tabs" role="tablist" aria-label="设置方式">
          <button type="button" role="tab" aria-selected={route === "create"} className={route === "create" ? "active" : ""} onClick={() => { setRoute("create"); setError(""); }}>
            <Plus size={17} />新建工作区
          </button>
          <button type="button" role="tab" aria-selected={route === "adopt"} className={route === "adopt" ? "active" : ""} onClick={() => { setRoute("adopt"); setError(""); }}>
            <Link size={17} />连接已有工作区
          </button>
        </div>

        {route === "create" ? (
          createPreview ? (
            <div className="setup-review">
              <div className="setup-review-facts">
                <div><span>工作区</span><strong>{createPreview.preview.workspace_label}</strong></div>
                <div><span>文件夹</span><strong>{createPreview.preview.workspace_name}</strong></div>
                <div><span>文献来源</span><strong>{createPreview.preview.external_source_root_count}</strong></div>
                <div><span>导入目录</span><strong>已连接</strong></div>
              </div>
              <div className="setup-command-row">
                <button type="button" className="secondary-button" disabled={busy} onClick={() => setCreatePreview(null)}>返回修改</button>
                <button type="button" disabled={busy} onClick={handleCreateCommit}><Check size={17} />确认创建</button>
              </div>
            </div>
          ) : (
            <form className="setup-form" onSubmit={handlePrepare}>
              <div className="setup-name-grid">
                <label>工作区名称<input value={workspaceLabel} onChange={(event) => setWorkspaceLabel(event.target.value)} maxLength={80} required /></label>
                <label>文件夹名称<input value={workspaceName} onChange={(event) => setWorkspaceName(event.target.value)} maxLength={80} required /></label>
              </div>
              <SelectionField label="保存位置" icon={<HardDrive size={18} />} selection={workspaceParent} disabled={busy} onSelect={() => chooseFolder("workspace_parent", setWorkspaceParent, true)} />
              <div className="source-root-fieldset">
                <div className="setup-field-label"><Database size={18} /><span>文献来源</span><button type="button" className="icon-button" title="添加文献来源" aria-label="添加文献来源" disabled={busy || sourceRoots.length >= 8} onClick={() => chooseFolder("source_root", (selection) => setSourceRoots((current) => [...current, selection]))}><Plus size={17} /></button></div>
                <div className="selection-list">
                  {sourceRoots.map((selection, index) => (
                    <div className="selection-row" key={selection.lease_id}>
                      <span><strong>{selection.display_label}</strong><small>来源 {index + 1}</small></span>
                      <button type="button" className="icon-button" title="移除文献来源" aria-label={`移除文献来源 ${index + 1}`} onClick={() => setSourceRoots((current) => current.filter((item) => item.lease_id !== selection.lease_id))}><Trash2 size={16} /></button>
                    </div>
                  ))}
                  {sourceRoots.length === 0 ? <div className="selection-empty">尚未选择</div> : null}
                </div>
              </div>
              <SelectionField label="导入目录" icon={<FolderOpen size={18} />} selection={localInbox} disabled={busy} onSelect={() => chooseFolder("local_inbox", setLocalInbox)} />
              <button className="setup-primary" type="submit" disabled={busy || !workspaceParent || !localInbox || sourceRoots.length === 0}>检查并预览</button>
            </form>
          )
        ) : adoptionPreview ? (
          <div className="setup-review">
            <div className="setup-review-facts">
              <div><span>工作区</span><strong>{adoptionLabel}</strong></div>
              <div><span>内容处理</span><strong>保持原位</strong></div>
            </div>
            <div className="setup-command-row">
              <button type="button" className="secondary-button" disabled={busy} onClick={() => setAdoptionPreview(null)}>返回修改</button>
              <button type="button" disabled={busy} onClick={handleAdoptionCommit}><Check size={17} />确认连接</button>
            </div>
          </div>
        ) : (
          <form className="setup-form" onSubmit={handleAdoptionPreview}>
            <label>显示名称<input value={adoptionLabel} onChange={(event) => setAdoptionLabel(event.target.value)} maxLength={80} required /></label>
            <SelectionField label="已有工作区" icon={<FolderOpen size={18} />} selection={adoptionSelection} disabled={busy} onSelect={() => chooseFolder("existing_workspace_config", setAdoptionSelection)} />
            <button className="setup-primary" type="submit" disabled={busy || !adoptionSelection}>检查并预览</button>
          </form>
        )}
        {error ? <div className="error-banner" role="alert">{error}</div> : null}
      </section>
    </SetupFrame>
  );
}

function SetupFrame({ status, children }: { status: string; children: ReactNode }) {
  return (
    <main className="setup-shell">
      <header className="bootstrap-brand">
        <div className="brand-mark" aria-hidden="true"><Database size={21} /></div>
        <div><h1>Research KB</h1><p>Local Workspace Manager</p></div>
        <span className="phase-indicator">{status}</span>
      </header>
      {children}
    </main>
  );
}

function SelectionField({
  label,
  icon,
  selection,
  disabled,
  onSelect,
}: {
  label: string;
  icon: ReactNode;
  selection: SetupSelection | null;
  disabled: boolean;
  onSelect: () => void;
}) {
  return (
    <div className="selection-field">
      <div className="setup-field-label">{icon}<span>{label}</span></div>
      <div className="selection-control">
        <span className={selection ? "selection-value" : "selection-placeholder"}>
          {selection ? selection.display_label : "尚未选择"}
        </span>
        <button type="button" className="secondary-button" disabled={disabled} onClick={onSelect}><FolderOpen size={17} />选择</button>
      </div>
    </div>
  );
}

function recoveryStateLabel(state: string): string {
  return {
    absent: "等待重新开始",
    staged: "创建未完成",
    validated: "等待继续",
    complete: "等待完成配置",
    corrupt: "需要检查",
  }[state] ?? "需要处理";
}

function errorMessage(caught: unknown): string {
  if (caught instanceof ApiError) return caught.message;
  return "操作未完成";
}
