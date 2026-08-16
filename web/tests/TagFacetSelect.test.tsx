import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TagFacetSelect } from "../src/components/TagFacetSelect";

const api = vi.hoisted(() => ({ listTags: vi.fn() }));

vi.mock("../src/api", () => ({
  ...api,
  ApiError: class ApiError extends Error {},
}));

describe("TagFacetSelect", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.listTags.mockResolvedValue({
      status: "success",
      tags: [{ tag_id: "tag_one", name: "Mechanism", normalized_name: "mechanism", description: "", aliases: [], status: "active", revision_id: "tagrev_one" }],
      next_cursor: "tag-cursor-two",
      persistent_writes: 0,
      canonical_scientific_write: false,
    });
  });

  it("loads a bounded active Tag page and reports the selected stable ID", async () => {
    const onChange = vi.fn();
    render(<TagFacetSelect id="library-tag" value="" onChange={onChange} />);

    await screen.findByRole("option", { name: "Mechanism" });
    expect(api.listTags).toHaveBeenCalledWith(false, 50, null);
    fireEvent.change(screen.getByLabelText("标签"), { target: { value: "tag_one" } });
    expect(onChange).toHaveBeenCalledWith("tag_one");

    fireEvent.click(screen.getByRole("button", { name: "下一页标签" }));
    await waitFor(() => expect(api.listTags).toHaveBeenCalledWith(false, 50, "tag-cursor-two"));
  });
});
