import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TagsView } from "../src/components/TagsView";

const api = vi.hoisted(() => ({
  getTag: vi.fn(),
  listTags: vi.fn(),
  listTargetTags: vi.fn(),
  promoteTag: vi.fn(),
  setTagAssignment: vi.fn(),
}));

vi.mock("../src/api", () => ({
  ...api,
  ApiError: class ApiError extends Error {
    constructor(message: string, public status = 409, public code = "RKBAPP-TAG-CONFLICT") { super(message); }
  },
}));

const hostileTag = {
  tag_id: "tag_one",
  name: "<script>alert(1)</script>",
  normalized_name: "script-alert-1-script",
  description: "Mechanism",
  aliases: ["MoA"],
  status: "active",
  revision_id: "tagrev_one",
  assignment_count: 1,
};
const assignment = {
  tag_link_id: "taglink_one",
  tag_id: "tag_one",
  target_kind: "paper",
  target_id: "paper_one",
  state: "assigned",
  revision_id: "taglinkrev_one",
  target_availability: "unavailable",
};

describe("TagsView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.listTags.mockResolvedValue({ status: "success", tags: [hostileTag], next_cursor: null, persistent_writes: 0, canonical_scientific_write: false });
    api.getTag.mockResolvedValue({ status: "success", tag: hostileTag, assignments: [assignment], persistent_writes: 0, canonical_scientific_write: false });
    api.listTargetTags.mockResolvedValue({ status: "success", target_kind: "paper", target_id: "paper_one", tags: [hostileTag], persistent_writes: 0, canonical_scientific_write: false });
    api.promoteTag.mockResolvedValue({ status: "success", result: "committed", tag: hostileTag, persistent_writes: 1, canonical_scientific_write: false });
    api.setTagAssignment.mockResolvedValue({ status: "success", result: "committed", assignment, persistent_writes: 1, canonical_scientific_write: false });
  });

  it("renders hostile content as text and revises/archive with the current head", async () => {
    const { container } = render(<TagsView />);
    fireEvent.click(await screen.findByRole("button", { name: /script.*alert/ }));
    await screen.findByText("unavailable");
    expect(container.querySelector("script")).toBeNull();

    fireEvent.change(screen.getByLabelText("标签名称"), { target: { value: "Mechanism revised" } });
    fireEvent.click(screen.getByRole("button", { name: "保存修改" }));
    await waitFor(() => expect(api.promoteTag).toHaveBeenCalledWith(expect.objectContaining({
      tag_id: "tag_one",
      name: "Mechanism revised",
      expected_revision_id: "tagrev_one",
    })));

    fireEvent.click(screen.getByRole("button", { name: "归档标签" }));
    await waitFor(() => expect(api.promoteTag).toHaveBeenCalledWith(expect.objectContaining({
      tag_id: "tag_one",
      status: "archived",
      expected_revision_id: "tagrev_one",
    })));
  });

  it("creates Tags and assigns/removes only with known assignment heads", async () => {
    render(<TagsView />);
    await screen.findByText(/alert\(1\)/);
    fireEvent.click(screen.getByRole("button", { name: "新建标签" }));
    fireEvent.change(screen.getByLabelText("标签名称"), { target: { value: "Clinical" } });
    fireEvent.click(screen.getByRole("button", { name: "创建标签" }));
    await waitFor(() => expect(api.promoteTag).toHaveBeenCalledWith(expect.objectContaining({ name: "Clinical" })));

    fireEvent.click(screen.getByRole("button", { name: /script.*alert/ }));
    await screen.findByText("unavailable");
    fireEvent.change(screen.getByLabelText("目标 ID"), { target: { value: "paper_two" } });
    fireEvent.click(screen.getByRole("button", { name: "建立关联" }));
    await waitFor(() => expect(api.setTagAssignment).toHaveBeenCalledWith({
      tag_id: "tag_one",
      target_kind: "paper",
      target_id: "paper_two",
      state: "assigned",
    }));
    fireEvent.change(screen.getByLabelText("目标 ID"), { target: { value: "paper_one" } });
    fireEvent.click(screen.getByRole("button", { name: "移除关联" }));
    await waitFor(() => expect(api.setTagAssignment).toHaveBeenCalledWith({
      tag_id: "tag_one",
      target_kind: "paper",
      target_id: "paper_one",
      state: "removed",
      expected_revision_id: "taglinkrev_one",
    }));
  });

  it("shows no-change and API conflict states without disabling the whole view", async () => {
    api.promoteTag.mockResolvedValueOnce({ status: "success", result: "no_change", tag: hostileTag, persistent_writes: 0, canonical_scientific_write: false });
    render(<TagsView />);
    fireEvent.click(await screen.findByRole("button", { name: /script.*alert/ }));
    await screen.findByLabelText("标签名称");
    fireEvent.click(screen.getByRole("button", { name: "保存修改" }));
    expect(await screen.findByText("没有变化")).toBeVisible();

    api.promoteTag.mockRejectedValueOnce(new (class extends Error { status = 409; code = "RKBAPP-TAG-DUPLICATE"; })("duplicate"));
    fireEvent.click(screen.getByRole("button", { name: "保存修改" }));
    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText(/duplicate/i)).toBeVisible();
    expect(screen.getByRole("button", { name: "保存修改" })).toBeEnabled();

    api.promoteTag.mockRejectedValueOnce(new (class extends Error { status = 409; code = "RKBAPP-TAG-CONFLICT"; })("stale expected head"));
    fireEvent.click(screen.getByRole("button", { name: "保存修改" }));
    expect(await screen.findByText(/stale expected head/i)).toBeVisible();
  });

  it("loads archived Tags explicitly and disables commands while a mutation is pending", async () => {
    let resolveMutation: ((value: unknown) => void) | undefined;
    api.promoteTag.mockImplementationOnce(() => new Promise((resolve) => { resolveMutation = resolve; }));
    render(<TagsView />);

    fireEvent.click(await screen.findByRole("button", { name: /script.*alert/ }));
    await screen.findByLabelText("标签名称");
    fireEvent.click(screen.getByRole("button", { name: "保存修改" }));
    expect(screen.getByRole("button", { name: "保存修改" })).toBeDisabled();

    resolveMutation?.({ status: "success", result: "no_change", tag: hostileTag, persistent_writes: 0, canonical_scientific_write: false });
    await screen.findByText("没有变化");
    fireEvent.click(screen.getByLabelText("包含已归档"));
    await waitFor(() => expect(api.listTags).toHaveBeenCalledWith(true, 40, null));
  });
});
