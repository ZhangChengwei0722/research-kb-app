import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ProcessingView, routeFieldsForPreset } from "../src/components/ProcessingView";

const api = vi.hoisted(() => ({
  approveTrustedParse: vi.fn(),
  cancelIntakeJob: vi.fn(),
  decideIntakeSourceAdequacyResolution: vi.fn(),
  getCatalogStatus: vi.fn(),
  getHealth: vi.fn(),
  getIntakeSourceAdequacyResolution: vi.fn(),
  getIntakeJob: vi.fn(),
  listInboxCandidates: vi.fn(),
  listIntakeJobs: vi.fn(),
  openIntakeSourceAdequacyReview: vi.fn(),
  prepareTrustedParse: vi.fn(),
  resumeIntakeJob: vi.fn(),
  startInboxIntake: vi.fn(),
  startUploadIntake: vi.fn(),
}));

vi.mock("../src/api", () => ({
  ...api,
  ApiError: class ApiError extends Error {},
}));

const pipeline = {
  job_id: "job_1234",
  state_id: "jobstate_1234",
  state_digest: "a".repeat(64),
  revision: 4,
  requested_route: "local_source",
  requested_depth: "semantic_gate",
  current_node: "source_adequacy",
  status: "waiting_user",
  wait_reason: "route_ambiguous",
  retry_count: 0,
  terminal_receipt: false,
  updated_at: "2026-07-31T00:00:00Z",
  can_resume: true,
  can_cancel: true,
};

const deterministicIntakeFilter = {
  requested_route: "local_source",
  requested_depth: "semantic_gate",
};

const detail = {
  status: "success",
  pipeline,
  ingress_mode: "upload",
  paper_id: "paper_1234",
  requested_operation: "basic_paper_card",
  document_route: null,
  route_reason: null,
  source_adequacy: {
    requested_operation: "basic_paper_card",
    gate_status: "allowed",
    required_capability: "basic_paper_understanding",
    freshness: "current",
    capability_status: "yes",
    wait_reason: null,
    capabilities: {
      basic_paper_understanding: {
        status: "yes",
        reasons: ["All deterministic checks for this capability passed."],
        authority_layers: ["machine"],
      },
      figure_table_evidence_extraction: {
        status: "no",
        reasons: ["图表顺序仍需检查。"],
        authority_layers: ["machine"],
      },
    },
    known_limitations: ["当前不适合图表证据提取。"],
    recommended_actions: ["需要时重新进行版面解析。"],
  },
  persistent_writes: 0,
};

const trustedDetail = {
  ...detail,
  paper_id: "paper_trusted",
  source_adequacy: null,
  pipeline: {
    ...pipeline,
    current_node: "trusted_parse_authority_primary",
    status: "waiting_user",
    wait_reason: "authority_required",
    can_resume: false,
  },
};

const trustedPreparation = {
  status: "success",
  interface_version: "1.22",
  lease_token: "opaque-lease-token",
  paper_id: "paper_trusted",
  source: {
    display_name: "C:\\private\\synthetic.pdf",
    size_bytes: 2048,
    identity_status: "current",
  },
  parser: {
    adapter: "pdfplumber-text-flow",
    version: "0.11.7",
  },
  parser_profile_id: "trusted-local-pdf-limited-v1",
  policy_version: "trusted-parse-v1",
  allowed_operation: "parse_run",
  expires_at: "2099-08-08T00:00:00Z",
  limited_trust_warning: "本地受监督解析仍需用户明确批准。",
  supervised_reparse_required: true,
  aggregate_preview_digest: "b".repeat(64),
  persistent_writes: 0,
};

const sourceReviewDetail = {
  ...detail,
  document_route: "review",
  requested_operation: "basic_review_memory",
  pipeline: {
    ...pipeline,
    status: "waiting_source",
    wait_reason: "source_incomplete",
    can_resume: false,
  },
};

const sourceReviewContext = {
  status: "success",
  application_service_interface_version: "1.23",
  resolution_registry_version: "intake-source-adequacy-resolution-v1",
  resolution_state: "review_required",
  job: {
    job_id: "job_1234",
    state_id: "jobstate_1234",
    state_digest: "a".repeat(64),
    status: "waiting_source",
    current_node: "source_adequacy",
    wait_reason: "source_incomplete",
  },
  paper_id: "paper_1234",
  basis_profile_id: "sourceadequacyprofile_1234",
  requested_operation: "basic_review_memory",
  required_capability: "basic_paper_understanding",
  document_route: "review",
  route_reason: null,
  machine_status: "uncertain",
  hard_failure: false,
  freshness: "current",
  source_availability: "available",
  known_limitations: ["Reading order requires review or a layout-aware parse."],
  recommended_actions: ["acquire_or_parse_supplement", "review_reading_order", "run_layout_aware_parse"],
  allowed_actions: ["accept_uncertainty", "remediation_required"],
  source_review_required: true,
  persistent_writes: 0,
  canonical_scientific_write: false,
};

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

describe("P3-D2 processing work surface", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal("crypto", { randomUUID: vi.fn(() => "00000000-0000-4000-8000-000000000001") });
    api.listInboxCandidates.mockResolvedValue({
      status: "success",
      candidates: [{ candidate_token: "candidate-1", name: "review.pdf", size_bytes: 2048 }],
      persistent_writes: 0,
    });
    api.listIntakeJobs.mockResolvedValue({
      status: "success",
      jobs: [pipeline],
      next_cursor: null,
      persistent_writes: 0,
    });
    api.getIntakeJob.mockResolvedValue(detail);
    api.getIntakeSourceAdequacyResolution.mockResolvedValue(sourceReviewContext);
    api.getHealth.mockResolvedValue({
      status: "success",
      process_ready: true,
      core_compatible: true,
      workspace_selected: true,
      projection_state: "current",
      operation: { category: null, state: "current", job_id: null, diagnostic_code: null },
    });
    api.getCatalogStatus.mockResolvedValue({
      projection_state: "current",
      item_count: 40,
      operation: { category: null, state: "current", job_id: null, diagnostic_code: null },
    });
    api.startUploadIntake.mockResolvedValue({
      status: "accepted",
      operation: { state: "running", job_id: "job_1234" },
    });
    api.startInboxIntake.mockResolvedValue({
      status: "accepted",
      operation: { state: "running", job_id: "job_1234" },
    });
    api.resumeIntakeJob.mockResolvedValue({
      status: "accepted",
      operation: { state: "running", job_id: "job_1234" },
    });
    api.cancelIntakeJob.mockResolvedValue({
      status: "accepted",
      operation: { state: "running", job_id: "job_1234" },
    });
    api.prepareTrustedParse.mockResolvedValue(trustedPreparation);
    api.approveTrustedParse.mockResolvedValue({
      status: "accepted",
      operation: { state: "running", job_id: "job_1234" },
    });
    api.openIntakeSourceAdequacyReview.mockResolvedValue({
      status: "success",
      job_id: "job_1234",
      basis_profile_id: "sourceadequacyprofile_1234",
      reader: { provider: "system" },
      confirmation: { confirmation_id: "confirmation-" + "c".repeat(32), expires_in_seconds: 600 },
      persistent_writes: 0,
      canonical_scientific_write: false,
    });
    api.decideIntakeSourceAdequacyResolution.mockResolvedValue({
      ...sourceReviewContext,
      resolution_state: "continued",
      decision_action: "accept_uncertainty",
      operation: { category: "intake", state: "complete", job_id: "job_1234", diagnostic_code: null },
    });
  });

  it("maps all four document-route presets to the closed Core request", () => {
    expect(routeFieldsForPreset("primary")).toEqual({
      requested_operation: "basic_paper_card",
      document_route: "primary",
      route_reason: null,
    });
    expect(routeFieldsForPreset("review")).toEqual({
      requested_operation: "basic_review_memory",
      document_route: "review",
      route_reason: null,
    });
    expect(routeFieldsForPreset("mixed")).toEqual({
      requested_operation: "basic_review_memory",
      document_route: "review",
      route_reason: "mixed_document",
    });
    expect(routeFieldsForPreset("undecided")).toEqual({
      requested_operation: "basic_paper_card",
      document_route: null,
      route_reason: null,
    });
  });

  it("lists and selects only local-source semantic-gate Jobs", async () => {
    const semanticProcessingJob = {
      ...pipeline,
      job_id: "job_semantic_processing",
      requested_route: "semantic_processing",
    };
    const sourceAdequacyJob = {
      ...pipeline,
      job_id: "job_source_adequacy",
      requested_depth: "source_adequacy",
    };
    api.listIntakeJobs.mockResolvedValue({
      status: "success",
      jobs: [semanticProcessingJob, sourceAdequacyJob, pipeline],
      next_cursor: null,
      persistent_writes: 0,
    });
    render(<ProcessingView onCatalogStatus={vi.fn()} onHealth={vi.fn()} />);

    expect(await screen.findByRole("button", { name: "job_1234" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "job_semantic_processing" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "job_source_adequacy" })).not.toBeInTheDocument();
    await waitFor(() => expect(api.getIntakeJob).toHaveBeenCalledWith("job_1234"));
    expect(api.getIntakeJob).not.toHaveBeenCalledWith("job_semantic_processing");
    expect(api.getIntakeJob).not.toHaveBeenCalledWith("job_source_adequacy");
  });

  it("uses the deterministic Job filter on initial load and manual refresh", async () => {
    render(<ProcessingView onCatalogStatus={vi.fn()} onHealth={vi.fn()} />);

    await waitFor(() => expect(api.listIntakeJobs).toHaveBeenCalledWith(20, null, deterministicIntakeFilter));
    fireEvent.click(screen.getByRole("button", { name: "刷新任务" }));
    await waitFor(() => expect(api.listIntakeJobs).toHaveBeenCalledTimes(2));
    expect(api.listIntakeJobs).toHaveBeenNthCalledWith(2, 20, null, deterministicIntakeFilter);
  });

  it("keeps unknown operational codes Chinese-first and safely escaped", async () => {
    const rawStatus = "<script>status</script>";
    const rawNode = "<script>node</script>";
    const rawReason = "<script>reason</script>";
    const unknownPipeline = {
      ...pipeline,
      status: rawStatus,
      current_node: rawNode,
      wait_reason: rawReason,
    };
    api.listIntakeJobs.mockResolvedValue({
      status: "success",
      jobs: [unknownPipeline],
      next_cursor: null,
      persistent_writes: 0,
    });
    api.getIntakeJob.mockResolvedValue({
      ...detail,
      pipeline: unknownPipeline,
      source_adequacy: {
        ...detail.source_adequacy,
        gate_status: "unexpected_gate",
        known_limitations: ["<b>unexpected limitation</b>"],
        recommended_actions: ["unexpected_action"],
      },
    });
    const { container } = render(<ProcessingView onCatalogStatus={vi.fn()} onHealth={vi.fn()} />);

    expect(await screen.findByText(`未识别状态（${rawStatus}）`)).toBeInTheDocument();
    expect(screen.getAllByText(`未识别节点（${rawNode}）`).length).toBeGreaterThan(0);
    expect(screen.getAllByText(`未识别等待原因（${rawReason}）`).length).toBeGreaterThan(0);
    expect(await screen.findByText("未识别值（unexpected_gate）")).toBeInTheDocument();
    expect(screen.getByText("未识别值（<b>unexpected limitation</b>）")).toBeInTheDocument();
    expect(screen.getByText("未识别值（unexpected_action）")).toBeInTheDocument();
    expect(container.querySelector("script")).not.toBeInTheDocument();
  });

  it("starts an upload with a browser-memory filename and selected review route", async () => {
    render(<ProcessingView onCatalogStatus={vi.fn()} onHealth={vi.fn()} />);
    await screen.findByText("job_1234");

    const file = new File(["%PDF-synthetic"], "selected-review.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText("PDF 文件"), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "综述" }));
    fireEvent.change(screen.getByLabelText("标题"), { target: { value: "Selected review" } });
    fireEvent.click(screen.getByRole("button", { name: "开始处理" }));

    await waitFor(() => expect(api.startUploadIntake).toHaveBeenCalledOnce());
    expect(api.listIntakeJobs).toHaveBeenNthCalledWith(2, 20, null, deterministicIntakeFilter);
    expect(screen.getByText("selected-review.pdf")).toBeInTheDocument();
    expect(api.startUploadIntake).toHaveBeenCalledWith(
      file,
      expect.objectContaining({
        idempotency_key: "00000000-0000-4000-8000-000000000001",
        requested_operation: "basic_review_memory",
        document_route: "review",
        route_reason: null,
        bibliography: expect.objectContaining({ title: "Selected review" }),
      }),
    );
  });

  it("selects a watched-inbox candidate and starts the mixed review route", async () => {
    render(<ProcessingView onCatalogStatus={vi.fn()} onHealth={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "收件箱" }));
    await screen.findByText("review.pdf");
    fireEvent.click(screen.getByRole("radio", { name: /review\.pdf/ }));
    fireEvent.click(screen.getByRole("button", { name: "混合型" }));
    fireEvent.click(screen.getByRole("button", { name: "开始处理" }));

    await waitFor(() => expect(api.startInboxIntake).toHaveBeenCalledOnce());
    expect(api.startInboxIntake).toHaveBeenCalledWith(
      expect.objectContaining({
        candidate_token: "candidate-1",
        requested_operation: "basic_review_memory",
        document_route: "review",
        route_reason: "mixed_document",
      }),
    );
  });

  it("recovers Job detail, renders use capabilities, and submits CAS controls", async () => {
    render(<ProcessingView onCatalogStatus={vi.fn()} onHealth={vi.fn()} />);

    await screen.findByText("用途能力");
    expect(screen.getAllByText("等待用户").length).toBeGreaterThan(0);
    expect(screen.getAllByText("来源充分性").length).toBeGreaterThan(0);
    expect(screen.getAllByText("文献路线待确认").length).toBeGreaterThan(0);
    expect(screen.getByText("任务详情")).toBeInTheDocument();
    expect(screen.getByText("当前节点")).toBeInTheDocument();
    expect(screen.getByText("来源方式")).toBeInTheDocument();
    expect(screen.getByText("上传")).toBeInTheDocument();
    expect(screen.getByText("论文 ID")).toBeInTheDocument();
    expect(screen.getByText(/基础论文理解卡/)).toBeInTheDocument();
    expect(screen.getByText("基础论文理解")).toBeInTheDocument();
    expect(screen.getByText(/当前有效/)).toBeInTheDocument();
    expect(screen.getByText("不适合")).toBeInTheDocument();
    expect(screen.getByText("该能力的确定性检查均已通过。")).toBeInTheDocument();
    expect(screen.queryByText("All deterministic checks for this capability passed.")).not.toBeInTheDocument();
    expect(screen.getByText("当前不适合图表证据提取。")).toBeInTheDocument();
    expect(screen.getByText("需要时重新进行版面解析。")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "原始研究" }));
    fireEvent.click(screen.getByRole("button", { name: "继续处理" }));
    await waitFor(() => expect(api.resumeIntakeJob).toHaveBeenCalledOnce());
    expect(api.resumeIntakeJob).toHaveBeenCalledWith(
      "job_1234",
      expect.objectContaining({
        expected_state_id: "jobstate_1234",
        expected_state_digest: "a".repeat(64),
        document_route: "primary",
      }),
    );

    await waitFor(() => expect(screen.getByRole("button", { name: "取消任务" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "取消任务" }));
    await waitFor(() => expect(api.cancelIntakeJob).toHaveBeenCalledOnce());
    expect(api.cancelIntakeJob).toHaveBeenCalledWith("job_1234", {
      expected_state_id: "jobstate_1234",
      expected_state_digest: "a".repeat(64),
    });
  });

  it("opens the bound source before accepting an uncertain basic-use profile", async () => {
    api.getIntakeJob.mockResolvedValue(sourceReviewDetail);
    const { container } = render(<ProcessingView onCatalogStatus={vi.fn()} onHealth={vi.fn()} />);

    expect(await screen.findByRole("heading", { name: "来源充分性处理" })).toBeInTheDocument();
    await waitFor(() => expect(api.getIntakeSourceAdequacyResolution).toHaveBeenCalledWith("job_1234"));
    expect(screen.getByText(/基础综述阅读记忆/)).toBeInTheDocument();
    expect(screen.getAllByText("基础论文理解").length).toBeGreaterThan(0);
    expect(screen.getByText("需要确认")).toBeInTheDocument();
    expect(screen.getByText("阅读顺序需要复核或进行版面感知解析。")).toBeInTheDocument();
    expect(screen.getByText("获取或解析补充材料")).toBeInTheDocument();
    expect(screen.getByText("复核阅读顺序")).toBeInTheDocument();
    expect(screen.getByText("进行版面感知解析")).toBeInTheDocument();
    expect(screen.queryByText("reading order uncertain")).not.toBeInTheDocument();
    expect(screen.queryByText("review source")).not.toBeInTheDocument();

    const accept = screen.getByRole("button", { name: "接受当前限制" });
    expect(accept).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "打开原文" }));
    await waitFor(() => expect(api.openIntakeSourceAdequacyReview).toHaveBeenCalledWith("job_1234", {
      expected_state_id: "jobstate_1234",
      expected_state_digest: "a".repeat(64),
    }));
    await waitFor(() => expect(accept).toBeEnabled());

    const completedPipeline = {
      ...sourceReviewDetail.pipeline,
      status: "completed",
      current_node: "review_semantic_gate",
      wait_reason: null,
    };
    api.listIntakeJobs.mockResolvedValue({
      status: "success",
      jobs: [completedPipeline],
      next_cursor: null,
      persistent_writes: 0,
    });
    api.getIntakeJob.mockResolvedValue({ ...sourceReviewDetail, pipeline: completedPipeline });
    fireEvent.click(accept);
    await waitFor(() => expect(api.decideIntakeSourceAdequacyResolution).toHaveBeenCalledWith(
      "job_1234",
      {
        expected_state_id: "jobstate_1234",
        expected_state_digest: "a".repeat(64),
        action: "accept_uncertainty",
        confirmation_id: "confirmation-" + "c".repeat(32),
      },
    ));
    await waitFor(() => expect(
      container.querySelectorAll(".job-table .job-status-completed"),
    ).toHaveLength(1));
  });

  it("keeps remediation independent from source-open confirmation", async () => {
    api.getIntakeJob.mockResolvedValue(sourceReviewDetail);
    api.getIntakeSourceAdequacyResolution.mockResolvedValueOnce(sourceReviewContext);
    render(<ProcessingView onCatalogStatus={vi.fn()} onHealth={vi.fn()} />);

    const remediate = await screen.findByRole("button", { name: "需要重新处理" });
    fireEvent.click(remediate);
    await waitFor(() => expect(api.decideIntakeSourceAdequacyResolution).toHaveBeenCalledWith(
      "job_1234",
      {
        expected_state_id: "jobstate_1234",
        expected_state_digest: "a".repeat(64),
        action: "remediation_required",
      },
    ));
  });

  it("hides acceptance for stale Source Adequacy contexts", async () => {
    api.getIntakeJob.mockResolvedValue(sourceReviewDetail);
    api.getIntakeSourceAdequacyResolution.mockResolvedValue({
      ...sourceReviewContext,
      resolution_state: "stale",
      allowed_actions: [],
      source_review_required: false,
    });
    render(<ProcessingView onCatalogStatus={vi.fn()} onHealth={vi.fn()} />);

    expect(await screen.findByText("已过期")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "接受当前限制" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "打开原文" })).not.toBeInTheDocument();
  });

  it("continues an accepted successor without opening the source a second time", async () => {
    api.getIntakeJob.mockResolvedValue(sourceReviewDetail);
    api.getIntakeSourceAdequacyResolution.mockResolvedValue({
      ...sourceReviewContext,
      resolution_state: "continuation_in_progress",
      allowed_actions: [],
      source_review_required: false,
      decision_action: "accept_uncertainty",
      successor_profile_id: "sourceadequacyprofile_successor",
    });
    render(<ProcessingView onCatalogStatus={vi.fn()} onHealth={vi.fn()} />);

    const continueButton = await screen.findByRole("button", { name: "继续已接受处理" });
    expect(screen.queryByRole("button", { name: "打开原文" })).not.toBeInTheDocument();
    fireEvent.click(continueButton);

    await waitFor(() => expect(api.decideIntakeSourceAdequacyResolution).toHaveBeenCalledWith(
      "job_1234",
      {
        expected_state_id: "jobstate_1234",
        expected_state_digest: "a".repeat(64),
        action: "accept_uncertainty",
      },
    ));
  });

  it("prepares and approves trusted Parse with redacted public facts and opaque payloads", async () => {
    api.listIntakeJobs.mockResolvedValue({
      status: "success",
      jobs: [trustedDetail.pipeline],
      next_cursor: null,
      persistent_writes: 0,
    });
    api.getIntakeJob.mockResolvedValue(trustedDetail);
    render(<ProcessingView onCatalogStatus={vi.fn()} onHealth={vi.fn()} />);

    await screen.findByRole("heading", { name: "受监督解析批准" });
    expect(screen.getByText("受监督解析")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "继续处理" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "批准并解析" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "准备解析" }));

    await waitFor(() => expect(api.prepareTrustedParse).toHaveBeenCalledWith("job_1234", {
      expected_state_id: "jobstate_1234",
      expected_state_digest: "a".repeat(64),
    }));
    expect(await screen.findByText("synthetic.pdf")).toBeInTheDocument();
    expect(screen.getByText("来源身份")).toBeInTheDocument();
    expect(screen.getByText("当前有效")).toBeInTheDocument();
    expect(screen.getByText("解析器")).toBeInTheDocument();
    expect(screen.getByText("解析配置 ID")).toBeInTheDocument();
    expect(screen.getByText("策略版本")).toBeInTheDocument();
    expect(screen.getByText("允许操作")).toBeInTheDocument();
    expect(screen.getByText("有效期至")).toBeInTheDocument();
    expect(screen.getByText("需要受监督重新解析")).toBeInTheDocument();
    expect(screen.getByText("是")).toBeInTheDocument();
    expect(screen.getByText("有限信任警告")).toBeInTheDocument();
    expect(screen.queryByText("C:\\private\\synthetic.pdf")).not.toBeInTheDocument();
    expect(screen.getByText("pdfplumber-text-flow")).toBeInTheDocument();
    expect(screen.getByText("trusted-local-pdf-limited-v1")).toBeInTheDocument();
    expect(screen.queryByText("opaque-lease-token")).not.toBeInTheDocument();
    expect(screen.queryByText("b".repeat(64))).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "批准并解析" }));
    await waitFor(() => expect(api.approveTrustedParse).toHaveBeenCalledWith("job_1234", {
      lease_token: "opaque-lease-token",
      aggregate_preview_digest: "b".repeat(64),
    }));
    expect(screen.getByText("解析请求已接收")).toBeInTheDocument();
  });

  it("invalidates stale trusted Parse preparation and requires a new preview", async () => {
    api.listIntakeJobs.mockResolvedValue({
      status: "success",
      jobs: [trustedDetail.pipeline],
      next_cursor: null,
      persistent_writes: 0,
    });
    api.getIntakeJob.mockResolvedValue(trustedDetail);
    api.approveTrustedParse.mockRejectedValue(new Error("stale lease"));
    render(<ProcessingView onCatalogStatus={vi.fn()} onHealth={vi.fn()} />);

    await screen.findByRole("heading", { name: "受监督解析批准" });
    fireEvent.click(screen.getByRole("button", { name: "准备解析" }));
    await screen.findByText("synthetic.pdf");
    fireEvent.click(screen.getByRole("button", { name: "批准并解析" }));

    expect(await screen.findByText("解析准备已失效，请重新准备解析")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "批准并解析" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "准备解析" }));
    await waitFor(() => expect(api.prepareTrustedParse).toHaveBeenCalledTimes(2));
  });

  it("keeps active cancellation available and clears its pending state after settlement", async () => {
    api.listIntakeJobs.mockResolvedValue({
      status: "success",
      jobs: [trustedDetail.pipeline],
      next_cursor: null,
      persistent_writes: 0,
    });
    api.getIntakeJob.mockResolvedValue(trustedDetail);
    render(<ProcessingView onCatalogStatus={vi.fn()} onHealth={vi.fn()} />);

    const cancelButton = await screen.findByRole("button", { name: "取消当前解析" });
    expect(cancelButton).toBeEnabled();
    fireEvent.click(cancelButton);
    await waitFor(() => expect(api.cancelIntakeJob).toHaveBeenCalledOnce());
    await waitFor(() => expect(cancelButton).toBeEnabled());
    expect(screen.getByText("任务状态已更新")).toBeInTheDocument();
  });

  it("reports a late trusted Parse cancellation without claiming it took effect", async () => {
    api.listIntakeJobs.mockResolvedValue({
      status: "success",
      jobs: [trustedDetail.pipeline],
      next_cursor: null,
      persistent_writes: 0,
    });
    api.getIntakeJob.mockResolvedValue(trustedDetail);
    api.cancelIntakeJob.mockResolvedValue({
      status: "accepted",
      cancel_outcome: "too_late",
      operation: { state: "running", job_id: "job_1234" },
    });
    render(<ProcessingView onCatalogStatus={vi.fn()} onHealth={vi.fn()} />);

    fireEvent.click(await screen.findByRole("button", { name: "取消当前解析" }));

    expect(await screen.findByText("解析已进入提交阶段，取消未生效")).toBeInTheDocument();
    expect(screen.queryByText("取消请求已接收")).not.toBeInTheDocument();
  });

  it("fails closed when the fresh intake-job baseline cannot be read", async () => {
    api.listIntakeJobs
      .mockResolvedValueOnce({
        status: "success",
        jobs: [pipeline],
        next_cursor: null,
        persistent_writes: 0,
      })
      .mockRejectedValueOnce(new Error("baseline unavailable"));
    render(<ProcessingView onCatalogStatus={vi.fn()} onHealth={vi.fn()} />);
    await screen.findByText("job_1234");

    const file = new File(["%PDF-synthetic"], "baseline-failure.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText("PDF 文件"), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "开始处理" }));

    await waitFor(() => expect(api.listIntakeJobs).toHaveBeenCalledTimes(2));
    expect(api.startUploadIntake).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("请求未完成");
  });

  it("does not let a delayed initial job read redirect selection after intake starts", async () => {
    const oldJob = { ...pipeline, job_id: "job_old", state_id: "jobstate_old" };
    const newJob = { ...trustedDetail.pipeline, job_id: "job_new", state_id: "jobstate_new" };
    const newJobDetail = { ...trustedDetail, pipeline: newJob };
    const initialJobs = deferred<{
      status: string;
      jobs: Array<typeof oldJob>;
      next_cursor: null;
      persistent_writes: number;
    }>();
    api.listIntakeJobs
      .mockImplementationOnce(() => initialJobs.promise)
      .mockResolvedValueOnce({
        status: "success",
        jobs: [oldJob],
        next_cursor: null,
        persistent_writes: 0,
      })
      .mockResolvedValueOnce({
        status: "success",
        jobs: [oldJob, newJob],
        next_cursor: null,
        persistent_writes: 0,
      });
    api.startUploadIntake.mockResolvedValue({
      status: "accepted",
      operation: { state: "running", job_id: null },
    });
    api.getHealth.mockResolvedValue({
      status: "success",
      process_ready: true,
      core_compatible: true,
      workspace_selected: true,
      projection_state: "current",
      operation: { category: "intake", state: "complete", job_id: null, diagnostic_code: null },
    });
    api.getIntakeJob.mockImplementation((jobId: string) => (
      jobId === "job_new" ? Promise.resolve(newJobDetail) : Promise.resolve({ ...detail, pipeline: oldJob })
    ));

    render(<ProcessingView onCatalogStatus={vi.fn()} onHealth={vi.fn()} />);
    const file = new File(["%PDF-synthetic"], "delayed-initial.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText("PDF 文件"), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "开始处理" }));

    await waitFor(() => expect(api.startUploadIntake).toHaveBeenCalledOnce());
    await waitFor(() => expect(api.getIntakeJob).toHaveBeenCalledWith("job_new"));
    initialJobs.resolve({
      status: "success",
      jobs: [oldJob],
      next_cursor: null,
      persistent_writes: 0,
    });

    await waitFor(() => expect(screen.getByRole("button", { name: "job_new" }).closest("tr"))
      .toHaveClass("job-row-selected"));
    expect(screen.getByRole("button", { name: "job_old" }).closest("tr"))
      .not.toHaveClass("job-row-selected");
  });

  it("prefers the health operation Job over stale pre-start job inference", async () => {
    const oldJob = { ...pipeline, job_id: "job_old", state_id: "jobstate_old" };
    const trustedJob = { ...trustedDetail.pipeline, job_id: "job_new", state_id: "jobstate_new" };
    const trustedJobDetail = { ...trustedDetail, pipeline: trustedJob };
    api.listIntakeJobs
      .mockResolvedValueOnce({ status: "success", jobs: [], next_cursor: null, persistent_writes: 0 })
      .mockResolvedValueOnce({
        status: "success",
        jobs: [oldJob],
        next_cursor: null,
        persistent_writes: 0,
      })
      .mockResolvedValueOnce({
        status: "success",
        jobs: [oldJob, trustedJob],
        next_cursor: null,
        persistent_writes: 0,
      });
    api.getHealth.mockResolvedValue({
      status: "success",
      process_ready: true,
      core_compatible: true,
      workspace_selected: true,
      projection_state: "current",
      operation: { category: "intake", state: "complete", job_id: "job_new", diagnostic_code: null },
    });
    api.startUploadIntake.mockResolvedValue({
      status: "accepted",
      operation: { state: "running", job_id: null },
    });
    api.getIntakeJob.mockImplementation((jobId: string) => (
      jobId === "job_new" ? Promise.resolve(trustedJobDetail) : Promise.resolve(detail)
    ));

    render(<ProcessingView onCatalogStatus={vi.fn()} onHealth={vi.fn()} />);
    await waitFor(() => expect(api.listIntakeJobs).toHaveBeenCalledOnce());

    const file = new File(["%PDF-synthetic"], "trusted.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText("PDF 文件"), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "开始处理" }));

    await waitFor(() => expect(api.startUploadIntake).toHaveBeenCalledOnce());
    expect(await screen.findByRole("heading", { name: "受监督解析批准" })).toBeInTheDocument();
    expect(api.getIntakeJob).toHaveBeenCalledWith("job_new");
    expect(api.getIntakeJob).not.toHaveBeenCalledWith("job_old");
  });

  it("stops bounded polling when an operation remains active", async () => {
    render(<ProcessingView onCatalogStatus={vi.fn()} onHealth={vi.fn()} />);
    await screen.findByText("job_1234");
    api.getHealth.mockResolvedValue({
      status: "success",
      process_ready: true,
      core_compatible: true,
      workspace_selected: true,
      projection_state: "stale",
      operation: { category: "intake", state: "running", job_id: "job_1234", diagnostic_code: null },
    });
    api.getCatalogStatus.mockResolvedValue({
      projection_state: "stale",
      item_count: 40,
      operation: { category: "intake", state: "running", job_id: "job_1234", diagnostic_code: null },
    });
    vi.useFakeTimers();
    const file = new File(["%PDF-synthetic"], "bounded.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText("PDF 文件"), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "开始处理" }));

    await act(async () => {
      await Promise.resolve();
    });
    await act(async () => {
      await vi.runAllTimersAsync();
    });

    expect(api.getHealth).toHaveBeenCalledTimes(120);
    expect(api.listIntakeJobs).toHaveBeenCalledTimes(2);
    expect(screen.getByText("自动刷新已暂停，请手动刷新任务")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始处理" })).toBeEnabled();
  });
});
