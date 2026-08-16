import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../src/api";
import { EvidencePdfViewer } from "../src/components/EvidencePdfViewer";

const pdfMocks = vi.hoisted(() => ({
  getDocument: vi.fn(),
  getPage: vi.fn(),
  render: vi.fn(),
  getTextContent: vi.fn(),
  loadingDestroy: vi.fn(),
  documentDestroy: vi.fn(),
  renderCancel: vi.fn(),
}));

vi.mock("pdfjs-dist", () => ({
  GlobalWorkerOptions: { workerSrc: "" },
  getDocument: pdfMocks.getDocument,
  Util: {
    transform: (left: number[], right: number[]) => [
      left[0] * right[0],
      0,
      0,
      left[3] * right[3],
      left[4] + right[4] * left[0],
      left[5] + right[5] * left[3],
    ],
  },
}));

vi.mock("../src/api", async () => {
  const actual = await vi.importActual<typeof import("../src/api")>("../src/api");
  return {
    ...actual,
    createEvidencePdfHandle: vi.fn(),
    openEvidencePdfExternally: vi.fn(),
  };
});

describe("Evidence PDF viewer", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Object.defineProperty(HTMLCanvasElement.prototype, "getContext", {
      configurable: true,
      value: vi.fn(() => ({})),
    });
    vi.mocked(api.createEvidencePdfHandle).mockResolvedValue({
      status: "success",
      handle_id: "opaque-handle",
      evidence_id: "evidence_primary",
      pdf_page: 3,
      expires_in_seconds: 900,
    });
    vi.mocked(api.openEvidencePdfExternally).mockResolvedValue({
      status: "success",
      reader: "updf",
      page_targeting: "manual",
      pdf_page: 3,
      locator: "page:3:char:20-50",
    });
    pdfMocks.getTextContent.mockResolvedValue({
      items: [
        textItem("Synthetic", 20, 700, 70),
        textItem("quoted source", 96, 700, 96),
      ],
    });
    pdfMocks.render.mockReturnValue({ promise: Promise.resolve(), cancel: pdfMocks.renderCancel });
    pdfMocks.getPage.mockResolvedValue({
      getViewport: ({ scale }: { scale: number }) => ({
        width: 600 * scale,
        height: 800 * scale,
        scale,
        transform: [scale, 0, 0, -scale, 0, 800 * scale],
      }),
      render: pdfMocks.render,
      getTextContent: pdfMocks.getTextContent,
    });
    pdfMocks.getDocument.mockReturnValue({
      promise: Promise.resolve({
        numPages: 5,
        getPage: pdfMocks.getPage,
        destroy: pdfMocks.documentDestroy,
      }),
      destroy: pdfMocks.loadingDestroy,
    });
  });

  it("loads lazily, renders the target page and marks a normalized quote match", async () => {
    render(<EvidencePdfViewer {...viewerProps()} />);

    expect(api.createEvidencePdfHandle).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "打开 Evidence PDF" }));

    await waitFor(() => expect(pdfMocks.getDocument).toHaveBeenCalledWith({
      url: "/api/reading/pdf/opaque-handle",
      withCredentials: true,
    }));
    await waitFor(() => expect(pdfMocks.getPage).toHaveBeenCalledWith(3));
    expect((await screen.findAllByTestId("evidence-highlight")).length).toBeGreaterThan(0);
    expect(screen.getByText("已定位原文摘录")).toBeVisible();
    expect(screen.getByText("3 / 5")).toBeVisible();
  });

  it("supports stable page and zoom controls", async () => {
    render(<EvidencePdfViewer {...viewerProps()} />);
    fireEvent.click(screen.getByRole("button", { name: "打开 Evidence PDF" }));
    await screen.findByText("已定位原文摘录");

    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    await waitFor(() => expect(pdfMocks.getPage).toHaveBeenLastCalledWith(4));
    fireEvent.click(screen.getByRole("button", { name: "放大" }));
    await waitFor(() => expect(screen.getByText("125%" )).toBeVisible());
  });

  it("reports quote-not-located without claiming a highlight", async () => {
    pdfMocks.getTextContent.mockResolvedValueOnce({ items: [textItem("Different page", 20, 700, 90)] });
    render(<EvidencePdfViewer {...viewerProps()} />);
    fireEvent.click(screen.getByRole("button", { name: "打开 Evidence PDF" }));

    expect(await screen.findByText("当前页面未定位到原文摘录")).toBeVisible();
    expect(screen.queryByTestId("evidence-highlight")).not.toBeInTheDocument();
  });

  it("keeps external-reader fallback explicit", async () => {
    vi.mocked(api.openEvidencePdfExternally).mockResolvedValueOnce({
      status: "success",
      reader: "system",
      page_targeting: "manual",
      pdf_page: 3,
      locator: "page:3:char:20-50",
    });
    render(<EvidencePdfViewer {...viewerProps()} />);
    fireEvent.click(screen.getByRole("button", { name: "打开 Evidence PDF" }));
    await screen.findByText("3 / 5");
    fireEvent.click(screen.getByRole("button", { name: "在 UPDF 中打开" }));

    expect(await screen.findByText("已使用系统 PDF 阅读器打开，请手动定位到 PDF 3。")).toBeVisible();
  });

  it("shows changed-source state and allows a fresh retry", async () => {
    vi.mocked(api.createEvidencePdfHandle).mockRejectedValueOnce(
      new api.ApiError("Source changed", 409, "RKBC-014"),
    );
    render(<EvidencePdfViewer {...viewerProps()} />);
    fireEvent.click(screen.getByRole("button", { name: "打开 Evidence PDF" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("源文件已变化，需重新登记或重解析后再回源");
    fireEvent.click(screen.getByRole("button", { name: "重试打开 PDF" }));
    expect(await screen.findByText("3 / 5")).toBeVisible();
  });

  it("ignores an obsolete handle response after Evidence changes", async () => {
    let resolveOld: ((value: api.EvidencePdfHandle) => void) | undefined;
    vi.mocked(api.createEvidencePdfHandle)
      .mockReturnValueOnce(new Promise((resolve) => { resolveOld = resolve; }))
      .mockResolvedValueOnce({
        status: "success",
        handle_id: "new-handle",
        evidence_id: "evidence_new",
        pdf_page: 2,
        expires_in_seconds: 900,
      });
    const { rerender } = render(<EvidencePdfViewer {...viewerProps()} />);
    fireEvent.click(screen.getByRole("button", { name: "打开 Evidence PDF" }));
    rerender(<EvidencePdfViewer {...viewerProps({ evidenceId: "evidence_new", targetPage: 2 })} />);
    fireEvent.click(screen.getByRole("button", { name: "打开 Evidence PDF" }));
    await waitFor(() => expect(pdfMocks.getDocument).toHaveBeenCalledWith(expect.objectContaining({
      url: "/api/reading/pdf/new-handle",
    })));

    resolveOld?.({
      status: "success",
      handle_id: "old-handle",
      evidence_id: "evidence_primary",
      pdf_page: 3,
      expires_in_seconds: 900,
    });
    await Promise.resolve();
    expect(pdfMocks.getDocument).not.toHaveBeenCalledWith(expect.objectContaining({
      url: "/api/reading/pdf/old-handle",
    }));
  });
});

function viewerProps(overrides: Partial<React.ComponentProps<typeof EvidencePdfViewer>> = {}) {
  return {
    evidenceId: "evidence_primary",
    quote: "Synthetic   quoted source",
    targetPage: 3,
    locator: "page:3:char:20-50",
    ...overrides,
  };
}

function textItem(str: string, x: number, y: number, width: number) {
  return {
    str,
    dir: "ltr",
    transform: [1, 0, 0, 12, x, y],
    width,
    height: 12,
    fontName: "fixture",
    hasEOL: false,
  };
}
