import { useEffect, useState } from "react";
import {
  ChevronLeft,
  ChevronRight,
  Copy,
  Eye,
  RefreshCw,
  Save,
  Trash2,
  TriangleAlert,
  Vault,
} from "lucide-react";
import {
  ApiError,
  applyObsidianRender,
  applyObsidianSync,
  getObsidianStatus,
  getObsidianTargets,
  previewObsidianRender,
  previewObsidianSync,
  type ObsidianOptionalTable,
  type ObsidianRenderPreview,
  type ObsidianStatus,
  type ObsidianSyncContinuation,
  type ObsidianSyncPreview,
  type ObsidianTarget,
} from "../api";


const PAGE_SIZE = 20;
const TABLES: ReadonlyArray<{ value: ObsidianOptionalTable; label: string }> = [
  { value: "library_summary", label: "文献库摘要表" },
  { value: "question_coverage", label: "问题覆盖表" },
];


export function ObsidianView() {
  const [status, setStatus] = useState<ObsidianStatus | null>(null);
  const [targets, setTargets] = useState<ObsidianTarget[]>([]);
  const [targetId, setTargetId] = useState("");
  const [optionalTables, setOptionalTables] = useState<ObsidianOptionalTable[]>([]);
  const [renderPreview, setRenderPreview] = useState<ObsidianRenderPreview | null>(null);
  const [syncPreview, setSyncPreview] = useState<ObsidianSyncPreview | null>(null);
  const [cursors, setCursors] = useState<Array<string | null>>([null]);
  const [pageIndex, setPageIndex] = useState(0);
  const [loading, setLoading] = useState(true);
  const [mutating, setMutating] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    let active = true;
    Promise.all([getObsidianStatus(PAGE_SIZE), getObsidianTargets()])
      .then(([source, configured]) => {
        if (!active) return;
        setStatus(source);
        setOptionalTables(source.optional_tables);
        setTargets(configured.targets);
        setTargetId(configured.targets[0]?.target_id ?? "");
      })
      .catch((caught: unknown) => {
        if (active) setError(errorMessage(caught));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, []);

  async function loadStatus(cursor: string | null = null) {
    const next = await getObsidianStatus(PAGE_SIZE, cursor);
    setStatus(next);
    return next;
  }

  async function refresh() {
    setLoading(true);
    setError("");
    setNotice("");
    setRenderPreview(null);
    setSyncPreview(null);
    try {
      const next = await loadStatus();
      setOptionalTables(next.optional_tables);
      setCursors([null]);
      setPageIndex(0);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setLoading(false);
    }
  }

  function toggleTable(table: ObsidianOptionalTable) {
    setOptionalTables((current) => (
      current.includes(table)
        ? current.filter((item) => item !== table)
        : TABLES.map((item) => item.value).filter((item) => item === table || current.includes(item))
    ));
    setRenderPreview(null);
    setSyncPreview(null);
    setNotice("");
  }

  async function previewRender() {
    setMutating(true);
    setError("");
    setNotice("");
    setSyncPreview(null);
    try {
      setRenderPreview(await previewObsidianRender(optionalTables));
    } catch (caught) {
      setRenderPreview(null);
      setError(errorMessage(caught));
    } finally {
      setMutating(false);
    }
  }

  async function applyRender() {
    if (!renderPreview) return;
    setMutating(true);
    setError("");
    setNotice("");
    try {
      const result = await applyObsidianRender({
        preview_token: renderPreview.preview_token,
        optional_tables: optionalTables,
        continuation: renderPreview.integrity_state === "edited_managed_file"
          ? "discard_managed_edits"
          : "render",
      });
      setNotice(result.result === "no_change" ? "视图没有变化" : "视图已生成");
      setRenderPreview(null);
      setSyncPreview(null);
      const next = await loadStatus();
      setOptionalTables(next.optional_tables);
      setCursors([null]);
      setPageIndex(0);
    } catch (caught) {
      setRenderPreview(null);
      setError(errorMessage(caught));
    } finally {
      setMutating(false);
    }
  }

  function selectTarget(nextTargetId: string) {
    setTargetId(nextTargetId);
    setSyncPreview(null);
    setNotice("");
  }

  async function previewSync() {
    if (!targetId) return;
    setMutating(true);
    setError("");
    setNotice("");
    try {
      setSyncPreview(await previewObsidianSync(targetId));
    } catch (caught) {
      setSyncPreview(null);
      setError(errorMessage(caught));
    } finally {
      setMutating(false);
    }
  }

  async function applySync(continuation: ObsidianSyncContinuation) {
    if (!syncPreview || !targetId) return;
    setMutating(true);
    setError("");
    setNotice("");
    try {
      const result = await applyObsidianSync({
        target_id: targetId,
        preview_token: syncPreview.preview_token,
        continuation,
      });
      setNotice(
        result.personal_copy
          ? `个人副本已导出，${result.personal_copy.file_count} files`
          : result.result === "no_change" ? "目标已经同步" : "Obsidian 同步完成",
      );
      setSyncPreview(null);
    } catch (caught) {
      setSyncPreview(null);
      setError(errorMessage(caught));
    } finally {
      setMutating(false);
    }
  }

  async function nextPage() {
    if (!status?.next_cursor) return;
    const nextIndex = pageIndex + 1;
    const cursor = status.next_cursor;
    setLoading(true);
    setError("");
    try {
      await loadStatus(cursor);
      setCursors((current) => [...current.slice(0, nextIndex), cursor]);
      setPageIndex(nextIndex);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setLoading(false);
    }
  }

  async function previousPage() {
    if (pageIndex === 0) return;
    const nextIndex = pageIndex - 1;
    setLoading(true);
    setError("");
    try {
      await loadStatus(cursors[nextIndex] ?? null);
      setPageIndex(nextIndex);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setLoading(false);
    }
  }

  const sourceCurrent = status?.projection_state === "ready"
    && status.integrity_state === "intact"
    && status.stale_count === 0;
  const hasTargetConflicts = Boolean(
    syncPreview && (
      syncPreview.edited_count > 0
      || syncPreview.missing_count > 0
      || syncPreview.unknown_count > 0
    )
  );

  return (
    <section className="obsidian-view" aria-labelledby="obsidian-title">
      <header className="view-heading">
        <div><p className="section-kicker">GENERATED READING VIEWS</p><h2 id="obsidian-title">Obsidian 视图</h2></div>
        <div className="view-heading-actions">
          <span className={`operation-chip operation-${mutating ? "running" : "idle"}`}>{mutating ? "processing" : "ready"}</span>
          <button className="icon-button" type="button" onClick={() => void refresh()} disabled={loading || mutating} title="刷新" aria-label="刷新"><RefreshCw size={17} /></button>
        </div>
      </header>
      {error ? <div className="error-banner" role="alert">{error}</div> : null}
      {notice ? <div className="notice-banner" role="status">{notice}</div> : null}

      <div className="obsidian-workbench">
        <section className="obsidian-source-pane" aria-labelledby="obsidian-source-title">
          <div className="subsection-heading">
            <div><h3 id="obsidian-source-title">生成视图</h3><span>{status?.generation_id ?? "not rendered"}</span></div>
            <Vault size={18} aria-hidden="true" />
          </div>
          <div className="obsidian-metrics">
            <Metric label="状态" value={status?.projection_state ?? (loading ? "loading" : "unavailable")} />
            <Metric label="完整性" value={status?.integrity_state ?? "unknown"} />
            <Metric label="当前" value={String(status?.current_count ?? 0)} />
            <Metric label="过期" value={String(status?.stale_count ?? 0)} />
          </div>
          <fieldset className="obsidian-table-options" disabled={mutating}>
            <legend>可选表</legend>
            {TABLES.map((table) => (
              <label key={table.value}>
                <input type="checkbox" checked={optionalTables.includes(table.value)} onChange={() => toggleTable(table.value)} />
                <span>{table.label}</span>
              </label>
            ))}
          </fieldset>
          <button className="secondary-button" type="button" onClick={() => void previewRender()} disabled={mutating}>
            <Eye size={16} />预览生成
          </button>
          {renderPreview ? (
            <div className="obsidian-preview" aria-label="生成预览">
              <div className="obsidian-preview-counts">
                <span>文件 {renderPreview.proposed_file_count}</span>
                <span>待更新 {renderPreview.changed_file_count}</span>
                <span>待移除 {renderPreview.removed_file_count}</span>
              </div>
              <LogicalPaths paths={[...renderPreview.changed_paths, ...renderPreview.removed_paths]} />
              <button className="start-button" type="button" onClick={() => void applyRender()} disabled={mutating}>
                {renderPreview.integrity_state === "edited_managed_file" ? <Trash2 size={16} /> : <Save size={16} />}
                {renderPreview.integrity_state === "edited_managed_file" ? "放弃生成文件修改" : "生成视图"}
              </button>
            </div>
          ) : null}
        </section>

        <section className="obsidian-inventory-pane" aria-labelledby="obsidian-inventory-title">
          <div className="subsection-heading"><div><h3 id="obsidian-inventory-title">视图清单</h3><span>第 {pageIndex + 1} 页 · {status?.file_count ?? 0} files</span></div></div>
          {loading && !status ? <div className="list-state" role="status">正在读取视图</div> : null}
          <div className="obsidian-file-list" role="list">
            {status?.entries.map((entry) => (
              <div className="obsidian-file-row" role="listitem" key={entry.logical_path}>
                <span><strong>{entry.logical_path}</strong><small>{entry.view_kind} · {entry.view_id}</small></span>
                <span className={`status-badge freshness-${entry.freshness}`}>{entry.freshness}</span>
              </div>
            ))}
            {!loading && !status?.entries.length ? <div className="compact-empty">尚未生成视图</div> : null}
          </div>
          <div className="pagination-bar">
            <button className="secondary-button" type="button" onClick={() => void previousPage()} disabled={loading || mutating || pageIndex === 0}><ChevronLeft size={17} />上一页</button>
            <button className="secondary-button" type="button" onClick={() => void nextPage()} disabled={loading || mutating || !status?.next_cursor}>下一页<ChevronRight size={17} /></button>
          </div>
        </section>

        <section className="obsidian-sync-pane" aria-labelledby="obsidian-sync-title">
          <div className="subsection-heading"><div><h3 id="obsidian-sync-title">Vault 同步</h3><span>one-way managed subtree</span></div></div>
          <label htmlFor="obsidian-target">同步目标</label>
          <select id="obsidian-target" value={targetId} onChange={(event) => selectTarget(event.target.value)} disabled={mutating || !targets.length}>
            {!targets.length ? <option value="">没有配置目标</option> : null}
            {targets.map((target) => <option key={target.target_id} value={target.target_id}>{target.label}</option>)}
          </select>
          <button className="secondary-button" type="button" onClick={() => void previewSync()} disabled={mutating || !targetId || !sourceCurrent}>
            <Eye size={16} />预览同步
          </button>
          {!sourceCurrent && status ? <div className="obsidian-boundary"><TriangleAlert size={15} />生成视图不是 current + intact</div> : null}
          {syncPreview ? (
            <div className="obsidian-preview" aria-label="同步预览">
              <div className="obsidian-preview-counts">
                <span>新建 {syncPreview.create_count}</span>
                <span>更新 {syncPreview.update_count}</span>
                <span>移除 {syncPreview.remove_count}</span>
                <span>冲突 {syncPreview.edited_count + syncPreview.missing_count + syncPreview.unknown_count + syncPreview.collision_count}</span>
              </div>
              <LogicalPaths paths={syncPreview.conflict_paths.length ? syncPreview.conflict_paths : syncPreview.changed_paths} />
              {syncPreview.destination_state === "collision" ? (
                <div className="obsidian-boundary"><TriangleAlert size={15} />目标包含未受管内容，不能接管</div>
              ) : hasTargetConflicts ? (
                <div className="obsidian-continuations">
                  <button className="secondary-button danger-text" type="button" onClick={() => void applySync("discard_managed_edits")} disabled={mutating}><Trash2 size={16} />放弃受管修改并同步</button>
                  <button className="start-button" type="button" onClick={() => void applySync("export_personal_copy_then_sync")} disabled={mutating}><Copy size={16} />导出个人副本后同步</button>
                </div>
              ) : (
                <button className="start-button" type="button" onClick={() => void applySync("sync")} disabled={mutating}><Save size={16} />同步到 Obsidian</button>
              )}
            </div>
          ) : null}
        </section>
      </div>
    </section>
  );
}


function Metric({ label, value }: { label: string; value: string }) {
  return <div><span>{label}</span><strong>{value}</strong></div>;
}


function LogicalPaths({ paths }: { paths: string[] }) {
  if (!paths.length) return <div className="compact-empty">没有文件变化</div>;
  return <ul className="obsidian-path-list">{paths.map((path) => <li key={path}>{path}</li>)}</ul>;
}


function errorMessage(caught: unknown): string {
  if (caught instanceof ApiError) return `${caught.code}: ${caught.message}`;
  if (caught instanceof Error) return caught.message;
  return "Obsidian 操作未完成";
}
