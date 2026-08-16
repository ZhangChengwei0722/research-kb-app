import { FormEvent, useEffect, useRef, useState } from "react";
import { BookOpenText, Check, ChevronLeft, ChevronRight, Plus, Search } from "lucide-react";
import {
  ApiError,
  getCatalogItem,
  listCatalogItems,
  type CatalogDetail,
  type CatalogItem,
  type CatalogSearchResult,
} from "../api";
import { kindLabel, type CatalogView } from "../catalogViews";
import { DetailInspector } from "./DetailInspector";
import { TagFacetSelect } from "./TagFacetSelect";

const PAGE_SIZE = 8;

type CatalogBrowserProps = {
  view: CatalogView;
  projectionState: string;
  refreshKey: number;
  readingPaperIds?: readonly string[];
  onOpenPaper?: (paperId: string) => void;
  onToggleComparison?: (paperId: string) => void;
};

export function CatalogBrowser({
  view,
  projectionState,
  refreshKey,
  readingPaperIds = [],
  onOpenPaper,
  onToggleComparison,
}: CatalogBrowserProps) {
  const [queryDraft, setQueryDraft] = useState("");
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("all");
  const [tagId, setTagId] = useState("");
  const [cursors, setCursors] = useState<Array<string | null>>([null]);
  const [pageIndex, setPageIndex] = useState(0);
  const [result, setResult] = useState<CatalogSearchResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [listError, setListError] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<CatalogDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const detailRequest = useRef(0);

  const queryable = projectionState === "current" || projectionState === "stale";
  const cursor = cursors[pageIndex] ?? null;

  useEffect(() => {
    setQueryDraft("");
    setQuery("");
    setKind("all");
    setTagId("");
    setCursors([null]);
    setPageIndex(0);
    setSelectedId(null);
    setDetail(null);
    setDetailError("");
  }, [view.id]);

  useEffect(() => {
    setCursors([null]);
    setPageIndex(0);
    clearDetail();
  }, [refreshKey]);

  useEffect(() => {
    if (!queryable) {
      setResult(null);
      return;
    }
    let active = true;
    setLoading(true);
    setListError("");
    listCatalogItems({
      query,
      itemKinds: kind === "all" ? [...view.kinds] : [kind],
      pageSize: PAGE_SIZE,
      cursor,
      tagId: view.id === "library" ? tagId || undefined : undefined,
    }).then((response) => {
      if (active) setResult(response);
    }).catch((caught: unknown) => {
      if (active) setListError(errorMessage(caught));
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, [cursor, kind, projectionState, query, queryable, refreshKey, tagId, view]);

  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setQuery(queryDraft.trim());
    resetPageAndDetail();
  }

  function changeKind(nextKind: string) {
    if (nextKind !== "all" && !view.kinds.includes(nextKind)) return;
    setKind(nextKind);
    resetPageAndDetail();
  }

  function changeTag(nextTagId: string) {
    setTagId(nextTagId);
    resetPageAndDetail();
  }

  function nextPage() {
    if (!result?.next_cursor) return;
    const next = result.next_cursor;
    setCursors((current) => [...current.slice(0, pageIndex + 1), next]);
    setPageIndex((current) => current + 1);
    clearDetail();
  }

  function previousPage() {
    if (pageIndex === 0) return;
    setPageIndex((current) => current - 1);
    clearDetail();
  }

  async function selectItem(item: CatalogItem) {
    const requestId = detailRequest.current + 1;
    detailRequest.current = requestId;
    setSelectedId(item.item_id);
    setDetail(null);
    setDetailLoading(true);
    setDetailError("");
    try {
      const response = await getCatalogItem(item.item_id);
      if (detailRequest.current === requestId) setDetail(response);
    } catch (caught) {
      if (detailRequest.current === requestId) setDetailError(errorMessage(caught));
    } finally {
      if (detailRequest.current === requestId) setDetailLoading(false);
    }
  }

  function resetPageAndDetail() {
    setCursors([null]);
    setPageIndex(0);
    clearDetail();
  }

  function clearDetail() {
    detailRequest.current += 1;
    setSelectedId(null);
    setDetail(null);
    setDetailLoading(false);
    setDetailError("");
  }

  return (
    <section className="catalog-view" aria-labelledby={`${view.id}-title`}>
      <header className="view-heading">
        <div>
          <p className="section-kicker">{view.eyebrow}</p>
          <h2 id={`${view.id}-title`}>{view.title}</h2>
        </div>
        <span className={`projection-chip projection-${projectionState}`}>projection:{projectionState}</span>
      </header>

      <div className="catalog-toolbar">
        <form className="search-form" onSubmit={submitSearch} role="search">
          <label className="sr-only" htmlFor={`${view.id}-search`}>搜索{view.label}</label>
          <Search size={17} aria-hidden="true" />
          <input
            id={`${view.id}-search`}
            type="search"
            value={queryDraft}
            onChange={(event) => setQueryDraft(event.target.value)}
            placeholder="搜索标题、摘要、ID 或状态"
            disabled={!queryable}
          />
          <button type="submit" disabled={!queryable || loading}>搜索</button>
        </form>
        <div className="kind-filter">
          <label htmlFor={`${view.id}-kind`}>类型</label>
          <select id={`${view.id}-kind`} value={kind} onChange={(event) => changeKind(event.target.value)} disabled={!queryable}>
            <option value="all">全部</option>
            {view.kinds.map((itemKind) => (
              <option key={itemKind} value={itemKind}>{kindLabel(view, itemKind)}</option>
            ))}
          </select>
        </div>
        {view.id === "library" ? (
          <TagFacetSelect id="library-tag-filter" value={tagId} onChange={changeTag} disabled={!queryable || loading} />
        ) : null}
      </div>

      <div className="catalog-workbench">
        <div className="catalog-list-pane">
          <div className="list-meta" aria-live="polite">
            <span>第 {pageIndex + 1} 页</span>
            <span>{result?.items.length ?? 0} records</span>
          </div>

          {!queryable && <ListState label="Projection 尚不可查询" />}
          {queryable && loading && <ListState label="正在读取 Catalog" />}
          {queryable && !loading && listError && <div className="inline-error" role="alert">{listError}</div>}
          {queryable && !loading && !listError && result?.items.length === 0 && <ListState label={view.emptyLabel} />}
          {queryable && !loading && !listError && result && result.items.length > 0 && (
            <div className="catalog-list" role="list">
              {result.items.map((item) => (
                <div className="catalog-list-item catalog-row-shell" role="listitem" key={item.item_id}>
                  <button
                    type="button"
                    className={`catalog-row catalog-row-main ${selectedId === item.item_id ? "catalog-row-selected" : ""}`}
                    onClick={() => void selectItem(item)}
                    aria-pressed={selectedId === item.item_id}
                  >
                    <span className="row-type">{kindLabel(view, item.item_kind)}</span>
                    <strong>{item.title}</strong>
                    <span className="row-summary">{item.summary || "-"}</span>
                    {item.tags.length ? (
                      <span className="catalog-row-tags" aria-label="记录标签">
                        {item.tags.map((tag) => <span key={tag.tag_id}>{tag.name}</span>)}
                      </span>
                    ) : null}
                    <span className="row-footer">
                      <span className={`authority-badge authority-${item.authority_layer}`}>{item.authority_layer}</span>
                      <span className="mono row-id">{item.record_id}</span>
                    </span>
                  </button>
                  {view.id === "library" && item.item_kind === "paper" && item.paper_id && onOpenPaper && onToggleComparison && (
                    <div className="catalog-row-actions" aria-label={`${item.title} 阅读操作`}>
                      <button
                        className="icon-button compact-icon"
                        type="button"
                        onClick={() => onOpenPaper(item.paper_id as string)}
                        title="打开阅读"
                        aria-label={`打开阅读 ${item.title}`}
                      >
                        <BookOpenText size={16} />
                      </button>
                      <button
                        className={`icon-button compact-icon${readingPaperIds.includes(item.paper_id) ? " compare-selected" : ""}`}
                        type="button"
                        onClick={() => onToggleComparison(item.paper_id as string)}
                        disabled={!readingPaperIds.includes(item.paper_id) && readingPaperIds.length >= 4}
                        title={readingPaperIds.includes(item.paper_id) ? "从比较中移除" : "加入比较"}
                        aria-label={`${readingPaperIds.includes(item.paper_id) ? "从比较中移除" : "加入比较"} ${item.title}`}
                        aria-pressed={readingPaperIds.includes(item.paper_id)}
                      >
                        {readingPaperIds.includes(item.paper_id) ? <Check size={16} /> : <Plus size={16} />}
                      </button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          <div className="pagination-bar">
            <button className="secondary-button" type="button" onClick={previousPage} disabled={loading || pageIndex === 0}>
              <ChevronLeft size={17} aria-hidden="true" />
              上一页
            </button>
            <button className="secondary-button" type="button" onClick={nextPage} disabled={loading || !result?.has_more || !result.next_cursor}>
              下一页
              <ChevronRight size={17} aria-hidden="true" />
            </button>
          </div>
        </div>

        <DetailInspector
          view={view}
          detail={detail}
          loading={detailLoading}
          error={detailError}
          onClose={clearDetail}
        />
      </div>
    </section>
  );
}

function ListState({ label }: { label: string }) {
  return <div className="list-state" role="status">{label}</div>;
}

function errorMessage(caught: unknown): string {
  if (caught instanceof ApiError) return `${caught.code}: ${caught.message}`;
  return "请求未完成";
}
