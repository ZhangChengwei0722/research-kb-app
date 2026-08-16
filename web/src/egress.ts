import {
  copyToClipboard,
  exportAgentTaskPackage,
  selectSetupFolder,
  type AgentHandoffResult,
  type AgentTaskProjection,
  type AgentTaskPackageExportResult,
} from "./api";

export function handoffManifestText(result: AgentHandoffResult): string {
  return JSON.stringify(result.handoff, null, 2);
}

export async function copyHandoffToClipboard(result: AgentHandoffResult): Promise<void> {
  await copyToClipboard({
    action: "agent_handoff",
    task_id: result.task.task_id,
    expected_state_id: result.task.state_id,
    expected_state_digest: result.task.state_digest,
    executor_id: result.task.executor_id as "codex_cli" | "claude_code_cli",
  });
}

export async function copyKnowledgeQueryAnswer(task: AgentTaskProjection): Promise<void> {
  await copyToClipboard({
    action: "knowledge_query_answer",
    task_id: task.task_id,
    expected_state_id: task.state_id,
    expected_state_digest: task.state_digest,
  });
}

export async function copyTaskMetadata(task: AgentTaskProjection): Promise<void> {
  await copyToClipboard({
    action: "metadata_only",
    task_id: task.task_id,
    expected_state_id: task.state_id,
    expected_state_digest: task.state_digest,
    metadata_disclosure_accepted: true,
  });
}

export async function exportHandoffPackage(
  result: AgentHandoffResult,
): Promise<AgentTaskPackageExportResult | null> {
  const selected = await selectSetupFolder("task_package_destination", {
    allowNewChild: false,
    initialLocationId: "documents",
  });
  if (selected.status === "cancelled" || !selected.selection) return null;
  return exportAgentTaskPackage(result.task.task_id, {
    expected_state_id: result.task.state_id,
    expected_state_digest: result.task.state_digest,
    executor_id: result.task.executor_id as "codex_cli" | "claude_code_cli",
    selection_lease_id: selected.selection.lease_id,
  });
}
