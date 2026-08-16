import { ChangeEvent, useEffect, useMemo, useState } from "react";
import {
  Archive,
  Download,
  Eye,
  FileArchive,
  PackageOpen,
  RefreshCw,
  Search,
  ShieldCheck,
  TriangleAlert,
  Upload,
} from "lucide-react";
import {
  ApiError,
  applyExchangeImport,
  buildExchangeExport,
  exchangeDownloadUrl,
  getExchangeCapabilities,
  getExchangeImport,
  listCatalogItems,
  listExchangeImports,
  previewExchangeExport,
  previewExchangeImport,
  uploadExchangeImport,
  type CatalogItem,
  type ExchangeCapabilities,
  type ExchangeExportBuild,
  type ExchangeExportPreview,
  type ExchangeImportDetail,
  type ExchangeImportPreview,
  type ExchangeImportReceipt,
  type ExchangeScope,
} from "../api";


const SCOPES: Array<{ value: ExchangeScope; label: string }> = [
  { value: "paper", label: "论文" },
  { value: "question", label: "问题" },
  { value: "direction", label: "方向" },
  { value: "workspace", label: "工作区" },
];


export function ExchangeView() {
  const [capabilities, setCapabilities] = useState<ExchangeCapabilities | null>(null);
  const [scope, setScope] = useState<ExchangeScope>("workspace");
  const [targetQuery, setTargetQuery] = useState("");
  const [targets, setTargets] = useState<CatalogItem[]>([]);
  const [selectorId, setSelectorId] = useState("");
  const [includeSources, setIncludeSources] = useState(false);
  const [rightsAsserted, setRightsAsserted] = useState(false);
  const [exportPreview, setExportPreview] = useState<ExchangeExportPreview | null>(null);
  const [built, setBuilt] = useState<ExchangeExportBuild | null>(null);
  const [downloaded, setDownloaded] = useState(false);
  const [archive, setArchive] = useState<File | null>(null);
  const [importPreview, setImportPreview] = useState<ExchangeImportPreview | null>(null);
  const [imports, setImports] = useState<ExchangeImportReceipt[]>([]);
  const [detail, setDetail] = useState<ExchangeImportDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    let active = true;
    Promise.all([getExchangeCapabilities(), listExchangeImports()])
      .then(([nextCapabilities, nextImports]) => {
        if (!active) return;
        setCapabilities(nextCapabilities);
        setImports(nextImports.imports);
      })
      .catch((caught: unknown) => { if (active) setError(errorMessage(caught)); });
    return () => { active = false; };
  }, []);

  const selectedTarget = useMemo(
    () => targets.find((item) => targetId(scope, item) === selectorId) ?? null,
    [scope, selectorId, targets],
  );

  function changeScope(next: ExchangeScope) {
    setScope(next);
    setTargets([]);
    setSelectorId("");
    setExportPreview(null);
    setBuilt(null);
    setDownloaded(false);
    setNotice("");
  }

  async function searchTargets() {
    if (scope === "workspace") return;
    setBusy(true);
    setError("");
    try {
      const result = await listCatalogItems({
        query: targetQuery.trim() || undefined,
        itemKinds: [scope],
        pageSize: 20,
      });
      setTargets(result.items);
      const nextId = result.items[0] ? targetId(scope, result.items[0]) : "";
      setSelectorId(nextId);
      setExportPreview(null);
      setBuilt(null);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function previewExport() {
    if (scope !== "workspace" && !selectorId) return;
    setBusy(true);
    setError("");
    setNotice("");
    setBuilt(null);
    try {
      setExportPreview(await previewExchangeExport({
        scope,
        selector_id: scope === "workspace" ? null : selectorId,
        include_sources: includeSources,
        rights_asserted: includeSources && rightsAsserted,
      }));
    } catch (caught) {
      setExportPreview(null);
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function buildExport() {
    if (!exportPreview) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await buildExchangeExport(exportPreview.preview_token);
      setBuilt(result);
      setDownloaded(false);
      setExportPreview(null);
      setNotice("Archive 已生成");
    } catch (caught) {
      setExportPreview(null);
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  function selectArchive(event: ChangeEvent<HTMLInputElement>) {
    setArchive(event.target.files?.[0] ?? null);
    setImportPreview(null);
    setNotice("");
  }

  async function previewImport() {
    if (!archive) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const upload = await uploadExchangeImport(archive);
      setImportPreview(await previewExchangeImport(upload.upload_token));
    } catch (caught) {
      setImportPreview(null);
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function applyImport() {
    if (!importPreview?.preview_token) return;
    setBusy(true);
    setError("");
    try {
      const result = await applyExchangeImport(importPreview.preview_token);
      const refreshed = await listExchangeImports();
      setImports(refreshed.imports);
      setImportPreview(null);
      setArchive(null);
      setNotice(result.result === "no_change" ? "Archive 已存在" : "外部知识包已导入");
    } catch (caught) {
      setImportPreview(null);
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function showImport(importId: string) {
    setBusy(true);
    setError("");
    try {
      setDetail(await getExchangeImport(importId));
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="exchange-view" aria-labelledby="exchange-title">
      <header className="view-heading">
        <div><p className="section-kicker">KNOWLEDGE EXCHANGE</p><h2 id="exchange-title">知识库交换</h2></div>
        <div className="view-heading-actions">
          <span className={`operation-chip operation-${busy ? "running" : "idle"}`}>{busy ? "processing" : "ready"}</span>
          <span className="status-badge freshness-current">{capabilities?.bundle_format ?? "loading"}</span>
        </div>
      </header>
      {error ? <div className="error-banner" role="alert">{error}</div> : null}
      {notice ? <div className="notice-banner" role="status">{notice}</div> : null}

      <div className="exchange-workbench">
        <section className="exchange-export-pane" aria-labelledby="exchange-export-title">
          <div className="subsection-heading"><div><h3 id="exchange-export-title">导出</h3><span>portable archive</span></div><Archive size={18} /></div>
          <fieldset className="exchange-scope" disabled={busy}>
            <legend>范围</legend>
            {SCOPES.map((item) => (
              <button key={item.value} type="button" className={scope === item.value ? "active" : ""} onClick={() => changeScope(item.value)}>{item.label}</button>
            ))}
          </fieldset>
          {scope !== "workspace" ? (
            <div className="exchange-target-picker">
              <label htmlFor="exchange-target-query">目标</label>
              <div className="exchange-search-row">
                <input id="exchange-target-query" value={targetQuery} onChange={(event) => setTargetQuery(event.target.value)} placeholder="Search" />
                <button className="icon-button" type="button" onClick={() => void searchTargets()} disabled={busy} title="搜索" aria-label="搜索"><Search size={16} /></button>
              </div>
              <select aria-label="导出目标" value={selectorId} onChange={(event) => { setSelectorId(event.target.value); setExportPreview(null); }} disabled={busy || !targets.length}>
                {!targets.length ? <option value="">没有候选</option> : null}
                {targets.map((item) => <option key={item.item_id} value={targetId(scope, item)}>{item.title}</option>)}
              </select>
              {selectedTarget ? <span className="exchange-target-id">{selectorId}</span> : null}
            </div>
          ) : null}
          <label className="exchange-check"><input type="checkbox" checked={includeSources} onChange={(event) => { setIncludeSources(event.target.checked); setRightsAsserted(false); setExportPreview(null); }} />包含 PDF</label>
          {includeSources ? <label className="exchange-check rights-check"><input type="checkbox" checked={rightsAsserted} onChange={(event) => { setRightsAsserted(event.target.checked); setExportPreview(null); }} />已确认本批次可再分发</label> : null}
          <button className="secondary-button" type="button" onClick={() => void previewExport()} disabled={busy || (scope !== "workspace" && !selectorId) || (includeSources && !rightsAsserted)}><Eye size={16} />预览导出</button>
          {exportPreview ? (
            <div className="exchange-preview" aria-label="导出预览">
              <MetricGrid values={[
                ["记录", exportPreview.record_count], ["PDF", exportPreview.pdf_count],
                ["体积", formatBytes(exportPreview.estimated_archive_bytes)], ["缺失源", exportPreview.missing_source_count],
              ]} />
              <KindCounts values={exportPreview.record_kind_counts} />
              <button className="start-button" type="button" onClick={() => void buildExport()} disabled={busy || exportPreview.missing_source_count > 0}><FileArchive size={16} />生成 Archive</button>
            </div>
          ) : null}
          {built ? (
            <div className="exchange-download">
              <span><strong>{built.download_filename}</strong><small>{formatBytes(built.archive_bytes)} · {built.record_count} records</small></span>
              {downloaded ? (
                <button className="secondary-button" type="button" disabled><Download size={16} />已下载</button>
              ) : (
                <a className="secondary-button" href={exchangeDownloadUrl(built.download_token)} download={built.download_filename} onClick={() => setDownloaded(true)}><Download size={16} />下载</a>
              )}
            </div>
          ) : null}
        </section>

        <section className="exchange-import-pane" aria-labelledby="exchange-import-title">
          <div className="subsection-heading"><div><h3 id="exchange-import-title">导入</h3><span>external origin</span></div><Upload size={18} /></div>
          <label className="exchange-file-picker" htmlFor="exchange-file"><FileArchive size={20} /><span>{archive?.name ?? "选择 .rkb-exchange.zip"}</span></label>
          <input id="exchange-file" className="visually-hidden" type="file" accept=".zip,.rkb-exchange.zip,application/vnd.research-kb.exchange+zip" onChange={selectArchive} />
          <button className="secondary-button" type="button" onClick={() => void previewImport()} disabled={busy || !archive}><Eye size={16} />上传并预检</button>
          {importPreview ? (
            <div className="exchange-preview" aria-label="导入预览">
              <div className={`exchange-compatibility compatibility-${importPreview.compatibility}`}>
                {importPreview.compatibility === "supported" ? <ShieldCheck size={17} /> : <TriangleAlert size={17} />}
                <strong>{importPreview.compatibility}</strong>
              </div>
              <MetricGrid values={[
                ["记录", importPreview.record_count], ["PDF", importPreview.source_count],
                ["体积", formatBytes(importPreview.archive_bytes)], ["冲突", sumCounts(importPreview.conflict_counts)],
              ]} />
              <div className="external-boundary"><TriangleAlert size={15} /><span>external · local review pending</span></div>
              <KindCounts values={importPreview.conflict_counts} />
              <button className="start-button" type="button" onClick={() => void applyImport()} disabled={busy || importPreview.compatibility !== "supported" || !importPreview.preview_token}><PackageOpen size={16} />批准导入</button>
            </div>
          ) : null}
        </section>

        <section className="exchange-inventory-pane" aria-labelledby="exchange-inventory-title">
          <div className="subsection-heading"><div><h3 id="exchange-inventory-title">外部知识包</h3><span>{imports.length} packages</span></div><button className="icon-button" type="button" onClick={() => void listExchangeImports().then((result) => setImports(result.imports)).catch((caught) => setError(errorMessage(caught)))} title="刷新" aria-label="刷新"><RefreshCw size={16} /></button></div>
          <div className="exchange-package-list" role="list">
            {imports.map((item) => (
              <button key={item.import_id} type="button" role="listitem" className={detail?.import.import_id === item.import_id ? "selected" : ""} onClick={() => void showImport(item.import_id)}>
                <span><strong>{item.origin_workspace_id}</strong><small>{item.record_count} records · {item.source_count} PDFs</small></span>
                <span className="external-status">external</span>
              </button>
            ))}
            {!imports.length ? <div className="compact-empty">没有外部知识包</div> : null}
          </div>
          {detail ? (
            <div className="exchange-detail" aria-label="外部知识包详情">
              <div className="external-boundary"><TriangleAlert size={15} /><span>external · local review pending</span></div>
              <KindCounts values={detail.record_kind_counts} />
              <div className="exchange-record-list">
                {detail.records.map((record) => (
                  <div key={`${record.record_kind}:${record.origin_record_id}:${record.revision_digest}`}>
                    <span>{record.record_kind}</span>
                    <strong>{record.label}</strong>
                    <small>{record.origin_record_id}</small>
                  </div>
                ))}
              </div>
              {detail.records_truncated ? <div className="compact-empty">仅显示前 100 条</div> : null}
            </div>
          ) : null}
        </section>
      </div>
    </section>
  );
}


function MetricGrid({ values }: { values: Array<[string, string | number]> }) {
  return <div className="exchange-metrics">{values.map(([label, value]) => <div key={label}><span>{label}</span><strong>{value}</strong></div>)}</div>;
}


function KindCounts({ values }: { values: Record<string, number> }) {
  const entries = Object.entries(values);
  return entries.length ? <div className="exchange-kind-counts">{entries.map(([key, value]) => <span key={key}>{key} {value}</span>)}</div> : null;
}


function targetId(scope: ExchangeScope, item: CatalogItem): string {
  if (scope === "paper") return item.paper_id ?? item.record_id;
  if (scope === "question") return item.question_id ?? item.record_id;
  return item.record_id;
}


function sumCounts(values: Record<string, number>): number {
  return Object.values(values).reduce((total, value) => total + value, 0);
}


function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KiB`;
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MiB`;
  return `${(value / 1024 ** 3).toFixed(1)} GiB`;
}


function errorMessage(caught: unknown): string {
  if (caught instanceof ApiError) return `${caught.code}: ${caught.message}`;
  if (caught instanceof Error) return caught.message;
  return "Exchange 操作未完成";
}
