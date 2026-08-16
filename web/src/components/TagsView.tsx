import {
  Archive,
  ChevronLeft,
  ChevronRight,
  Link2,
  Plus,
  Save,
  Tag as TagIcon,
  Unlink,
} from "lucide-react";
import { useEffect, useState } from "react";
import {
  ApiError,
  getCatalogStatus,
  getHealth,
  getTag,
  listTags,
  promoteTag,
  setTagAssignment,
  type Tag,
  type TagAssignment,
  type TagDetail,
  type TagTargetKind,
  type CatalogStatus,
  type HealthResult,
} from "../api";

const PAGE_SIZE = 40;
const SETTLE_ATTEMPTS = 80;
const SETTLE_INTERVAL_MS = 150;
const TARGET_KINDS: ReadonlyArray<{ value: TagTargetKind; label: string }> = [
  { value: "paper", label: "Paper" },
  { value: "direction", label: "Direction" },
  { value: "field_map_entry", label: "Field Map Entry" },
  { value: "question", label: "Question" },
];

type Props = {
  onCatalogStatus?: (status: CatalogStatus) => void;
  onHealth?: (health: HealthResult) => void;
};

export function TagsView({ onCatalogStatus, onHealth }: Props = {}) {
  const [includeArchived, setIncludeArchived] = useState(false);
  const [tags, setTags] = useState<Tag[]>([]);
  const [cursors, setCursors] = useState<Array<string | null>>([null]);
  const [pageIndex, setPageIndex] = useState(0);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState<TagDetail | null>(null);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [aliases, setAliases] = useState("");
  const [targetKind, setTargetKind] = useState<TagTargetKind>("paper");
  const [targetId, setTargetId] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);
  const [loading, setLoading] = useState(true);
  const [mutating, setMutating] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const cursor = cursors[pageIndex] ?? null;

  useEffect(() => {
    let current = true;
    setLoading(true);
    setError("");
    listTags(includeArchived, PAGE_SIZE, cursor)
      .then((result) => {
        if (!current) return;
        setTags(result.tags);
        setNextCursor(result.next_cursor);
      })
      .catch((caught: unknown) => current && setError(errorMessage(caught)))
      .finally(() => current && setLoading(false));
    return () => { current = false; };
  }, [cursor, includeArchived, refreshKey]);

  useEffect(() => {
    if (!selectedId || creating) {
      setDetail(null);
      return;
    }
    let current = true;
    setLoading(true);
    setError("");
    getTag(selectedId)
      .then((result) => {
        if (!current) return;
        setDetail(result);
        setName(result.tag.name);
        setDescription(result.tag.description);
        setAliases(result.tag.aliases.join("\n"));
      })
      .catch((caught: unknown) => current && setError(errorMessage(caught)))
      .finally(() => current && setLoading(false));
    return () => { current = false; };
  }, [creating, selectedId, refreshKey]);

  function startCreate() {
    setCreating(true);
    setSelectedId("");
    setDetail(null);
    setName("");
    setDescription("");
    setAliases("");
    setTargetId("");
    setError("");
    setNotice("");
  }

  function selectTag(tagId: string) {
    setCreating(false);
    setSelectedId(tagId);
    setTargetId("");
    setError("");
    setNotice("");
  }

  function resetList() {
    setCursors([null]);
    setPageIndex(0);
    setRefreshKey((current) => current + 1);
  }

  async function settleCatalog() {
    if (!onCatalogStatus || !onHealth) return;
    for (let attempt = 0; attempt < SETTLE_ATTEMPTS; attempt += 1) {
      const [health, catalog] = await Promise.all([getHealth(), getCatalogStatus()]);
      onHealth(health);
      onCatalogStatus(catalog);
      const active = ["running", "building"].includes(health.operation.state);
      if (!active && catalog.projection_state !== "stale") return;
      await delay(SETTLE_INTERVAL_MS);
    }
    setNotice("标签已保存，后台索引仍在更新");
  }

  async function saveTag() {
    if (!name.trim()) return;
    setMutating(true);
    setError("");
    setNotice("");
    try {
      const result = await promoteTag({
        ...(detail ? { tag_id: detail.tag.tag_id, expected_revision_id: detail.tag.revision_id } : {}),
        name: name.trim(),
        description: description.trim(),
        aliases: parseAliases(aliases),
        ...(detail ? {} : { status: "active" as const }),
      });
      setCreating(false);
      setSelectedId(result.tag.tag_id);
      setDetail((current) => ({
        status: "success",
        tag: result.tag,
        assignments: current?.tag.tag_id === result.tag.tag_id ? current.assignments : [],
        persistent_writes: result.persistent_writes,
        canonical_scientific_write: result.canonical_scientific_write,
      }));
      setNotice(result.result === "no_change" ? "没有变化" : detail ? "标签已更新" : "标签已创建");
      resetList();
      if (result.result === "committed") await settleCatalog();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setMutating(false);
    }
  }

  async function archiveTag() {
    if (!detail || detail.tag.status === "archived") return;
    setMutating(true);
    setError("");
    setNotice("");
    try {
      const result = await promoteTag({
        tag_id: detail.tag.tag_id,
        status: "archived",
        expected_revision_id: detail.tag.revision_id,
      });
      setNotice(result.result === "no_change" ? "没有变化" : "标签已归档");
      setDetail({ ...detail, tag: result.tag });
      resetList();
      if (result.result === "committed") await settleCatalog();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setMutating(false);
    }
  }

  async function changeAssignment(state: "assigned" | "removed") {
    if (!detail || !targetId.trim()) return;
    const normalizedTargetId = targetId.trim();
    const current = detail.assignments.find((item) => item.target_kind === targetKind && item.target_id === normalizedTargetId);
    setMutating(true);
    setError("");
    setNotice("");
    try {
      const result = await setTagAssignment({
        tag_id: detail.tag.tag_id,
        target_kind: targetKind,
        target_id: normalizedTargetId,
        state,
        ...(current ? { expected_revision_id: current.revision_id } : {}),
      });
      setNotice(result.result === "no_change" ? "没有变化" : state === "assigned" ? "关联已建立" : "关联已移除");
      setRefreshKey((value) => value + 1);
      if (result.result === "committed") await settleCatalog();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setMutating(false);
    }
  }

  function nextPage() {
    if (!nextCursor) return;
    setCursors((current) => [...current.slice(0, pageIndex + 1), nextCursor]);
    setPageIndex((current) => current + 1);
    setSelectedId("");
    setDetail(null);
  }

  function previousPage() {
    if (pageIndex === 0) return;
    setPageIndex((current) => current - 1);
    setSelectedId("");
    setDetail(null);
  }

  return (
    <section className="tags-view" aria-labelledby="tags-title">
      <header className="view-heading">
        <div><p className="section-kicker">DETERMINISTIC ORGANIZATION</p><h2 id="tags-title">标签</h2></div>
        <span className={`operation-chip operation-${mutating ? "running" : "idle"}`}>{mutating ? "processing" : "user-owned"}</span>
      </header>
      {error ? <div className="error-banner" role="alert">{error}</div> : null}
      {notice ? <div className="notice-banner" role="status">{notice}</div> : null}

      <div className="tags-workbench">
        <section className="tags-list-pane" aria-labelledby="tag-vocabulary-title">
          <div className="subsection-heading">
            <div><h3 id="tag-vocabulary-title">标签词表</h3><span>第 {pageIndex + 1} 页 · {tags.length} records</span></div>
            <button className="secondary-button compact-button" type="button" onClick={startCreate} disabled={mutating}><Plus size={16} />新建标签</button>
          </div>
          <label className="tag-archive-toggle">
            <input type="checkbox" checked={includeArchived} onChange={(event) => { setIncludeArchived(event.target.checked); setCursors([null]); setPageIndex(0); }} disabled={mutating} />
            <span>包含已归档</span>
          </label>
          {loading && !tags.length ? <div className="list-state" role="status">正在读取标签</div> : null}
          {!loading && !tags.length ? <div className="list-state" role="status">当前没有标签</div> : null}
          <div className="tag-list" role="list">
            {tags.map((tag) => (
              <div key={tag.tag_id} role="listitem">
                <button type="button" className={selectedId === tag.tag_id ? "tag-row tag-row-selected" : "tag-row"} onClick={() => selectTag(tag.tag_id)} disabled={mutating}>
                  <span><TagIcon size={15} /><strong>{tag.name}</strong></span>
                  <small>{tag.description || tag.normalized_name}</small>
                  <span className="tag-row-meta"><span className={`status-badge tag-status-${tag.status}`}>{tag.status}</span>{typeof tag.assignment_count === "number" ? <span>{tag.assignment_count} links</span> : null}</span>
                </button>
              </div>
            ))}
          </div>
          <div className="pagination-bar">
            <button className="secondary-button" type="button" onClick={previousPage} disabled={loading || pageIndex === 0}><ChevronLeft size={17} />上一页</button>
            <button className="secondary-button" type="button" onClick={nextPage} disabled={loading || !nextCursor}>下一页<ChevronRight size={17} /></button>
          </div>
        </section>

        <section className="tag-editor-pane" aria-labelledby="tag-editor-title">
          <div className="subsection-heading"><div><h3 id="tag-editor-title">{creating ? "创建标签" : detail ? "编辑标签" : "标签详情"}</h3><span>{detail?.tag.revision_id ?? "select or create"}</span></div></div>
          {creating || detail ? (
            <>
              <label htmlFor="tag-name">标签名称</label>
              <input id="tag-name" value={name} onChange={(event) => setName(event.target.value)} maxLength={80} disabled={mutating} />
              <label htmlFor="tag-description">描述</label>
              <textarea id="tag-description" value={description} onChange={(event) => setDescription(event.target.value)} maxLength={500} disabled={mutating} />
              <label htmlFor="tag-aliases">别名（每行一个）</label>
              <textarea id="tag-aliases" value={aliases} onChange={(event) => setAliases(event.target.value)} maxLength={2000} disabled={mutating} />
              <div className="tag-command-row">
                <button className="start-button" type="button" onClick={saveTag} disabled={mutating || !name.trim()}><Save size={16} />{creating ? "创建标签" : "保存修改"}</button>
                {detail ? <button className="secondary-button danger-text" type="button" onClick={archiveTag} disabled={mutating || detail.tag.status === "archived"}><Archive size={16} />归档标签</button> : null}
              </div>
            </>
          ) : <div className="compact-empty">选择标签查看 revision 与关联</div>}
        </section>

        <section className="tag-assignment-pane" aria-labelledby="tag-assignment-title">
          <div className="subsection-heading"><div><h3 id="tag-assignment-title">目标关联</h3><span>Paper / Direction / Field Map / Question</span></div></div>
          {detail ? (
            <>
              <label htmlFor="tag-target-kind">目标类型</label>
              <select id="tag-target-kind" value={targetKind} onChange={(event) => setTargetKind(event.target.value as TagTargetKind)} disabled={mutating}>
                {TARGET_KINDS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
              </select>
              <label htmlFor="tag-target-id">目标 ID</label>
              <input id="tag-target-id" value={targetId} onChange={(event) => setTargetId(event.target.value)} spellCheck={false} disabled={mutating} />
              <div className="tag-command-row">
                <button type="button" onClick={() => void changeAssignment("assigned")} disabled={mutating || !targetId.trim()}><Link2 size={16} />建立关联</button>
                <button className="secondary-button" type="button" onClick={() => void changeAssignment("removed")} disabled={mutating || !targetId.trim()}><Unlink size={16} />移除关联</button>
              </div>
              <div className="tag-assignment-list" role="list">
                {detail.assignments.map((item) => <AssignmentRow key={item.tag_link_id} assignment={item} onSelect={() => { setTargetKind(item.target_kind); setTargetId(item.target_id); }} />)}
                {!detail.assignments.length ? <div className="compact-empty">当前没有关联</div> : null}
              </div>
            </>
          ) : <div className="compact-empty">选择一个标签后管理关联</div>}
        </section>
      </div>
    </section>
  );
}

function AssignmentRow({ assignment, onSelect }: { assignment: TagAssignment; onSelect: () => void }) {
  return (
    <button type="button" className="tag-assignment-row" onClick={onSelect}>
      <span><strong>{assignment.target_kind}</strong><span className="mono">{assignment.target_id}</span></span>
      <span><span className={`status-badge tag-link-${assignment.state}`}>{assignment.state}</span>{assignment.target_availability ? <span className="status-badge">{assignment.target_availability}</span> : null}</span>
    </button>
  );
}

function parseAliases(value: string): string[] {
  return [...new Set(value.split(/[\n,]+/).map((item) => item.trim()).filter(Boolean))];
}

function errorMessage(caught: unknown): string {
  if (caught instanceof ApiError) return `${caught.code}: ${caught.message}`;
  if (caught instanceof Error) {
    const shaped = caught as Error & { code?: unknown };
    if (typeof shaped.code === "string") return `${shaped.code}: ${caught.message}`;
  }
  return "标签操作未完成";
}

function delay(milliseconds: number) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}
