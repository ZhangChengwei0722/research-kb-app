import {
  Check,
  Clipboard,
  Download,
  FileJson,
  History,
  LoaderCircle,
  Network,
  RotateCcw,
  Send,
  X,
} from "lucide-react";
import { ChangeEvent, useEffect, useMemo, useState } from "react";
import {
  ApiError,
  approveOrganizationProposal,
  createOrganizationProposal,
  getAgentPreview,
  getAgentRegistry,
  getAgentTask,
  getCatalogStatus,
  getHealth,
  getOrganizationTarget,
  getPaperOrganizationContext,
  inspectAgentHandoff,
  listCatalogItems,
  listAgentTasks,
  listOrganizationTargets,
  prepareAgentHandoff,
  rejectAgentResult,
  requestAgentRevision,
  submitAgentResult,
  type AgentHandoffResult,
  type AgentInspectResult,
  type AgentPreviewResult,
  type AgentRegistry,
  type AgentTaskProjection,
  type CatalogStatus,
  type HealthResult,
  type JsonValue,
  type OrganizationProposalCreateRequest,
  type OrganizationTargetKind,
  type OrganizationTargetSummary,
} from "../api";
import { copyHandoffToClipboard, exportHandoffPackage } from "../egress";
import { TagFacetSelect } from "./TagFacetSelect";

type Props = {
  initialPaperIds: string[];
  onCatalogStatus: (status: CatalogStatus) => void;
  onHealth: (health: HealthResult) => void;
};

const TARGETS: ReadonlyArray<{ value: OrganizationTargetKind; label: string }> = [
  { value: "direction", label: "Direction" },
  { value: "field_map_entry", label: "Field Map" },
  { value: "question", label: "Question" },
];
const SETTLE_ATTEMPTS = 80;
const SETTLE_INTERVAL_MS = 150;
const TAGGED_TARGET_PAGE_SIZE = 25;

export function ResearchOrganizationView({ initialPaperIds, onCatalogStatus, onHealth }: Props) {
  const [registry, setRegistry] = useState<AgentRegistry | null>(null);
  const [targetKind, setTargetKind] = useState<OrganizationTargetKind>("direction");
  const [targets, setTargets] = useState<OrganizationTargetSummary[]>([]);
  const [targetId, setTargetId] = useState("");
  const [targetTagId, setTargetTagId] = useState("");
  const [targetCursors, setTargetCursors] = useState<Array<string | null>>([null]);
  const [targetPageIndex, setTargetPageIndex] = useState(0);
  const [targetNextCursor, setTargetNextCursor] = useState<string | null>(null);
  const [targetHasMore, setTargetHasMore] = useState(false);
  const [targetRefreshKey, setTargetRefreshKey] = useState(0);
  const [targetDetail, setTargetDetail] = useState<OrganizationTargetSummary | null>(null);
  const [paperText, setPaperText] = useState(initialPaperIds.join("\n"));
  const [paperContext, setPaperContext] = useState<Record<string, JsonValue>[]>([]);
  const [goal, setGoal] = useState("");
  const [includeReview, setIncludeReview] = useState(false);
  const [executorId, setExecutorId] = useState<"codex_cli" | "claude_code_cli">("codex_cli");
  const [approvedClasses, setApprovedClasses] = useState<string[]>([]);
  const [tasks, setTasks] = useState<AgentTaskProjection[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState("");
  const [task, setTask] = useState<AgentTaskProjection | null>(null);
  const [inspection, setInspection] = useState<AgentInspectResult | null>(null);
  const [handoff, setHandoff] = useState<AgentHandoffResult | null>(null);
  const [resultText, setResultText] = useState("");
  const [preview, setPreview] = useState<AgentPreviewResult | null>(null);
  const [feedback, setFeedback] = useState("");
  const [loading, setLoading] = useState(true);
  const [mutating, setMutating] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const definition = useMemo(
    () => registry?.task_kinds.find((item) => item.task_kind === "organization_proposal") ?? null,
    [registry],
  );
  const paperIds = useMemo(() => parsePaperIds(paperText), [paperText]);
  const paperError = paperIds.length < 1 || paperIds.length > 25
    ? "需要 1-25 个不重复的 paper ID"
    : "";
  const candidate = preview?.candidate ?? null;
  const approvalBlocked = candidate?.approval_blocked === true;
  const canInspect = task?.status === "created" || task?.status === "leased";
  const canSubmit = task?.status === "leased" && handoff !== null;
  const canDecide = task?.status === "submitted" && candidate !== null;
  const requiredMissing = definition?.required_content_classes.some(
    (item) => !approvedClasses.includes(item),
  ) ?? true;
  const targetCursor = targetCursors[targetPageIndex] ?? null;

  useEffect(() => {
    let current = true;
    Promise.all([getAgentRegistry(), listAgentTasks(50)]).then(([registryResult, taskResult]) => {
      if (!current) return;
      const organizationTasks = taskResult.tasks.filter((item) => item.task_kind === "organization_proposal");
      setRegistry(registryResult);
      setTasks(organizationTasks);
      setSelectedTaskId(organizationTasks[0]?.task_id ?? "");
    }).catch((caught: unknown) => current && setError(errorMessage(caught)))
      .finally(() => current && setLoading(false));
    return () => { current = false; };
  }, []);

  useEffect(() => {
    if (!definition || !registry?.workspace_policy) {
      setApprovedClasses([]);
      return;
    }
    const allowed = new Set(registry.workspace_policy.allowed_content_classes);
    setApprovedClasses(definition.required_content_classes.filter((item) => allowed.has(item)).sort());
  }, [definition, registry]);

  useEffect(() => {
    let current = true;
    setTargetId("");
    setTargetDetail(null);
    const request = targetTagId
      ? listCatalogItems({
        tagId: targetTagId,
        itemKinds: [organizationCatalogKind(targetKind)],
        pageSize: TAGGED_TARGET_PAGE_SIZE,
        cursor: targetCursor,
      }).then((result) => {
        if (!current) return;
        setTargets(result.items.map((item) => catalogTarget(targetKind, item.record_id, item.title)));
        setTargetNextCursor(result.next_cursor);
        setTargetHasMore(result.has_more);
      })
      : listOrganizationTargets(targetKind).then((result) => {
        if (!current) return;
        setTargets(targetKind === "direction"
          ? result.directions ?? []
          : targetKind === "field_map_entry" ? result.field_map_entries ?? [] : result.questions ?? []);
        setTargetNextCursor(null);
        setTargetHasMore(false);
      });
    request.catch((caught: unknown) => current && setError(errorMessage(caught)));
    return () => { current = false; };
  }, [targetCursor, targetKind, targetRefreshKey, targetTagId]);

  useEffect(() => {
    if (!targetId) {
      setTargetDetail(null);
      return;
    }
    let current = true;
    getOrganizationTarget(targetKind, targetId).then((result) => {
      if (!current) return;
      setTargetDetail(result.direction ?? result.field_map_entry ?? result.question ?? null);
    }).catch((caught: unknown) => current && setError(errorMessage(caught)));
    return () => { current = false; };
  }, [targetId, targetKind]);

  useEffect(() => {
    let current = true;
    if (paperIds.length === 0 || paperIds.length > 25) {
      setPaperContext([]);
      return () => { current = false; };
    }
    Promise.all(paperIds.map((paperId) => getPaperOrganizationContext(paperId)))
      .then((contexts) => current && setPaperContext(contexts))
      .catch((caught: unknown) => current && setError(errorMessage(caught)));
    return () => { current = false; };
  }, [paperIds.join("\n")]);

  useEffect(() => {
    setInspection(null);
    setHandoff(null);
    setPreview(null);
    setResultText("");
    if (!selectedTaskId) {
      setTask(null);
      return;
    }
    let current = true;
    getAgentTask(selectedTaskId).then(async (detail) => {
      if (!current) return;
      setTask(detail.current_task);
      if (["submitted", "approved"].includes(detail.current_task.status)) {
        const nextPreview = await getAgentPreview(selectedTaskId);
        if (current) setPreview(nextPreview);
      }
    }).catch((caught: unknown) => current && setError(errorMessage(caught)));
    return () => { current = false; };
  }, [selectedTaskId]);

  function replaceTask(nextTask: AgentTaskProjection) {
    setTask(nextTask);
    setTasks((current) => {
      const visible = current.filter((item) => item.task_kind === "organization_proposal");
      const index = visible.findIndex((item) => item.task_id === nextTask.task_id);
      return index < 0 ? [nextTask, ...visible] : visible.map((item) => item.task_id === nextTask.task_id ? nextTask : item);
    });
  }

  async function settleOperation() {
    for (let attempt = 0; attempt < SETTLE_ATTEMPTS; attempt += 1) {
      const [health, catalog] = await Promise.all([getHealth(), getCatalogStatus()]);
      onHealth(health);
      onCatalogStatus(catalog);
      const active = ["running", "building"].includes(health.operation.state);
      if (!active && catalog.projection_state !== "stale") return;
      await delay(SETTLE_INTERVAL_MS);
    }
    setNotice("后台索引仍在更新");
  }

  async function runMutation<T extends { task: AgentTaskProjection }>(action: () => Promise<T>, message: string) {
    setMutating(true);
    setError("");
    setNotice("");
    try {
      const result = await action();
      replaceTask(result.task);
      await settleOperation();
      setNotice(message);
      return result;
    } catch (caught) {
      setError(errorMessage(caught));
      return null;
    } finally {
      setMutating(false);
    }
  }

  async function createTask() {
    if (!definition || paperError || !goal.trim()) return;
    const request: OrganizationProposalCreateRequest = {
      target_kind: targetKind,
      target_id: targetId || null,
      proposal_goal: goal.trim(),
      paper_ids: paperIds,
      include_review_background: includeReview,
      executor_id: executorId,
      approved_content_classes: approvedClasses,
      idempotency_key: crypto.randomUUID(),
    };
    const created = await runMutation(() => createOrganizationProposal(request), "研究组织 Task 已创建");
    if (created) setSelectedTaskId(created.task.task_id);
  }

  async function inspectPayload() {
    if (!task) return;
    setMutating(true);
    setError("");
    try {
      setInspection(await inspectAgentHandoff(task.task_id, {
        ...expected(task),
        executor_id: task.executor_id as "codex_cli" | "claude_code_cli",
      }));
      setNotice("Payload 已就绪");
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setMutating(false);
    }
  }

  async function preparePrompt() {
    if (!task || !inspection) return;
    const result = await runMutation(() => prepareAgentHandoff(task.task_id, {
      ...expected(task),
      executor_id: task.executor_id as "codex_cli" | "claude_code_cli",
    }), task.status === "leased" ? "Prompt 已恢复" : "Prompt 已生成");
    if (result && "handoff" in result) setHandoff(result as AgentHandoffResult);
  }

  async function copyPrompt() {
    if (!handoff) return;
    try {
      await copyHandoffToClipboard(handoff);
      setNotice("Prompt 已复制");
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  async function exportPrompt() {
    if (!handoff) return;
    try {
      const result = await exportHandoffPackage(handoff);
      if (result) setNotice(`Task package 已创建：${result.filename}`);
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  async function importResult() {
    if (!task) return;
    let result: { [key: string]: JsonValue };
    try {
      const parsed = JSON.parse(resultText) as unknown;
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("object required");
      result = parsed as { [key: string]: JsonValue };
    } catch {
      setError("JSON 格式无效");
      return;
    }
    const submitted = await runMutation(() => submitAgentResult(task.task_id, { ...expected(task), result }), "组织候选已暂存");
    if (submitted) setPreview(await getAgentPreview(task.task_id));
  }

  async function loadJsonFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    if (file.size > 2 * 1024 * 1024) {
      setError("JSON 文件超过 2 MB");
      return;
    }
    setResultText(await file.text());
  }

  async function revise() {
    if (!task || !feedback.trim()) return;
    const result = await runMutation(() => requestAgentRevision(task.task_id, {
      ...expected(task), feedback: feedback.trim(),
    }), "已创建修订 Task");
    if (result?.successor_task) setSelectedTaskId(result.successor_task.task_id);
  }

  async function reject() {
    if (!task) return;
    await runMutation(() => rejectAgentResult(task.task_id, {
      ...expected(task), reason_code: "user_rejected",
    }), "组织候选已拒绝");
  }

  async function approve() {
    if (!task) return;
    const result = await runMutation(() => approveOrganizationProposal(task.task_id, expected(task)), "组织 revision 已批准并提交");
    if (result) {
      setPreview(await getAgentPreview(task.task_id));
      setTargetRefreshKey((current) => current + 1);
    }
  }

  function changeTargetKind(nextKind: OrganizationTargetKind) {
    setTargetKind(nextKind);
    setTargetCursors([null]);
    setTargetPageIndex(0);
  }

  function changeTargetTag(nextTagId: string) {
    setTargetTagId(nextTagId);
    setTargetCursors([null]);
    setTargetPageIndex(0);
  }

  function nextTargetPage() {
    if (!targetNextCursor) return;
    setTargetCursors((current) => [...current.slice(0, targetPageIndex + 1), targetNextCursor]);
    setTargetPageIndex((current) => current + 1);
  }

  function previousTargetPage() {
    if (targetPageIndex === 0) return;
    setTargetPageIndex((current) => current - 1);
  }

  function toggleReview(enabled: boolean) {
    setIncludeReview(enabled);
    setApprovedClasses((current) => enabled
      ? [...new Set([...current, "review_background"])].sort()
      : current.filter((item) => item !== "review_background"));
  }

  return (
    <section className="query-view organization-view" aria-labelledby="organization-title">
      <header className="view-heading query-heading">
        <div><p className="section-kicker">RESEARCH ORGANIZATION</p><h2 id="organization-title">研究组织</h2></div>
        <span className={`operation-chip operation-${mutating ? "running" : "idle"}`}>{mutating ? "processing" : "ready"}</span>
      </header>
      {error ? <div className="error-banner" role="alert">{error}</div> : null}
      {notice ? <div className="notice-banner" role="status">{notice}</div> : null}

      <div className="query-grid organization-grid">
        <section className="query-compose-pane" aria-labelledby="organization-compose-title">
          <div className="subsection-heading"><h3 id="organization-compose-title">目标与来源</h3><Network size={17} /></div>
          <div className="organization-tabs" role="tablist" aria-label="组织类型">
            {TARGETS.map((item) => <button key={item.value} type="button" role="tab" aria-selected={targetKind === item.value} className={targetKind === item.value ? "agent-task-selected" : ""} onClick={() => changeTargetKind(item.value)}>{item.label}</button>)}
          </div>
          <TagFacetSelect id="organization-tag-filter" label="标签筛选" value={targetTagId} onChange={changeTargetTag} disabled={mutating} />
          <label htmlFor="organization-target">目标 revision</label>
          <select id="organization-target" value={targetId} onChange={(event) => setTargetId(event.target.value)} disabled={mutating}>
            <option value="">创建新目标</option>
            {targets.map((item) => {
              const id = targetIdentity(targetKind, item);
              return <option key={id} value={id}>{targetLabel(targetKind, item)}</option>;
            })}
          </select>
          {targetTagId ? (
            <div className="target-pagination" aria-label="标签筛选目标分页">
              <button className="secondary-button" type="button" onClick={previousTargetPage} disabled={mutating || targetPageIndex === 0}>上一页目标</button>
              <span>第 {targetPageIndex + 1} 页 · 最多 {TAGGED_TARGET_PAGE_SIZE} 条</span>
              <button className="secondary-button" type="button" onClick={nextTargetPage} disabled={mutating || !targetHasMore || !targetNextCursor}>下一页目标</button>
            </div>
          ) : null}
          {targetDetail ? <pre className="organization-context">{JSON.stringify(targetDetail, null, 2)}</pre> : null}

          <label htmlFor="organization-papers">Paper IDs（每行一个，1-25）</label>
          <textarea id="organization-papers" value={paperText} onChange={(event) => setPaperText(event.target.value)} spellCheck={false} disabled={mutating} />
          {paperError ? <p className="query-validation" role="status">{paperError}</p> : <p className="query-validation">已解析 {paperIds.length} 篇；{paperContext.length} 篇上下文可用</p>}

          <label htmlFor="organization-goal">Proposal goal</label>
          <textarea id="organization-goal" value={goal} onChange={(event) => setGoal(event.target.value)} maxLength={2000} disabled={mutating} />
          <label htmlFor="organization-executor">External Agent</label>
          <select id="organization-executor" value={executorId} onChange={(event) => setExecutorId(event.target.value as "codex_cli" | "claude_code_cli")} disabled={mutating}>
            {registry?.executors.map((executor) => <option key={executor.executor_id} value={executor.executor_id}>{humanize(executor.executor_id)}</option>)}
          </select>
          <label className="organization-review-toggle"><input type="checkbox" checked={includeReview} onChange={(event) => toggleReview(event.target.checked)} disabled={mutating || !definition?.optional_content_classes.includes("review_background")} /><span>加入 Review Memory 背景</span></label>
          <button className="start-button" type="button" onClick={createTask} disabled={mutating || loading || !definition || !goal.trim() || Boolean(paperError) || requiredMissing}><Network size={17} />创建组织 Task</button>

          <div className="query-task-history">
            <div className="subsection-heading"><h3>组织任务</h3><History size={16} /></div>
            {tasks.map((item) => <button key={item.task_id} type="button" className={selectedTaskId === item.task_id ? "agent-task-selected" : ""} onClick={() => setSelectedTaskId(item.task_id)}><span>{item.task_id}</span><small>Organization proposal</small><strong>{item.status}</strong></button>)}
            {!tasks.length && !loading ? <div className="compact-empty">尚无组织 Task</div> : null}
          </div>
        </section>

        <section className="query-handoff-pane" aria-labelledby="organization-handoff-title">
          <div className="subsection-heading"><div><h3 id="organization-handoff-title">Agent Handoff</h3><span>{task?.result_contract ?? "no task selected"}</span></div>{task ? <span className={`status-badge job-status-${task.status}`}>{task.status}</span> : null}</div>
          <div className="agent-command-row">
            <button type="button" onClick={inspectPayload} disabled={mutating || !canInspect}><Clipboard size={16} />预览 Payload</button>
            <button type="button" onClick={preparePrompt} disabled={mutating || !inspection || !canInspect}><Network size={16} />生成 Prompt</button>
            <button type="button" onClick={copyPrompt} disabled={!handoff}><Clipboard size={16} />复制 Prompt</button>
            <button type="button" onClick={exportPrompt} disabled={!handoff}><Download size={16} />导出 Task</button>
          </div>
          {inspection ? <pre className="organization-context">{JSON.stringify(inspection.handoff_preview.payload, null, 2)}</pre> : null}
          {handoff ? <pre className="organization-context">{JSON.stringify(handoff.handoff, null, 2)}</pre> : null}
          <label htmlFor="organization-result-json">Agent JSON</label>
          <textarea id="organization-result-json" value={resultText} onChange={(event) => setResultText(event.target.value)} placeholder="{}" spellCheck={false} disabled={mutating || task?.status === "submitted" || task?.status === "approved"} />
          <div className="agent-import-row">
            <label className="file-command" htmlFor="organization-result-file"><FileJson size={16} />JSON 文件</label>
            <input id="organization-result-file" className="visually-hidden" type="file" accept="application/json,.json" onChange={loadJsonFile} />
            <button type="button" onClick={importResult} disabled={mutating || !canSubmit || !resultText.trim()}>{mutating ? <LoaderCircle size={16} className="spin" /> : <Send size={16} />}导入结果</button>
          </div>
        </section>

        <section className="query-report-pane" aria-labelledby="organization-preview-title">
          <div className="subsection-heading"><div><h3 id="organization-preview-title">组织候选预览</h3><span>批准后写入 canonical revision</span></div></div>
          {candidate ? (
            <div className="organization-preview">
              <div className="query-report-boundary" role="note"><strong>{approvalBlocked ? "Approval blocked" : task?.status === "approved" ? "Committed" : "Ready for review"}</strong><span>{candidate.change_kind ? humanize(String(candidate.change_kind)) : targetKind}</span></div>
              <pre className="organization-context">{JSON.stringify(candidate, null, 2)}</pre>
            </div>
          ) : <div className="compact-empty">导入并通过 Core 校验后显示候选</div>}
          <label htmlFor="organization-feedback">Revision feedback</label>
          <textarea id="organization-feedback" value={feedback} onChange={(event) => setFeedback(event.target.value)} maxLength={4000} disabled={!canDecide || mutating} />
          <div className="agent-decision-row">
            <button type="button" onClick={revise} disabled={!canDecide || !feedback.trim() || mutating}><RotateCcw size={16} />请求修订</button>
            <button type="button" onClick={reject} disabled={!canDecide || mutating}><X size={16} />拒绝</button>
            <button className="approve-button" type="button" onClick={approve} disabled={!canDecide || approvalBlocked || mutating}><Check size={16} />批准 revision</button>
          </div>
        </section>
      </div>
    </section>
  );
}

function parsePaperIds(value: string): string[] {
  return [...new Set(value.split(/[\s,]+/).map((item) => item.trim()).filter(Boolean))];
}

function targetIdentity(kind: OrganizationTargetKind, target: OrganizationTargetSummary): string {
  const field = kind === "direction" ? "direction_id" : kind === "field_map_entry" ? "field_map_entry_id" : "question_id";
  return String(target[field] ?? "");
}

function targetLabel(kind: OrganizationTargetKind, target: OrganizationTargetSummary): string {
  const fallback = targetIdentity(kind, target);
  return String(target.name ?? target.title ?? target.question_text ?? fallback);
}

function organizationCatalogKind(kind: OrganizationTargetKind): string {
  return kind === "direction" ? "research_direction" : kind;
}

function catalogTarget(kind: OrganizationTargetKind, recordId: string, title: string): OrganizationTargetSummary {
  const key = kind === "direction" ? "direction_id" : kind === "field_map_entry" ? "field_map_entry_id" : "question_id";
  return { [key]: recordId, name: title };
}

function expected(task: AgentTaskProjection) {
  return { expected_state_id: task.state_id, expected_state_digest: task.state_digest };
}

function humanize(value: string): string {
  return value.replaceAll("_", " ");
}

function delay(milliseconds: number) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function errorMessage(caught: unknown): string {
  if (caught instanceof ApiError) return `${caught.code}: ${caught.message}`;
  return "研究组织任务未完成";
}
