import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "../src/App";

const api = vi.hoisted(() => ({
  bootstrap: vi.fn(),
  getSetupStatus: vi.fn(),
  listWorkspaces: vi.fn(),
  openWorkspace: vi.fn(),
  getCatalogStatus: vi.fn(),
  rebuildCatalog: vi.fn(),
  listCatalogItems: vi.fn(),
  getCatalogItem: vi.fn(),
  getCapabilities: vi.fn(),
  getHealth: vi.fn(),
  listInboxCandidates: vi.fn(),
  listIntakeJobs: vi.fn(),
  getIntakeJob: vi.fn(),
  getAgentRegistry: vi.fn(),
  listAgentTasks: vi.fn(),
  getAgentTask: vi.fn(),
  getReadingPaper: vi.fn(),
  getTag: vi.fn(),
  listTags: vi.fn(),
  listTargetTags: vi.fn(),
  promoteTag: vi.fn(),
  setTagAssignment: vi.fn(),
  inspectAgentHandoff: vi.fn(),
  prepareAgentHandoff: vi.fn(),
  createAgentTask: vi.fn(),
  submitAgentResult: vi.fn(),
  getAgentPreview: vi.fn(),
  requestAgentRevision: vi.fn(),
  refreshAgentTask: vi.fn(),
  rejectAgentResult: vi.fn(),
  approveAgentResult: vi.fn(),
  createKnowledgeQuery: vi.fn(),
  acceptKnowledgeQueryReport: vi.fn(),
  startUploadIntake: vi.fn(),
  startInboxIntake: vi.fn(),
  resumeIntakeJob: vi.fn(),
  cancelIntakeJob: vi.fn(),
  getExchangeCapabilities: vi.fn(),
  listExchangeImports: vi.fn(),
  shutdown: vi.fn(),
}));

vi.mock("../src/api", () => ({
  ...api,
  ApiError: class ApiError extends Error {},
}));

describe("P2-D read-only product shell", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.bootstrap.mockResolvedValue(undefined);
    api.getSetupStatus.mockResolvedValue({
      status: "success",
      interface_version: "research-kb-app-setup@1.0",
      mode: "explicit_config",
      recovery_available: false,
    });
    api.listWorkspaces.mockResolvedValue([
      {
        option_id: "p2-small",
        label: "P2 Small Synthetic",
        workspace_id: "workspace-synthetic",
        domain_profile_id: "domain-synthetic",
        domain_name: "Synthetic",
        domain_version: "1.0",
      },
    ]);
    api.openWorkspace.mockResolvedValue({
      projection_state: "current",
      item_count: 40,
      operation: { state: "idle", diagnostic_code: null },
    });
    api.getCapabilities.mockResolvedValue({
      status: "success",
      app: { canonical_scientific_writes: false },
      core: {},
      catalog: {
        raw_parsed_text_indexed: false,
        adapters: [{ record_kind: "registry-paper", adapter_version: "1.0" }],
      },
    });
    api.getHealth.mockResolvedValue({
      status: "success",
      process_ready: true,
      core_compatible: true,
      workspace_selected: true,
      projection_state: "current",
      operation: { category: null, state: "idle", job_id: null, diagnostic_code: null },
    });
    api.listInboxCandidates.mockResolvedValue({ status: "success", candidates: [], persistent_writes: 0 });
    api.listIntakeJobs.mockResolvedValue({ status: "success", jobs: [], next_cursor: null, persistent_writes: 0 });
    api.getAgentRegistry.mockResolvedValue({
      status: "success",
      registry_version: "p5c-v1",
      content_classes: [],
      task_kinds: [],
      executors: [],
      embedded_agent_runtime: false,
      workspace_policy: null,
    });
    api.listAgentTasks.mockResolvedValue({ status: "success", tasks: [], next_cursor: null });
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
    api.listTags.mockResolvedValue({ status: "success", tags: [], next_cursor: null, persistent_writes: 0, canonical_scientific_write: false });
    api.listCatalogItems.mockResolvedValue({
      status: "success",
      query: "",
      item_kinds: ["paper"],
      page_size: 8,
      items: [{
        item_id: "catalog_1234",
        item_kind: "paper",
        authority_layer: "canonical",
        record_kind: "registry-paper",
        record_id: "paper_1234",
        child_id: null,
        paper_id: "paper_1234",
        question_id: null,
        title: "Synthetic Primary Record",
        summary: "Synthetic Author, 2026",
        status_labels: ["review:ai_checked"],
        sort_key: "synthetic primary record",
        source_record_digest: "digest",
        adapter_version: "1.0",
        tags: [],
      }],
      next_cursor: "next-cursor",
      has_more: true,
      projection_state: "current",
      source_watermark: "watermark",
    });
    api.getCatalogItem.mockResolvedValue({
      status: "success",
      projection_state: "current",
      current_record_status: "current",
      item: {
        item_id: "catalog_1234",
        item_kind: "paper",
        authority_layer: "canonical",
        record_kind: "registry-paper",
        record_id: "paper_1234",
        child_id: null,
        paper_id: "paper_1234",
        question_id: null,
        title: "Synthetic Primary Record",
        summary: "Synthetic Author, 2026",
        status_labels: ["review:ai_checked"],
        sort_key: "synthetic primary record",
        source_record_digest: "digest",
        adapter_version: "1.0",
        tags: [],
      },
      detail: {
        screening_status: "candidate",
        review_status: "ai_checked",
      },
    });
  });

  it("clears the one-time token and exposes configured options after bootstrap", async () => {
    render(<App />);
    const input = screen.getByLabelText("一次性 Token");
    fireEvent.change(input, { target: { value: "test-token-00000000000000000000000000" } });
    fireEvent.click(screen.getByRole("button", { name: "验证" }));

    await waitFor(() => expect(api.bootstrap).toHaveBeenCalledOnce());
    expect(screen.queryByLabelText("一次性 Token")).not.toBeInTheDocument();
    expect(screen.getByRole("option", { name: "P2 Small Synthetic" })).toBeInTheDocument();
    expect(screen.getByText("只读工作区")).toBeInTheDocument();
  });

  it("routes a managed first run to workspace setup before the product shell", async () => {
    api.getSetupStatus.mockResolvedValueOnce({
      status: "success",
      interface_version: "research-kb-app-setup@1.0",
      mode: "first_run",
      profile_id: "default",
      current_revision_id: null,
      recovery_available: false,
    });
    api.listWorkspaces.mockResolvedValueOnce([]);
    render(<App />);
    fireEvent.change(screen.getByLabelText("一次性 Token"), { target: { value: "test-token-00000000000000000000000000" } });
    fireEvent.click(screen.getByRole("button", { name: "验证" }));

    expect(await screen.findByRole("heading", { name: "工作区设置" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "主要视图" })).not.toBeInTheDocument();
  });

  it("opens a workspace, filters the Library, paginates, and inspects current detail", async () => {
    render(<App />);
    fireEvent.change(screen.getByLabelText("一次性 Token"), { target: { value: "test-token-00000000000000000000000000" } });
    fireEvent.click(screen.getByRole("button", { name: "验证" }));
    await screen.findByRole("button", { name: "打开" });
    fireEvent.click(screen.getByRole("button", { name: "打开" }));

    await screen.findByRole("heading", { name: "P2 Small Synthetic" });
    fireEvent.click(screen.getByRole("button", { name: "文献" }));
    await screen.findByText("Synthetic Primary Record");

    fireEvent.change(screen.getByLabelText("类型"), { target: { value: "evidence" } });
    await waitFor(() => expect(api.listCatalogItems).toHaveBeenCalledWith(expect.objectContaining({ itemKinds: ["evidence"] })));

    fireEvent.click(screen.getByRole("button", { name: /^论文Synthetic Primary Record/ }));
    await screen.findByText("Screening status");
    expect(screen.getByText("record:current")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    await waitFor(() => expect(api.listCatalogItems).toHaveBeenCalledWith(expect.objectContaining({ cursor: "next-cursor" })));
  });

  it("keeps an opened workspace usable when one summary panel fails", async () => {
    api.getCapabilities.mockRejectedValueOnce(new Error("capability unavailable"));
    render(<App />);
    fireEvent.change(screen.getByLabelText("一次性 Token"), { target: { value: "test-token-00000000000000000000000000" } });
    fireEvent.click(screen.getByRole("button", { name: "验证" }));
    await screen.findByRole("button", { name: "打开" });
    fireEvent.click(screen.getByRole("button", { name: "打开" }));

    expect(await screen.findByRole("heading", { name: "P2 Small Synthetic" })).toBeInTheDocument();
    expect(screen.getByText("请求未完成")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "文献" })).toBeEnabled();
  });

  it("opens the processing work surface even when the Catalog projection is missing", async () => {
    api.openWorkspace.mockResolvedValueOnce({
      projection_state: "missing",
      item_count: 0,
      operation: { category: null, state: "idle", job_id: null, diagnostic_code: null },
    });
    render(<App />);
    fireEvent.change(screen.getByLabelText("一次性 Token"), { target: { value: "test-token-00000000000000000000000000" } });
    fireEvent.click(screen.getByRole("button", { name: "验证" }));
    await screen.findByRole("button", { name: "打开" });
    fireEvent.click(screen.getByRole("button", { name: "打开" }));

    const processing = await screen.findByRole("button", { name: "处理" });
    expect(processing).toBeEnabled();
    expect(screen.getByRole("button", { name: "文献" })).toBeDisabled();
    fireEvent.click(processing);
    expect(await screen.findByRole("heading", { name: "文献处理" })).toBeInTheDocument();
  });

  it("opens the Agent handoff work surface without a Catalog projection", async () => {
    api.openWorkspace.mockResolvedValueOnce({
      projection_state: "missing",
      item_count: 0,
      operation: { category: null, state: "idle", job_id: null, diagnostic_code: null },
    });
    render(<App />);
    fireEvent.change(screen.getByLabelText("一次性 Token"), { target: { value: "test-token-00000000000000000000000000" } });
    fireEvent.click(screen.getByRole("button", { name: "验证" }));
    await screen.findByRole("button", { name: "打开" });
    fireEvent.click(screen.getByRole("button", { name: "打开" }));

    const agent = await screen.findByRole("button", { name: "Agent" });
    expect(agent).toBeEnabled();
    fireEvent.click(agent);
    expect(await screen.findByRole("heading", { name: "Agent 工作台" })).toBeInTheDocument();
  });

  it("opens the report-only Knowledge Query work surface without a Catalog projection", async () => {
    api.openWorkspace.mockResolvedValueOnce({
      projection_state: "missing",
      item_count: 0,
      operation: { category: null, state: "idle", job_id: null, diagnostic_code: null },
    });
    render(<App />);
    fireEvent.change(screen.getByLabelText("一次性 Token"), { target: { value: "test-token-00000000000000000000000000" } });
    fireEvent.click(screen.getByRole("button", { name: "验证" }));
    await screen.findByRole("button", { name: "打开" });
    fireEvent.click(screen.getByRole("button", { name: "打开" }));

    const query = await screen.findByRole("button", { name: "问答" });
    expect(query).toBeEnabled();
    fireEvent.click(query);
    expect(await screen.findByRole("heading", { name: "知识库问答" })).toBeInTheDocument();
    expect(screen.getByText("从文献库选择 1-4 篇论文")).toBeVisible();
  });

  it("opens the deterministic Tags work surface without an Agent route", async () => {
    render(<App />);
    fireEvent.change(screen.getByLabelText("一次性 Token"), { target: { value: "test-token-00000000000000000000000000" } });
    fireEvent.click(screen.getByRole("button", { name: "验证" }));
    await screen.findByRole("button", { name: "打开" });
    fireEvent.click(screen.getByRole("button", { name: "打开" }));

    const tags = await screen.findByRole("button", { name: "标签" });
    expect(tags).toBeEnabled();
    fireEvent.click(tags);
    const heading = await screen.findByRole("heading", { name: "标签" });
    const tagSurface = heading.closest("section");
    expect(tagSurface).not.toBeNull();
    expect(within(tagSurface as HTMLElement).queryByText(/Agent/i)).not.toBeInTheDocument();
  });

  it("opens the Exchange work surface as an independent workspace operation", async () => {
    render(<App />);
    fireEvent.change(screen.getByLabelText("一次性 Token"), { target: { value: "test-token-00000000000000000000000000" } });
    fireEvent.click(screen.getByRole("button", { name: "验证" }));
    await screen.findByRole("button", { name: "打开" });
    fireEvent.click(screen.getByRole("button", { name: "打开" }));

    const exchange = await screen.findByRole("button", { name: "交换" });
    expect(exchange).toBeEnabled();
    fireEvent.click(exchange);

    expect(await screen.findByRole("heading", { name: "知识库交换" })).toBeInTheDocument();
    expect(screen.getByText("research-kb-exchange-bundle@1.0")).toBeVisible();
    expect(screen.queryByText(/use as local fact/i)).not.toBeInTheDocument();
  });
});
