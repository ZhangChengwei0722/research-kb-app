import { FileSearch, X } from "lucide-react";
import type { CatalogDetail, JsonValue } from "../api";
import { kindLabel, type CatalogView } from "../catalogViews";

type DetailInspectorProps = {
  view: CatalogView;
  detail: CatalogDetail | null;
  loading: boolean;
  error: string;
  onClose: () => void;
};

export function DetailInspector({ view, detail, loading, error, onClose }: DetailInspectorProps) {
  const idle = !detail && !loading && !error;
  return (
    <aside className={`detail-inspector${idle ? " detail-idle" : ""}`} aria-label="记录详情">
      <div className="inspector-header">
        <div>
          <p className="section-kicker">ARTIFACT DETAIL</p>
          <h3>{detail?.item.title ?? "记录详情"}</h3>
        </div>
        {detail && (
          <button className="icon-button" type="button" onClick={onClose} title="关闭详情" aria-label="关闭详情">
            <X size={18} />
          </button>
        )}
      </div>

      {loading && <InspectorState label="正在读取当前记录" />}
      {!loading && error && <div className="inline-error" role="alert">{error}</div>}
      {!loading && !error && !detail && <InspectorState label="选择一条记录查看详情" />}

      {!loading && !error && detail && (
        <div className="inspector-scroll">
          <div className="authority-line">
            <StatusBadge value={detail.item.authority_layer} />
            <StatusBadge value={`projection:${detail.projection_state}`} />
            <StatusBadge value={`record:${detail.current_record_status}`} />
          </div>

          {(detail.item.status_labels.includes("not_fact") || detail.detail?.background_only === true || detail.detail?.not_fact === true) && (
            <div className="boundary-banner">
              {detail.detail?.background_only === true ? "Background only" : "Not a factual record"}
            </div>
          )}

          <section className="detail-section" aria-labelledby="record-identity-title">
            <h4 id="record-identity-title">记录标识</h4>
            <DefinitionRow label="Type" value={kindLabel(view, detail.item.item_kind)} />
            <DefinitionRow label="Record ID" value={detail.item.record_id} mono />
            {detail.item.child_id && <DefinitionRow label="Unit ID" value={detail.item.child_id} mono />}
            {detail.item.paper_id && <DefinitionRow label="Paper ID" value={detail.item.paper_id} mono />}
            {detail.item.question_id && <DefinitionRow label="Question ID" value={detail.item.question_id} mono />}
            <DefinitionRow label="Adapter" value={detail.item.adapter_version} />
          </section>

          <section className="detail-section" aria-labelledby="record-status-title">
            <h4 id="record-status-title">状态</h4>
            <div className="tag-list">
              {detail.item.status_labels.map((status) => <StatusBadge key={status} value={status} />)}
            </div>
          </section>

          {detail.current_record_status === "current" && detail.detail ? (
            <section className="detail-section" aria-labelledby="record-content-title">
              <h4 id="record-content-title">当前内容</h4>
              <div className="structured-detail">
                {Object.entries(detail.detail).map(([key, value]) => (
                  <StructuredField key={key} fieldKey={key} value={value} depth={0} />
                ))}
              </div>
            </section>
          ) : (
            <div className="freshness-warning" role="status">
              当前记录无法按投影内容展示；请以 Core 返回的 freshness 状态为准。
            </div>
          )}
        </div>
      )}
    </aside>
  );
}

function InspectorState({ label }: { label: string }) {
  return (
    <div className="inspector-empty" role="status">
      <FileSearch size={24} aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}

function StructuredField({ fieldKey, value, depth }: { fieldKey: string; value: JsonValue; depth: number }) {
  const label = humanize(fieldKey);
  if (value === null) return <DefinitionRow label={label} value="-" />;
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return <DefinitionRow label={label} value={String(value)} mono={isIdentifier(fieldKey)} />;
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return <DefinitionRow label={label} value="None" />;
    if (value.every((item) => typeof item !== "object" || item === null)) {
      return (
        <div className="definition-row definition-stack">
          <span className="definition-label">{label}</span>
          <div className="definition-value tag-list">
            {value.map((item, index) => <span className="data-tag" key={`${String(item)}-${index}`}>{String(item)}</span>)}
          </div>
        </div>
      );
    }
    return (
      <div className="nested-field">
        <span className="nested-label">{label}</span>
        <div className="nested-list">
          {value.map((item, index) => (
            <div className="nested-item" key={index}>
              <span className="nested-index">{index + 1}</span>
              <StructuredValue value={item} depth={depth + 1} />
            </div>
          ))}
        </div>
      </div>
    );
  }
  return (
    <div className="nested-field">
      <span className="nested-label">{label}</span>
      <div className="nested-object">
        {Object.entries(value).map(([key, child]) => (
          <StructuredField key={key} fieldKey={key} value={child} depth={depth + 1} />
        ))}
      </div>
    </div>
  );
}

function StructuredValue({ value, depth }: { value: JsonValue; depth: number }) {
  if (value === null || typeof value !== "object") return <span>{String(value ?? "-")}</span>;
  if (Array.isArray(value)) {
    return <div>{value.map((item, index) => <StructuredValue key={index} value={item} depth={depth + 1} />)}</div>;
  }
  return (
    <dl className="nested-object">
      {Object.entries(value).map(([key, child]) => (
        <StructuredField key={key} fieldKey={key} value={child} depth={depth + 1} />
      ))}
    </dl>
  );
}

function DefinitionRow({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="definition-row">
      <span className="definition-label">{label}</span>
      <div className={`definition-value${mono ? " mono" : ""}`}>{value}</div>
    </div>
  );
}

function StatusBadge({ value }: { value: string }) {
  const tone = statusTone(value);
  return <span className={`status-badge status-${tone}`}>{value}</span>;
}

function humanize(value: string): string {
  return value.replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase());
}

function isIdentifier(key: string): boolean {
  return key.endsWith("_id") || key.endsWith("_digest") || key === "locator";
}

function statusTone(value: string): string {
  if (/missing|changed|failed|rejected|stale|warning|error/i.test(value)) return "warning";
  if (/current|success|ai_checked|passed|canonical/i.test(value)) return "current";
  if (/operational|background|not_fact/i.test(value)) return "context";
  return "neutral";
}
