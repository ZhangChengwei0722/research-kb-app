import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DiscoveryView } from "../src/components/DiscoveryView";
import {
  acquireDiscoveryCandidate,
  getAcquiredCandidateHandoff,
  listDiscoveryCandidates,
  resolveDiscoveryCandidate,
  searchDiscovery,
  selectDiscovery,
} from "../src/api";

vi.mock("../src/api", () => ({
  acquireDiscoveryCandidate: vi.fn(),
  getAcquiredCandidateHandoff: vi.fn(),
  listDiscoveryCandidates: vi.fn(),
  resolveDiscoveryCandidate: vi.fn(),
  searchDiscovery: vi.fn(),
  selectDiscovery: vi.fn(),
}));

const candidate = {
  candidate_id: "discovery_a1111111-1111-4111-8111-111111111111",
  result_key: "doi:10.0000/discovery.ui",
  title: "Synthetic discovery UI paper",
  doi: "10.0000/discovery.ui",
  first_publication_date: "2026-08-01",
  paper_type: "article",
  full_text_status: "open_access",
  acquisition_status: "not_started",
  target_question_ids: [],
  selection_context_count: 1,
};

const report = {
  status: "success",
  interface_version: "1.0",
  provider: "europe-pmc" as const,
  provider_api_version: "synthetic-6.9",
  query: {
    date_from: "2026-07-27",
    date_until: "2026-08-03",
    title_keywords: ["targeted degradation"],
    abstract_keywords: [],
    keyword_mode: "any" as const,
    include_preprints: true,
    max_results: 15,
  },
  provider_hit_count: 1,
  scanned_result_count: 1,
  returned_result_count: 1,
  truncated: false,
  persistent_writes: 0 as const,
  results: [{
    result_key: candidate.result_key,
    title: candidate.title,
    authors: ["Alpha Researcher"],
    first_publication_date: candidate.first_publication_date,
    journal_or_server: "Synthetic Journal",
    doi: candidate.doi,
    paper_type: candidate.paper_type,
    publication_types: ["Journal Article"],
    abstract: "Targeted degradation.",
    matched_keywords: ["targeted degradation"],
    match_location: "title",
    discovery_sources: [{ provider: "europe-pmc", source: "MED", record_id: "UI-1" }],
    full_text_status: candidate.full_text_status,
    version_relationship: { status: "unresolved", related_doi: null },
    possible_duplicate_result_keys: [],
  }],
};

describe("DiscoveryView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listDiscoveryCandidates).mockResolvedValue({
      status: "success",
      candidate_count: 0,
      page_size: 100,
      candidates: [],
      next_cursor: null,
      persistent_writes: 0,
    });
    vi.mocked(searchDiscovery).mockResolvedValue(report);
    vi.mocked(selectDiscovery).mockResolvedValue(undefined);
    vi.mocked(resolveDiscoveryCandidate).mockResolvedValue({
      status: "success",
      candidate_id: candidate.candidate_id,
      resolution_status: "auto_acquisition_eligible",
      access_basis: "repository_open_access",
      license_observation: "provider_oa_policy_no_license_text",
      manual_reason: null,
      persistent_writes: 0,
    });
    vi.mocked(acquireDiscoveryCandidate).mockResolvedValue(undefined);
    vi.mocked(getAcquiredCandidateHandoff).mockResolvedValue({
      status: "success",
      candidate_id: candidate.candidate_id,
      registration: { state: "unregistered", paper_ids: [] },
      persistent_writes: 0,
    });
  });

  it("keeps search, selection, resolution and acquisition as distinct actions", async () => {
    const candidatePages = [
      { status: "success", candidate_count: 0, page_size: 100, candidates: [], next_cursor: null, persistent_writes: 0 },
      { status: "success", candidate_count: 1, page_size: 100, candidates: [candidate], next_cursor: null, persistent_writes: 0 },
      { status: "success", candidate_count: 1, page_size: 100, candidates: [{ ...candidate, acquisition_status: "acquired" }], next_cursor: null, persistent_writes: 0 },
    ];
    vi.mocked(listDiscoveryCandidates).mockImplementation(async () => candidatePages.shift()!);
    render(<DiscoveryView />);

    fireEvent.change(screen.getByLabelText("起始日期"), { target: { value: "2026-07-27" } });
    fireEvent.change(screen.getByLabelText("结束日期"), { target: { value: "2026-08-03" } });
    fireEvent.change(screen.getByLabelText("标题关键词"), { target: { value: "targeted degradation" } });
    fireEvent.click(screen.getByRole("button", { name: "搜索" }));

    expect(await screen.findByText(candidate.title)).toBeInTheDocument();
    expect(selectDiscovery).not.toHaveBeenCalled();
    expect(resolveDiscoveryCandidate).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("checkbox", { name: new RegExp(candidate.title) }));
    fireEvent.click(screen.getByRole("button", { name: /保存所选/ }));
    await screen.findByRole("button", { name: "检查 OA" });
    expect(selectDiscovery).toHaveBeenCalledWith(report, [candidate.result_key]);
    expect(resolveDiscoveryCandidate).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "检查 OA" }));
    expect(await screen.findByRole("button", { name: "下载 OA PDF" })).toBeInTheDocument();
    expect(acquireDiscoveryCandidate).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "下载 OA PDF" }));
    await waitFor(() => expect(acquireDiscoveryCandidate).toHaveBeenCalledWith(candidate.candidate_id));
    expect(await screen.findByText("来源已就绪，等待单独纳入知识库")).toBeInTheDocument();
  });
});
