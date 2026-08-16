import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../src/api";
import { ReadingWorkspace } from "../src/components/ReadingWorkspace";

vi.mock("../src/api", async () => {
  const actual = await vi.importActual<typeof import("../src/api")>("../src/api");
  return {
    ...actual,
    getReadingPaper: vi.fn(),
    compareReadingPapers: vi.fn(),
    getEvidenceTrace: vi.fn(),
  };
});

const primary = readingPaper("paper_primary", "Primary title", "primary");
const review = readingPaper("paper_review", "Review title", "review");

describe("reading workspace", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getReadingPaper).mockResolvedValue(primary);
    vi.mocked(api.compareReadingPapers).mockResolvedValue({
      status: "success",
      interface_version: "1.0",
      application_service_interface_version: "1.7",
      papers: [primary, review],
      semantic_comparison: null,
      persistent_writes: 0,
      canonical_scientific_write: false,
    });
    vi.mocked(api.getEvidenceTrace).mockResolvedValue({
      status: "success",
      interface_version: "1.0",
      application_service_interface_version: "1.7",
      evidence: {
        evidence_id: "evidence_primary",
        paper_id: "paper_primary",
        claim: "Synthetic claim",
        evidence_type: "reported_result",
        quote: "<script>quoted source</script>",
        source_page: { pdf_page: 3, printed_page: "17", section: "Results", figure_or_table: null },
        locator: "page:3:char:20-50",
        support_scope: "Synthetic scope",
        what_it_does_not_support: ["External claims"],
        review_status: "ai_checked",
        automation_status: "passed_auto_checks",
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:00:00Z",
      },
      primary_revision: { authority_mode: "revisioned_bundle", revision_id: "primaryrev_1", revision_number: 1, revision_status: "active" },
      source: { source_availability: "available", source_currentness: "current", trace_back_available: true },
      parse: { bound_parse_run_id: "event_1", materialized_parse_run_id: "event_1", binding_state: "current", materialized_page_count: 4, materialized_parser: { adapter: "synthetic-text", version: "1.0" } },
      factual_support_eligible: true,
      persistent_writes: 0,
      canonical_scientific_write: false,
    });
  });

  it("renders the seven-section Primary card and an escaped Evidence trace", async () => {
    render(<ReadingWorkspace paperIds={["paper_primary"]} onRemovePaper={() => undefined} />);

    expect(await screen.findByRole("heading", { name: "Primary title" })).toBeVisible();
    expect(screen.getByText("1. 研究背景与研究意义")).toBeVisible();
    expect(screen.getByText("7. 对于未来研究的展望")).toBeVisible();
    expect(screen.getByText("Synthetic primary statement")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "查看 Evidence evidence_primary" }));
    expect(await screen.findByText("<script>quoted source</script>", { exact: true })).toBeVisible();
    expect(document.querySelector("script")).not.toBeInTheDocument();
    expect(screen.getByText("page:3:char:20-50", { exact: true })).toBeVisible();
    expect(screen.getByRole("button", { name: "打开 Evidence PDF" })).toBeVisible();
  });

  it("preserves compare order and keeps Review Memory background-only", async () => {
    render(<ReadingWorkspace paperIds={["paper_primary", "paper_review"]} onRemovePaper={() => undefined} />);

    await waitFor(() => expect(api.compareReadingPapers).toHaveBeenCalledWith(["paper_primary", "paper_review"]));
    const columns = await screen.findAllByTestId("reading-paper-column");
    expect(within(columns[0]).getByRole("heading", { name: "Primary title" })).toBeVisible();
    expect(within(columns[1]).getByRole("heading", { name: "Review title" })).toBeVisible();
    expect(within(columns[1]).getAllByText("仅作背景").length).toBeGreaterThan(0);
    expect(within(columns[1]).getAllByText("Synthetic review unit").length).toBeGreaterThan(0);
  });

  it("renders an empty selection without issuing a read", () => {
    render(<ReadingWorkspace paperIds={[]} onRemovePaper={() => undefined} />);

    expect(screen.getByText("从文献库打开论文，或选择 2-4 篇加入比较")).toBeVisible();
    expect(api.getReadingPaper).not.toHaveBeenCalled();
    expect(api.compareReadingPapers).not.toHaveBeenCalled();
  });

  it("keeps a zero-Unit low-value Review Memory visible", async () => {
    const lowValue = structuredClone(review);
    if (!lowValue.review) throw new Error("review fixture is unavailable");
    lowValue.review.review_memory.memory_value = { status: "low_value", reason: "Redundant synthetic review." };
    for (const section of lowValue.review.review_memory.sections) section.units = [];
    vi.mocked(api.getReadingPaper).mockResolvedValueOnce(lowValue);

    render(<ReadingWorkspace paperIds={["paper_review"]} onRemovePaper={() => undefined} />);

    expect(await screen.findByRole("heading", { name: "Review title" })).toBeVisible();
    expect(screen.getByText("Redundant synthetic review.")).toBeVisible();
    expect(screen.getByText("已记录为低价值或重复综述；保留此记录以避免重复阅读。")).toBeVisible();
  });

  it("shows stale source state and a bounded read error", async () => {
    const stale = structuredClone(primary);
    stale.source = { source_availability: "missing", source_currentness: "stale_source", trace_back_available: false };
    stale.parse.binding_state = "historical_not_materialized";
    vi.mocked(api.getReadingPaper).mockResolvedValueOnce(stale);
    const { rerender } = render(<ReadingWorkspace paperIds={["paper_primary"]} onRemovePaper={() => undefined} />);

    expect(await screen.findByText("source:missing")).toBeVisible();
    expect(screen.getByText("trace:stale_source")).toBeVisible();
    expect(screen.getByText("parse:historical_not_materialized")).toBeVisible();

    vi.mocked(api.getReadingPaper).mockRejectedValueOnce(new api.ApiError("Reading unavailable", 409, "RKBAPP-READ"));
    rerender(<ReadingWorkspace paperIds={["paper_missing"]} onRemovePaper={() => undefined} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("RKBAPP-READ: Reading unavailable");
  });

  it("removes a paper from comparison through the parent callback", async () => {
    const onRemovePaper = vi.fn();
    render(<ReadingWorkspace paperIds={["paper_primary", "paper_review"]} onRemovePaper={onRemovePaper} />);

    await screen.findByRole("heading", { name: "Primary title" });
    fireEvent.click(screen.getByRole("button", { name: "移出 Review title" }));
    expect(onRemovePaper).toHaveBeenCalledWith("paper_review");
  });

  it("cancels a pending Evidence drawer request when closed", async () => {
    vi.mocked(api.getEvidenceTrace).mockReturnValueOnce(new Promise(() => undefined));
    render(<ReadingWorkspace paperIds={["paper_primary"]} onRemovePaper={() => undefined} />);

    await screen.findByRole("heading", { name: "Primary title" });
    fireEvent.click(screen.getByRole("button", { name: "查看 Evidence evidence_primary" }));
    expect(screen.getByText("正在读取 Evidence provenance")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "关闭 Evidence" }));
    expect(screen.queryByLabelText("Evidence 回源")).not.toBeInTheDocument();
  });
});

function readingPaper(paperId: string, title: string, route: "primary" | "review"): api.ReadingPaper {
  const sections = [
    "research_background_significance",
    "research_problem",
    "method_principle_advantages",
    "conclusions_applications",
    "innovation",
    "limitations",
    "future_outlook",
  ].map((section_id, index) => ({
    section_id,
    units: index === 1 ? [{
      unit_id: "unit_primary",
      section_id,
      statement: "Synthetic primary statement",
      statement_type: "reported_result",
      grounding_status: "grounded",
      evidence_ids: ["evidence_primary"],
      boundary_refs: [],
      source_page: { pdf_page: 3, printed_page: "17", section: "Results", figure_or_table: null },
      confidence: "high",
    }] : [],
  }));
  const reviewSections = [
    "review_objective_scope",
    "review_question_search_boundaries",
    "taxonomy_field_structure",
    "major_synthesis",
    "methods_metrics_guardrails",
    "gaps_frontiers",
    "primary_leads_reuse",
  ].map((section_id, index) => ({
    section_id,
    units: index === 2 ? [{
      review_unit_id: "reviewunit_1",
      section_id,
      unit_type: "field_axis",
      content: "Synthetic review unit",
      source_notes: [{ pdf_page: 2, printed_page: null, section: "Taxonomy", figure_or_table: null, note_type: "paraphrase", text: "Synthetic review unit", locator: null, reopen_priority: "low" }],
      workflow_impacts: [],
      background_only: true,
      can_enter_canonical_evidence: false,
      not_fact: true,
    }] : [],
  }));
  return {
    status: "success",
    interface_version: "1.0",
    application_service_interface_version: "1.7",
    paper: { paper_id: paperId, bibliography: { title, authors: ["Fixture Author"], year: 2026, doi: null }, screening_status: "candidate", review_status: "ai_checked", automation_status: "passed_auto_checks", created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" },
    document_route: route,
    primary: route === "primary" ? { authority_mode: "revisioned_bundle", revision_id: "primaryrev_1", revision_number: 1, revision_status: "active", paper_card: { sections }, unit_admissibility: [{ unit_id: "unit_primary", section_id: "research_problem", grounding_status: "grounded", factual_support_eligible: true, evidence_ids: ["evidence_primary"], boundary_refs: [] }] } : null,
    review: route === "review" ? { authority_mode: "revisioned_bundle", revision_id: "reviewrev_1", revision_number: 1, revision_status: "active", review_memory: { background_only: true, can_enter_canonical_evidence: false, memory_value: { status: "reusable", reason: "Synthetic" }, coverage_limits: { unread_sections: [], weakly_read_sections: [], reason: "Synthetic" }, sections: reviewSections }, factual_support_eligible: false } : null,
    source: { source_availability: "available", source_currentness: "current", trace_back_available: true },
    parse: { bound_parse_run_id: "event_1", materialized_parse_run_id: "event_1", binding_state: "current", materialized_page_count: 4, materialized_parser: { adapter: "synthetic-text", version: "1.0" } },
    adequacy: [{ requested_operation: route === "primary" ? "basic_paper_card" : "basic_review_memory", freshness: "current", capability_status: "yes" }],
    questions: [],
    persistent_writes: 0,
    canonical_scientific_write: false,
  };
}
