import { ChevronLeft, ChevronRight } from "lucide-react";
import { useEffect, useState } from "react";
import { ApiError, listTags, type Tag } from "../api";

type Props = {
  id: string;
  value: string;
  onChange: (tagId: string) => void;
  label?: string;
  disabled?: boolean;
};

const PAGE_SIZE = 50;

export function TagFacetSelect({ id, value, onChange, label = "标签", disabled = false }: Props) {
  const [tags, setTags] = useState<Tag[]>([]);
  const [cursors, setCursors] = useState<Array<string | null>>([null]);
  const [pageIndex, setPageIndex] = useState(0);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const cursor = cursors[pageIndex] ?? null;

  useEffect(() => {
    let current = true;
    setLoading(true);
    setError("");
    listTags(false, PAGE_SIZE, cursor)
      .then((result) => {
        if (!current) return;
        setTags(result.tags);
        setNextCursor(result.next_cursor);
      })
      .catch((caught: unknown) => current && setError(errorMessage(caught)))
      .finally(() => current && setLoading(false));
    return () => { current = false; };
  }, [cursor]);

  function nextPage() {
    if (!nextCursor) return;
    setCursors((current) => [...current.slice(0, pageIndex + 1), nextCursor]);
    setPageIndex((current) => current + 1);
    onChange("");
  }

  function previousPage() {
    if (pageIndex === 0) return;
    setPageIndex((current) => current - 1);
    onChange("");
  }

  return (
    <div className="tag-facet">
      <label htmlFor={id}>{label}</label>
      <div className="tag-facet-controls">
        <select
          id={id}
          value={tags.some((tag) => tag.tag_id === value) ? value : ""}
          onChange={(event) => onChange(event.target.value)}
          disabled={disabled || loading}
        >
          <option value="">全部标签</option>
          {tags.map((tag) => <option key={tag.tag_id} value={tag.tag_id}>{tag.name}</option>)}
        </select>
        <button className="icon-button compact-icon" type="button" onClick={previousPage} disabled={disabled || loading || pageIndex === 0} aria-label="上一页标签" title="上一页标签">
          <ChevronLeft size={15} />
        </button>
        <span className="tag-facet-page" aria-label={`标签第 ${pageIndex + 1} 页`}>{pageIndex + 1}</span>
        <button className="icon-button compact-icon" type="button" onClick={nextPage} disabled={disabled || loading || !nextCursor} aria-label="下一页标签" title="下一页标签">
          <ChevronRight size={15} />
        </button>
      </div>
      {error ? <span className="facet-error" role="alert">{error}</span> : null}
    </div>
  );
}

function errorMessage(caught: unknown): string {
  if (caught instanceof ApiError) return `${caught.code}: ${caught.message}`;
  return "标签列表不可用";
}
