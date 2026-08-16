import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { catalogViews } from "../src/catalogViews";
import { CatalogBrowser } from "../src/components/CatalogBrowser";

const api = vi.hoisted(() => ({
  getCatalogItem: vi.fn(),
  listCatalogItems: vi.fn(),
  listTags: vi.fn(),
}));

vi.mock("../src/api", () => ({
  ...api,
  ApiError: class ApiError extends Error {},
}));

describe("CatalogBrowser Tag facet", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.listTags.mockResolvedValue({
      status: "success",
      tags: [{ tag_id: "tag_one", name: "Mechanism", normalized_name: "mechanism", description: "", aliases: [], status: "active", revision_id: "tagrev_one" }],
      next_cursor: null,
      persistent_writes: 0,
      canonical_scientific_write: false,
    });
    api.listCatalogItems.mockResolvedValue({
      status: "success",
      query: "",
      item_kinds: ["paper"],
      page_size: 8,
      items: [{
        item_id: "catalog_paper_one",
        item_kind: "paper",
        authority_layer: "canonical",
        record_kind: "registry-paper",
        record_id: "paper_one",
        child_id: null,
        paper_id: "paper_one",
        question_id: null,
        title: "Synthetic paper",
        summary: "",
        status_labels: [],
        sort_key: "synthetic paper",
        source_record_digest: "digest",
        adapter_version: "1.0",
        tags: [{ tag_id: "tag_one", name: "<script>Mechanism</script>" }],
      }],
      next_cursor: null,
      has_more: false,
      projection_state: "current",
      source_watermark: "watermark",
    });
  });

  it("sends the selected Tag to Catalog and resets the page", async () => {
    const { container } = render(<CatalogBrowser view={catalogViews.library} projectionState="current" refreshKey={0} />);
    await screen.findByRole("option", { name: "Mechanism" });
    expect(await screen.findByText("<script>Mechanism</script>")).toBeVisible();
    expect(container.querySelector("script")).toBeNull();
    fireEvent.change(screen.getByLabelText("标签"), { target: { value: "tag_one" } });
    await waitFor(() => expect(api.listCatalogItems).toHaveBeenCalledWith(expect.objectContaining({ tagId: "tag_one", cursor: null })));
  });
});
