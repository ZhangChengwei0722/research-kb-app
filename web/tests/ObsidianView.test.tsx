import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ObsidianView } from "../src/components/ObsidianView";


const api = vi.hoisted(() => ({
  getObsidianStatus: vi.fn(),
  getObsidianTargets: vi.fn(),
  previewObsidianRender: vi.fn(),
  applyObsidianRender: vi.fn(),
  previewObsidianSync: vi.fn(),
  applyObsidianSync: vi.fn(),
}));

vi.mock("../src/api", () => ({
  ...api,
  ApiError: class ApiError extends Error {
    code = "RKBAPP-TEST";
  },
}));

const status = {
  status: "success",
  projection_state: "ready",
  integrity_state: "intact",
  generation_id: `gen-${"a".repeat(64)}`,
  optional_tables: ["library_summary"],
  file_count: 2,
  current_count: 2,
  stale_count: 0,
  edited_paths: [],
  edited_paths_truncated: false,
  entries: [
    { logical_path: "Home.md", view_kind: "home", view_id: "home", freshness: "current", freshness_reasons: [], rendered_at: "2026-08-04T08:00:00Z" },
    { logical_path: "Papers/_index.md", view_kind: "paper_index", view_id: "paper-index", freshness: "current", freshness_reasons: [], rendered_at: "2026-08-04T08:00:00Z" },
  ],
  next_cursor: null,
  persistent_writes: 0,
  canonical_scientific_write: false,
} as const;

const renderPreview = {
  status: "success",
  projection_state: "ready",
  integrity_state: "intact",
  generation_id: status.generation_id,
  optional_tables: ["library_summary"],
  proposed_file_count: 3,
  changed_file_count: 1,
  removed_file_count: 0,
  changed_paths: ["Home.md"],
  changed_paths_truncated: false,
  removed_paths: [],
  removed_paths_truncated: false,
  edited_paths: [],
  edited_paths_truncated: false,
  preview_token: "render-preview-token-0000000000000000",
  preview_ttl_seconds: 300,
  persistent_writes: 0,
  canonical_scientific_write: false,
} as const;

const syncPreview = {
  status: "success",
  target_id: "synthetic-vault",
  target_label: "Synthetic Vault",
  source_generation_id: status.generation_id,
  source_file_count: 2,
  source_byte_count: 64,
  destination_state: "missing",
  create_count: 2,
  update_count: 0,
  no_change_count: 0,
  remove_count: 0,
  edited_count: 0,
  missing_count: 0,
  unknown_count: 0,
  collision_count: 0,
  changed_paths: ["Home.md", "Papers/_index.md"],
  changed_paths_truncated: false,
  conflict_paths: [],
  conflict_paths_truncated: false,
  preview_token: "sync-preview-token-000000000000000000",
  preview_ttl_seconds: 300,
  persistent_writes: 0,
  canonical_scientific_write: false,
} as const;

describe("P9-B Obsidian work surface", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getObsidianStatus.mockResolvedValue(status);
    api.getObsidianTargets.mockResolvedValue({
      status: "success",
      targets: [
        { target_id: "synthetic-vault", label: "Synthetic Vault" },
        { target_id: "second-vault", label: "Second Vault" },
      ],
      preview_ttl_seconds: 300,
      persistent_writes: 0,
      canonical_scientific_write: false,
    });
    api.previewObsidianRender.mockResolvedValue(renderPreview);
    api.applyObsidianRender.mockResolvedValue({
      status: "success",
      result: "committed",
      generation_id: status.generation_id,
      file_count: 3,
      changed_file_count: 1,
      removed_file_count: 0,
      persistent_writes: 1,
      canonical_scientific_write: false,
    });
    api.previewObsidianSync.mockResolvedValue(syncPreview);
    api.applyObsidianSync.mockResolvedValue({
      status: "success",
      result: "committed",
      target_id: "synthetic-vault",
      source_generation_id: status.generation_id,
      file_count: 2,
      byte_count: 64,
      continuation: "sync",
      personal_copy: null,
      persistent_writes: 1,
      canonical_scientific_write: false,
    });
  });

  it("loads current source and configured targets without mutation", async () => {
    render(<ObsidianView />);

    expect(await screen.findByRole("heading", { name: "Obsidian 视图" })).toBeVisible();
    expect(await screen.findByText("Home.md")).toBeVisible();
    expect(screen.getByRole("option", { name: "Synthetic Vault" })).toBeVisible();
    expect(api.previewObsidianRender).not.toHaveBeenCalled();
    expect(api.previewObsidianSync).not.toHaveBeenCalled();
    expect(api.applyObsidianRender).not.toHaveBeenCalled();
    expect(api.applyObsidianSync).not.toHaveBeenCalled();
  });

  it("previews and applies render through an opaque token", async () => {
    render(<ObsidianView />);
    await screen.findByText("Home.md");
    fireEvent.click(screen.getByLabelText("问题覆盖表"));
    fireEvent.click(screen.getByRole("button", { name: "预览生成" }));

    expect(await screen.findByText("待更新 1")).toBeVisible();
    expect(api.previewObsidianRender).toHaveBeenCalledWith(["library_summary", "question_coverage"]);
    fireEvent.click(screen.getByRole("button", { name: "生成视图" }));

    await waitFor(() => expect(api.applyObsidianRender).toHaveBeenCalledWith({
      preview_token: renderPreview.preview_token,
      optional_tables: ["library_summary", "question_coverage"],
      continuation: "render",
    }));
    expect(await screen.findByText("视图已生成")).toBeVisible();
  });

  it("clears a target preview when the target selection changes", async () => {
    render(<ObsidianView />);
    await screen.findByText("Home.md");
    fireEvent.click(screen.getByRole("button", { name: "预览同步" }));
    expect(await screen.findByText("新建 2")).toBeVisible();

    fireEvent.change(screen.getByLabelText("同步目标"), { target: { value: "second-vault" } });
    expect(screen.queryByRole("button", { name: "同步到 Obsidian" })).not.toBeInTheDocument();
  });

  it("requires an explicit edited-file continuation", async () => {
    api.previewObsidianSync.mockResolvedValueOnce({
      ...syncPreview,
      destination_state: "edited",
      create_count: 0,
      edited_count: 1,
      unknown_count: 1,
      conflict_paths: ["Home.md", "Personal.md"],
    });
    render(<ObsidianView />);
    await screen.findByText("Home.md");
    fireEvent.click(screen.getByRole("button", { name: "预览同步" }));

    expect(await screen.findByRole("button", { name: "放弃受管修改并同步" })).toBeVisible();
    expect(screen.getByRole("button", { name: "导出个人副本后同步" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "同步到 Obsidian" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "导出个人副本后同步" }));

    await waitFor(() => expect(api.applyObsidianSync).toHaveBeenCalledWith({
      target_id: "synthetic-vault",
      preview_token: syncPreview.preview_token,
      continuation: "export_personal_copy_then_sync",
    }));
  });

  it("blocks an unowned target collision", async () => {
    api.previewObsidianSync.mockResolvedValueOnce({
      ...syncPreview,
      destination_state: "collision",
      create_count: 0,
      collision_count: 1,
      unknown_count: 1,
      conflict_paths: ["Unowned.md"],
    });
    render(<ObsidianView />);
    await screen.findByText("Home.md");
    fireEvent.click(screen.getByRole("button", { name: "预览同步" }));

    expect(await screen.findByText("目标包含未受管内容，不能接管")).toBeVisible();
    expect(screen.queryByRole("button", { name: "同步到 Obsidian" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "放弃受管修改并同步" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "导出个人副本后同步" })).not.toBeInTheDocument();
  });
});
