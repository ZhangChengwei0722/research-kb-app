import { useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, ExternalLink, FileText, LoaderCircle, ZoomIn, ZoomOut } from "lucide-react";
import {
  GlobalWorkerOptions,
  Util,
  getDocument,
  type PDFDocumentLoadingTask,
  type PDFDocumentProxy,
  type PDFPageProxy,
  type RenderTask,
} from "pdfjs-dist";
import type { TextItem } from "pdfjs-dist/types/src/display/api";
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import {
  ApiError,
  createEvidencePdfHandle,
  evidencePdfUrl,
  openEvidencePdfExternally,
} from "../api";

GlobalWorkerOptions.workerSrc = pdfWorkerUrl;

const MIN_ZOOM = 0.75;
const MAX_ZOOM = 2;
const ZOOM_STEP = 0.25;

type HighlightRect = {
  left: number;
  top: number;
  width: number;
  height: number;
};

type ViewerPhase = "idle" | "issuing" | "loading" | "ready" | "error";

export function EvidencePdfViewer({
  evidenceId,
  quote,
  targetPage,
  locator,
}: {
  evidenceId: string;
  quote: string;
  targetPage: number;
  locator: string;
}) {
  const [phase, setPhase] = useState<ViewerPhase>("idle");
  const [error, setError] = useState("");
  const [handleId, setHandleId] = useState<string | null>(null);
  const [documentProxy, setDocumentProxy] = useState<PDFDocumentProxy | null>(null);
  const [pageNumber, setPageNumber] = useState(targetPage);
  const [zoom, setZoom] = useState(1);
  const [pageSize, setPageSize] = useState({ width: 0, height: 0 });
  const [highlights, setHighlights] = useState<HighlightRect[]>([]);
  const [quoteLocated, setQuoteLocated] = useState<boolean | null>(null);
  const [externalStatus, setExternalStatus] = useState("");
  const [externalLoading, setExternalLoading] = useState(false);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const requestGeneration = useRef(0);

  useEffect(() => {
    requestGeneration.current += 1;
    setPhase("idle");
    setError("");
    setHandleId(null);
    setDocumentProxy(null);
    setPageNumber(targetPage);
    setZoom(1);
    setPageSize({ width: 0, height: 0 });
    setHighlights([]);
    setQuoteLocated(null);
    setExternalStatus("");
  }, [evidenceId, targetPage]);

  useEffect(() => {
    if (!handleId) return;
    const generation = requestGeneration.current;
    const loadingTask: PDFDocumentLoadingTask = getDocument({
      url: evidencePdfUrl(handleId),
      withCredentials: true,
    });
    setPhase("loading");
    loadingTask.promise.then((loaded) => {
      if (requestGeneration.current !== generation) {
        return;
      }
      setDocumentProxy(loaded);
      setPageNumber((current) => clampPage(current, loaded.numPages));
    }).catch((caught: unknown) => {
      if (requestGeneration.current === generation) {
        setError(pdfErrorMessage(caught));
        setPhase("error");
      }
    });
    return () => {
      void loadingTask.destroy();
    };
  }, [handleId]);

  useEffect(() => {
    if (!documentProxy) return;
    const canvas = canvasRef.current;
    if (!canvas) return;
    const generation = requestGeneration.current;
    let renderTask: RenderTask | null = null;
    let cancelled = false;
    setPhase("loading");
    setHighlights([]);
    setQuoteLocated(null);

    void documentProxy.getPage(pageNumber).then(async (page: PDFPageProxy) => {
      if (cancelled || requestGeneration.current !== generation) return;
      const viewport = page.getViewport({ scale: zoom });
      const outputScale = Math.max(1, window.devicePixelRatio || 1);
      canvas.width = Math.floor(viewport.width * outputScale);
      canvas.height = Math.floor(viewport.height * outputScale);
      canvas.style.width = `${viewport.width}px`;
      canvas.style.height = `${viewport.height}px`;
      setPageSize({ width: viewport.width, height: viewport.height });
      renderTask = page.render({
        canvas,
        canvasContext: canvas.getContext("2d") ?? undefined,
        viewport,
        transform: outputScale === 1 ? undefined : [outputScale, 0, 0, outputScale, 0, 0],
      });
      const [textContent] = await Promise.all([page.getTextContent(), renderTask.promise]);
      if (cancelled || requestGeneration.current !== generation) return;
      const located = locateQuote(
        textContent.items.filter(isTextItem),
        quote,
        viewport,
      );
      setHighlights(located);
      setQuoteLocated(located.length > 0);
      setPhase("ready");
    }).catch((caught: unknown) => {
      if (!cancelled && requestGeneration.current === generation && !isRenderingCancelled(caught)) {
        setError("PDF 页面渲染失败，可重试或使用本地阅读器打开。");
        setPhase("error");
      }
    });
    return () => {
      cancelled = true;
      renderTask?.cancel();
    };
  }, [documentProxy, pageNumber, quote, zoom]);

  useEffect(() => () => {
    requestGeneration.current += 1;
  }, []);

  async function issueHandle() {
    const generation = requestGeneration.current + 1;
    requestGeneration.current = generation;
    setPhase("issuing");
    setError("");
    setHandleId(null);
    setDocumentProxy(null);
    setHighlights([]);
    setQuoteLocated(null);
    setExternalStatus("");
    try {
      const issued = await createEvidencePdfHandle(evidenceId);
      if (requestGeneration.current !== generation) return;
      setPageNumber(issued.pdf_page);
      setHandleId(issued.handle_id);
    } catch (caught) {
      if (requestGeneration.current !== generation) return;
      setError(pdfErrorMessage(caught));
      setPhase("error");
    }
  }

  async function openExternal() {
    if (!handleId || externalLoading) return;
    setExternalLoading(true);
    setExternalStatus("");
    try {
      const result = await openEvidencePdfExternally(handleId);
      setExternalStatus(
        result.reader === "updf"
          ? `已在 UPDF 中打开，请手动定位到 PDF ${result.pdf_page}。`
          : `已使用系统 PDF 阅读器打开，请手动定位到 PDF ${result.pdf_page}。`,
      );
    } catch (caught) {
      setExternalStatus(pdfErrorMessage(caught));
    } finally {
      setExternalLoading(false);
    }
  }

  if (phase === "idle") {
    return (
      <div className="pdf-viewer pdf-viewer-idle">
        <FileText size={22} aria-hidden="true" />
        <button type="button" onClick={() => void issueHandle()}>打开 Evidence PDF</button>
      </div>
    );
  }

  if (phase === "issuing") {
    return <PdfStatus icon={LoaderCircle} label="正在验证 PDF 来源" spinning />;
  }

  if (phase === "error") {
    return (
      <div className="pdf-viewer-error" role="alert">
        <span>{error}</span>
        <button type="button" onClick={() => void issueHandle()}>重试打开 PDF</button>
      </div>
    );
  }

  return (
    <div className="pdf-viewer" data-testid="evidence-pdf-viewer">
      <div className="pdf-toolbar" aria-label="PDF 阅读工具">
        <button className="icon-button compact-icon" type="button" disabled={pageNumber <= 1 || phase === "loading"} onClick={() => setPageNumber((page) => page - 1)} title="上一页" aria-label="上一页">
          <ChevronLeft size={17} />
        </button>
        <span className="pdf-page-count">{documentProxy ? `${pageNumber} / ${documentProxy.numPages}` : `PDF ${pageNumber}`}</span>
        <button className="icon-button compact-icon" type="button" disabled={!documentProxy || pageNumber >= documentProxy.numPages || phase === "loading"} onClick={() => setPageNumber((page) => page + 1)} title="下一页" aria-label="下一页">
          <ChevronRight size={17} />
        </button>
        <span className="pdf-toolbar-divider" />
        <button className="icon-button compact-icon" type="button" disabled={zoom <= MIN_ZOOM || phase === "loading"} onClick={() => setZoom((value) => Math.max(MIN_ZOOM, value - ZOOM_STEP))} title="缩小" aria-label="缩小">
          <ZoomOut size={16} />
        </button>
        <span className="pdf-zoom-value">{Math.round(zoom * 100)}%</span>
        <button className="icon-button compact-icon" type="button" disabled={zoom >= MAX_ZOOM || phase === "loading"} onClick={() => setZoom((value) => Math.min(MAX_ZOOM, value + ZOOM_STEP))} title="放大" aria-label="放大">
          <ZoomIn size={16} />
        </button>
        <button className="pdf-external-button" type="button" disabled={externalLoading} onClick={() => void openExternal()}>
          <ExternalLink size={15} aria-hidden="true" />
          在 UPDF 中打开
        </button>
      </div>
      <div className="pdf-page-frame" aria-busy={phase === "loading"}>
        {phase === "loading" && <PdfStatus icon={LoaderCircle} label="正在渲染 PDF 页面" spinning overlay />}
        <div
          className="pdf-page-surface"
          style={{ width: pageSize.width || undefined, aspectRatio: pageSize.width > 0 ? `${pageSize.width} / ${pageSize.height}` : "3 / 4" }}
        >
          <canvas ref={canvasRef} aria-label={`Evidence PDF 第 ${pageNumber} 页`} />
          <div className="pdf-highlight-layer" aria-hidden="true">
            {highlights.map((rect, index) => (
              <span
                key={`${rect.left}-${rect.top}-${index}`}
                data-testid="evidence-highlight"
                style={{
                  left: `${rect.left}%`,
                  top: `${rect.top}%`,
                  width: `${rect.width}%`,
                  height: `${rect.height}%`,
                }}
              />
            ))}
          </div>
        </div>
      </div>
      {phase === "ready" && (
        <div className={`pdf-quote-status ${quoteLocated ? "pdf-quote-found" : "pdf-quote-missing"}`}>
          {quoteLocated ? "已定位原文摘录" : "当前页面未定位到原文摘录"}
        </div>
      )}
      <div className="pdf-manual-location">PDF {targetPage} · <code>{locator}</code></div>
      {externalStatus && <div className="pdf-external-status" role="status">{externalStatus}</div>}
    </div>
  );
}

function PdfStatus({
  icon: Icon,
  label,
  spinning = false,
  overlay = false,
}: {
  icon: typeof FileText;
  label: string;
  spinning?: boolean;
  overlay?: boolean;
}) {
  return (
    <div className={`pdf-viewer-status${overlay ? " pdf-viewer-status-overlay" : ""}`} role="status">
      <Icon className={spinning ? "spin" : ""} size={20} aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}

function locateQuote(items: TextItem[], quote: string, viewport: ReturnType<PDFPageProxy["getViewport"]>): HighlightRect[] {
  const normalizedItems = items.map((item) => normalizeText(item.str));
  const joined = normalizedItems.join(" ");
  const needle = normalizeText(quote);
  const start = needle ? joined.indexOf(needle) : -1;
  if (start < 0) return [];
  const end = start + needle.length;
  let cursor = 0;
  const matched: TextItem[] = [];
  for (let index = 0; index < items.length; index += 1) {
    const itemStart = cursor;
    const itemEnd = itemStart + normalizedItems[index].length;
    if (itemEnd > start && itemStart < end) matched.push(items[index]);
    cursor = itemEnd + 1;
  }
  return matched.map((item) => itemRect(item, viewport));
}

function itemRect(item: TextItem, viewport: ReturnType<PDFPageProxy["getViewport"]>): HighlightRect {
  const transformed = Util.transform(viewport.transform, item.transform);
  const height = Math.max(Math.hypot(transformed[2], transformed[3]), item.height * viewport.scale, 4);
  const width = Math.max(item.width * viewport.scale, 4);
  const left = Math.max(0, transformed[4]);
  const top = Math.max(0, transformed[5] - height);
  return {
    left: (left / viewport.width) * 100,
    top: (top / viewport.height) * 100,
    width: (Math.min(width, viewport.width - left) / viewport.width) * 100,
    height: (Math.min(height, viewport.height - top) / viewport.height) * 100,
  };
}

function normalizeText(value: string): string {
  return value.normalize("NFKC").replace(/\s+/gu, " ").trim();
}

function isTextItem(value: unknown): value is TextItem {
  return typeof value === "object" && value !== null && "str" in value && typeof value.str === "string";
}

function clampPage(page: number, pageCount: number): number {
  return Math.min(Math.max(1, page), Math.max(1, pageCount));
}

function isRenderingCancelled(caught: unknown): boolean {
  return caught instanceof Error && caught.name === "RenderingCancelledException";
}

function pdfErrorMessage(caught: unknown): string {
  if (caught instanceof ApiError) {
    if (caught.code === "RKBC-014" || caught.code === "RKBC-017") return "源文件已变化，需重新登记或重解析后再回源。";
    if (caught.code === "RKBC-029") return "当前来源不是可安全读取的 PDF。";
    if (caught.code === "RKBC-030") return "PDF 超出浏览器回源大小上限，请使用受控外部阅读器。";
    if (caught.code === "RKBAPP-PDF-HANDLE-EXPIRED") return "PDF 访问已过期，请重新打开。";
    if (caught.code === "RKBAPP-PDF-READER") return "本地 PDF 阅读器未能打开，请继续使用内置阅读器。";
    return `${caught.code}: ${caught.message}`;
  }
  return "PDF 暂时不可用，可能已过期、变更或无法渲染。";
}
