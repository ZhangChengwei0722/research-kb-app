import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ExchangeView } from "../src/components/ExchangeView";


const api = vi.hoisted(() => ({
  getExchangeCapabilities: vi.fn(),
  listExchangeImports: vi.fn(),
  listCatalogItems: vi.fn(),
  previewExchangeExport: vi.fn(),
  buildExchangeExport: vi.fn(),
  exchangeDownloadUrl: vi.fn(),
  uploadExchangeImport: vi.fn(),
  previewExchangeImport: vi.fn(),
  applyExchangeImport: vi.fn(),
  getExchangeImport: vi.fn(),
}));


vi.mock("../src/api", () => ({
  ...api,
  ApiError: class ApiError extends Error {},
}));


describe("P10 Exchange work surface", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getExchangeCapabilities.mockResolvedValue({
      status: "success",
      bundle_format: "research-kb-exchange-bundle@1.0",
      selectors: ["paper", "question", "direction", "workspace"],
      source_inclusion_available: true,
      import_available: true,
      safe_reader_profile: { profile_id: "p10-exchange-safe-reader-v1", max_archive_bytes: 4096 },
      browser_paths_accepted: false,
      external_records_are_local_facts: false,
      lease_ttl_seconds: 300,
    });
    api.listExchangeImports.mockResolvedValue({ status: "success", imports: [] });
    api.previewExchangeExport.mockResolvedValue({
      status: "success",
      bundle_format: "research-kb-exchange-bundle@1.0",
      selection: { scope: "workspace" },
      record_count: 42,
      record_kind_counts: { "exchange-paper-identity": 4, evidence: 38 },
      structured_bytes: 1200,
      estimated_archive_bytes: 1300,
      source_count: 0,
      pdf_count: 0,
      missing_source_count: 0,
      rights_status: "not_required",
      preview_token: "export-preview-token-00000000000000000000",
      preview_ttl_seconds: 300,
    });
    api.buildExchangeExport.mockResolvedValue({
      status: "success",
      result: "created",
      export_id: "export_one",
      selection: { scope: "workspace" },
      record_count: 42,
      source_count: 0,
      archive_sha256: "a".repeat(64),
      archive_bytes: 1300,
      download_token: "download-token-0000000000000000000000",
      download_filename: "research-kb-workspace-export_one.rkb-exchange.zip",
      download_ttl_seconds: 300,
    });
    api.exchangeDownloadUrl.mockReturnValue("/api/exchange/export/download/token");
    api.uploadExchangeImport.mockResolvedValue({
      status: "success",
      upload_token: "upload-token-000000000000000000000000",
      archive_bytes: 1300,
      upload_ttl_seconds: 300,
    });
    api.previewExchangeImport.mockResolvedValue({
      status: "success",
      compatibility: "supported",
      safe_reader_profile_id: "p10-exchange-safe-reader-v1",
      archive_bytes: 1300,
      canonical_serialization: true,
      import_id: "import_one",
      existing_import_id: null,
      origin_workspace_id: "workspace_external",
      selection: { scope: "workspace" },
      record_count: 42,
      record_kind_counts: { evidence: 38 },
      source_count: 0,
      include_sources: false,
      rights_assertion: null,
      trust_projection: "unsigned_external_claims",
      conflict_counts: { semantic_conflict: 2, new_external_revision: 40 },
      conflicts: [],
      conflicts_truncated: false,
      preview_token: "import-preview-token-00000000000000000000",
      preview_ttl_seconds: 300,
    });
    api.applyExchangeImport.mockResolvedValue({
      status: "success",
      result: "imported",
      import_id: "import_one",
      origin_workspace_id: "workspace_external",
      record_count: 42,
      source_count: 0,
      trust_projection: "unsigned_external_claims",
      canonical_scientific_write: false,
    });
  });

  it("previews, builds and exposes only one opaque archive download", async () => {
    render(<ExchangeView />);
    expect(await screen.findByRole("heading", { name: "知识库交换" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "预览导出" }));
    expect(await screen.findByLabelText("导出预览")).toHaveTextContent("42");
    fireEvent.click(screen.getByRole("button", { name: "生成 Archive" }));

    const download = await screen.findByRole("link", { name: "下载" });
    expect(download).toHaveAttribute("href", "/api/exchange/export/download/token");
    expect(screen.queryByText(/[A-Z]:\\/)).not.toBeInTheDocument();
    fireEvent.click(download);
    expect(screen.getByRole("button", { name: "已下载" })).toBeDisabled();
    expect(screen.queryByRole("link", { name: "下载" })).not.toBeInTheDocument();
  });

  it("uploads, previews and imports only as external local-review-pending data", async () => {
    api.listExchangeImports
      .mockResolvedValueOnce({ status: "success", imports: [] })
      .mockResolvedValueOnce({
        status: "success",
        imports: [{
          import_id: "import_one",
          local_workspace_id: "workspace_local",
          origin_workspace_id: "workspace_external",
          export_id: "export_one",
          record_count: 42,
          source_count: 0,
          conflict_counts: { semantic_conflict: 2 },
          local_review_status: "unreviewed",
          trust_projection: "unsigned_external_claims",
          created_at: "2026-08-04T00:00:00Z",
        }],
      });
    render(<ExchangeView />);
    await screen.findByRole("heading", { name: "知识库交换" });
    const file = new File(["PK\x03\x04archive"], "bundle.rkb-exchange.zip", { type: "application/vnd.research-kb.exchange+zip" });
    fireEvent.change(screen.getByLabelText("选择 .rkb-exchange.zip"), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "上传并预检" }));

    expect(await screen.findByLabelText("导入预览")).toHaveTextContent("external · local review pending");
    expect(screen.queryByRole("button", { name: /use as local fact/i })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "批准导入" }));
    await waitFor(() => expect(api.applyExchangeImport).toHaveBeenCalledOnce());
    expect(await screen.findByText("workspace_external")).toBeInTheDocument();
  });

  it("renders hostile imported labels as text and never as HTML", async () => {
    api.listExchangeImports.mockResolvedValue({
      status: "success",
      imports: [{
        import_id: "import_one",
        local_workspace_id: "workspace_local",
        origin_workspace_id: "workspace_external",
        export_id: "export_one",
        record_count: 1,
        source_count: 0,
        conflict_counts: {},
        local_review_status: "unreviewed",
        trust_projection: "unsigned_external_claims",
        created_at: "2026-08-04T00:00:00Z",
      }],
    });
    api.getExchangeImport.mockResolvedValue({
      status: "success",
      import: (await api.listExchangeImports()).imports[0],
      selection: { scope: "workspace" },
      record_kind_counts: { evidence: 1 },
      include_sources: false,
      rights_assertion: null,
      records: [{
        origin_workspace_id: "workspace_external",
        origin_record_id: "evidence_one",
        record_kind: "evidence",
        revision_digest: "b".repeat(64),
        label: '<img src=x onerror="alert(1)">',
        local_admissibility: "external_unreviewed",
        trust_projection: "unsigned_external_claims",
      }],
      records_truncated: false,
    });
    render(<ExchangeView />);
    fireEvent.click(await screen.findByRole("listitem"));

    expect(await screen.findByText('<img src=x onerror="alert(1)">')).toBeInTheDocument();
    expect(document.querySelector(".exchange-record-list img")).toBeNull();
  });
});
