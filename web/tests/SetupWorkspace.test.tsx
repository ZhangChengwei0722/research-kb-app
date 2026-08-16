import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SetupWorkspace } from "../src/components/SetupWorkspace";

const api = vi.hoisted(() => ({
  selectSetupFolder: vi.fn(),
  prepareWorkspaceSetup: vi.fn(),
  commitWorkspaceSetup: vi.fn(),
  previewWorkspaceAdoption: vi.fn(),
  commitWorkspaceAdoption: vi.fn(),
  getSetupRecovery: vi.fn(),
  runSetupRecoveryAction: vi.fn(),
}));

vi.mock("../src/api", () => ({
  ...api,
  ApiError: class ApiError extends Error {},
}));

const FIRST_RUN = {
  status: "success" as const,
  interface_version: "research-kb-app-setup@1.0",
  mode: "first_run" as const,
  profile_id: "default",
  current_revision_id: null,
  recovery_available: false,
};

function selected(purpose: string, lease: string, label: string) {
  return {
    status: "success",
    interface_version: "research-kb-app-setup@1.0",
    selection: {
      lease_id: `selection_${lease.repeat(48)}`,
      purpose,
      display_label: label,
      capability_facts: { filesystem: "NTFS", accepted: true },
      expires_in_seconds: 600,
    },
  };
}

describe("managed workspace setup", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal("crypto", { randomUUID: () => "00000000-0000-4000-8000-000000000001" });
  });

  it("creates from opaque folder selections and never renders local paths", async () => {
    api.selectSetupFolder
      .mockResolvedValueOnce(selected("workspace_parent", "a", "Research Workspaces"))
      .mockResolvedValueOnce(selected("source_root", "b", "TPD Reviews"))
      .mockResolvedValueOnce(selected("local_inbox", "c", "PDF Inbox"));
    api.prepareWorkspaceSetup.mockResolvedValue({
      status: "success",
      interface_version: "research-kb-app-setup@1.0",
      proposal_token: `setup_${"d".repeat(48)}`,
      preview_digest: "e".repeat(64),
      preview: {
        workspace_label: "TPD Knowledge Base",
        workspace_name: "tpd-main",
        source_root_ids: ["source-1"],
        external_source_root_count: 1,
        local_inbox: "existing_external_reference",
        expires_at: "2026-08-07T12:15:00Z",
      },
    });
    api.commitWorkspaceSetup.mockResolvedValue({
      status: "success",
      interface_version: "research-kb-app-setup@1.0",
      workspace_id: "workspace_tpd",
      profile_revision_id: `profile-rev-${"f".repeat(32)}`,
      restart_required: false,
    });
    const onComplete = vi.fn().mockResolvedValue(undefined);
    render(<SetupWorkspace initialStatus={FIRST_RUN} onComplete={onComplete} />);

    fireEvent.change(screen.getByLabelText("工作区名称"), { target: { value: "TPD Knowledge Base" } });
    fireEvent.change(screen.getByLabelText("文件夹名称"), { target: { value: "tpd-main" } });
    fireEvent.click(screen.getAllByRole("button", { name: "选择" })[0]);
    await screen.findByText("Research Workspaces");
    fireEvent.click(screen.getByRole("button", { name: "添加文献来源" }));
    await screen.findByText("TPD Reviews");
    fireEvent.click(screen.getAllByRole("button", { name: "选择" })[1]);
    await screen.findByText("PDF Inbox");
    fireEvent.click(screen.getByRole("button", { name: "检查并预览" }));

    expect(await screen.findByRole("button", { name: "确认创建" })).toBeVisible();
    expect(JSON.stringify(api.prepareWorkspaceSetup.mock.calls)).not.toContain("C:\\");
    expect(JSON.stringify(api.prepareWorkspaceSetup.mock.calls)).not.toContain("expires_at");
    fireEvent.click(screen.getByRole("button", { name: "确认创建" }));
    await waitFor(() => expect(onComplete).toHaveBeenCalledOnce());
  });

  it("shows only closed recovery actions and completes a resumable setup", async () => {
    api.getSetupRecovery.mockResolvedValue({
      status: "success",
      interface_version: "research-kb-app-setup@1.0",
      profile_state: "current_missing",
      current_revision_id: null,
      recoverable_revision_ids: [],
      workspace_setup_operations: [{
        operation_id: "operation_00000000-0000-4000-8000-000000000000",
        workspace_label: "TPD Knowledge Base",
        state: "complete",
        actions: ["resume_workspace_setup"],
      }],
    });
    api.runSetupRecoveryAction.mockResolvedValue({
      status: "success",
      interface_version: "research-kb-app-setup@1.0",
      workspace_id: "workspace_tpd",
      profile_revision_id: `profile-rev-${"f".repeat(32)}`,
      restart_required: false,
    });
    const onComplete = vi.fn().mockResolvedValue(undefined);
    render(<SetupWorkspace initialStatus={{ ...FIRST_RUN, mode: "recovery", recovery_available: true }} onComplete={onComplete} />);

    fireEvent.click(await screen.findByRole("button", { name: "继续创建" }));
    await waitFor(() => expect(onComplete).toHaveBeenCalledOnce());
    expect(screen.queryByText(/operation_/)).not.toBeInTheDocument();
  });
});
