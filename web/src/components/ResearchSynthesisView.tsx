import {
  Clipboard,
  Download,
  FileJson,
  RotateCcw,
  Send,
  ShieldCheck,
  Sparkles,
  XCircle,
} from "lucide-react";
import { ChangeEvent, useEffect, useMemo, useState } from "react";
import {
  ApiError,
  approveResearchSynthesisProposal,
  createResearchSynthesisProposal,
  getAgentPreview,
  getAgentRegistry,
  getAgentTask,
  getCatalogStatus,
  getHealth,
  getResearchSynthesisCandidate,
  getResearchSynthesisLimits,
  getResearchSynthesisQuestionContext,
  inspectAgentHandoff,
  listAgentTasks,
  listOrganizationTargets,
  listResearchSynthesisCandidates,
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
  type OrganizationTargetSummary,
  type ResearchSynthesisCandidateSummary,
  type ResearchSynthesisCandidateType,
  type ResearchSynthesisMaintenanceIntent,
  type ResearchSynthesisProposalCreateRequest,
  type ResearchSynthesisQuestionContext,
} from "../api";
import { copyHandoffToClipboard, exportHandoffPackage } from "../egress";

type Props = {
  onCatalogStatus: (status: CatalogStatus) => void;
  onHealth: (health: HealthResult) => void;
};

const CANDIDATE_TYPES: ReadonlyArray<{ value: ResearchSynthesisCandidateType; label: string }> = [
  { value: "synthesis", label: "Synthesis" },
  { value: "review_angle", label: "Review Angle" },
  { value: "insight", label: "Insight" },
  { value: "cross_view", label: "Cross-View" },
];
const SETTLE_ATTEMPTS = 80;
const SETTLE_INTERVAL_MS = 150;
const PAGE_SIZE = 50;

export function ResearchSynthesisView({ onCatalogStatus, onHealth }: Props) {
  const [registry, setRegistry] = useState<AgentRegistry | null>(null);
  const [questions, setQuestions] = useState<OrganizationTargetSummary[]>([]);
  const [questionId, setQuestionId] = useState("");
  const [questionContext, setQuestionContext] = useState<ResearchSynthesisQuestionContext | null>(null);
  const [candidateType, setCandidateType] = useState<ResearchSynthesisCandidateType>("synthesis");
  const [intent, setIntent] = useState<ResearchSynthesisMaintenanceIntent>("append");
  const [goal, setGoal] = useState("");
  const [includeReview, setIncludeReview] = useState(false);
  const [executorId, setExecutorId] = useState<"codex_cli" | "claude_code_cli">("codex_cli");
  const [approvedClasses, setApprovedClasses] = useState<string[]>([]);
  const [freshness, setFreshness] = useState<"all" | "current" | "stale">("all");
  const [candidates, setCandidates] = useState<ResearchSynthesisCandidateSummary[]>([]);
  const [targetCandidateId, setTargetCandidateId] = useState("");
  const [candidateDetail, setCandidateDetail] = useState<Record<string, JsonValue> | null>(null);
  const [candidateCursors, setCandidateCursors] = useState<Array<string | null>>([null]);
  const [candidatePageIndex, setCandidatePageIndex] = useState(0);
  const [candidateNextCursor, setCandidateNextCursor] = useState<string | null>(null);
  const [candidateRefreshKey, setCandidateRefreshKey] = useState(0);
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
    () => registry?.task_kinds.find((item) => item.task_kind === "research_synthesis_drafting") ?? null,
    [registry],
  );
  const requiredMissing = definition?.required_content_classes.some(
    (item) => !approvedClasses.includes(item),
  ) ?? true;
  const candidate = preview?.candidate ?? null;
  const approvalBlocked = candidate?.approval_blocked === true
    || candidate?.duplicate_disposition === "uncertain_near_duplicate";
  const canInspect = task?.status === "created" || task?.status === "leased";
  const canSubmit = task?.status === "leased" && handoff !== null;
  const canDecide = task?.status === "submitted" && candidate !== null;
  const contextPayload = inspection?.handoff_preview.payload ?? handoff?.handoff.payload ?? null;
  const candidateCursor = candidateCursors[candidatePageIndex] ?? null;

  useEffect(() => {
    let current = true;
    Promise.all([
      getAgentRegistry(),
      listAgentTasks(100),
      listOrganizationTargets("question", 100),
      getResearchSynthesisLimits(),
    ]).then(([registryResult, taskResult, questionResult]) => {
      if (!current) return;
      const synthesisTasks = taskResult.tasks.filter((item) => item.task_kind === "research_synthesis_drafting");
      const nextQuestions = questionResult.questions ?? [];
      setRegistry(registryResult);
      setTasks(synthesisTasks);
      setSelectedTaskId(synthesisTasks[0]?.task_id ?? "");
      setQuestions(nextQuestions);
      setQuestionId(questionIdentity(nextQuestions[0] ?? null));
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
    setCandidateCursors([null]);
    setCandidatePageIndex(0);
    setTargetCandidateId("");
    setCandidateDetail(null);
  }, [questionId, candidateType, freshness]);

  useEffect(() => {
    if (!questionId) {
      setQuestionContext(null);
      setCandidates([]);
      return;
    }
    let current = true;
    Promise.all([
      getResearchSynthesisQuestionContext(questionId),
      listResearchSynthesisCandidates({
        questionId,
        candidateType,
        freshness: freshness === "all" ? undefined : freshness,
        pageSize: PAGE_SIZE,
        cursor: candidateCursor,
      }),
    ]).then(([contextResult, candidateResult]) => {
      if (!current) return;
      setQuestionContext(contextResult);
      setCandidates(candidateResult.candidates);
      setCandidateNextCursor(candidateResult.next_cursor);
    }).catch((caught: unknown) => current && setError(errorMessage(caught)));
    return () => { current = false; };
  }, [candidateCursor, candidateRefreshKey, candidateType, freshness, questionId]);

  useEffect(() => {
    if (!targetCandidateId) {
      setCandidateDetail(null);
      return;
    }
    let current = true;
    getResearchSynthesisCandidate(targetCandidateId).then((result) => {
      if (current) setCandidateDetail(result.candidate);
    }).catch((caught: unknown) => current && setError(errorMessage(caught)));
    return () => { current = false; };
  }, [targetCandidateId]);

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
      const visible = current.filter((item) => item.task_kind === "research_synthesis_drafting");
      const index = visible.findIndex((item) => item.task_id === nextTask.task_id);
      return index < 0
        ? [nextTask, ...visible]
        : visible.map((item) => item.task_id === nextTask.task_id ? nextTask : item);
    });
  }

  async function settleOperation() {
    for (let attempt = 0; attempt < SETTLE_ATTEMPTS; attempt += 1) {
      const [health, catalog] = await Promise.all([getHealth(), getCatalogStatus()]);
      onHealth(health);
      onCatalogStatus(catalog);
      if (!["running", "building"].includes(health.operation.state) && catalog.projection_state !== "stale") return;
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
      if (message) setNotice(message);
      return result;
    } catch (caught) {
      setError(errorMessage(caught));
      return null;
    } finally {
      setMutating(false);
    }
  }

  async function createTask() {
    if (!definition || !questionId || !goal.trim() || requiredMissing) return;
    if (intent === "replace" && !targetCandidateId) return;
    const request: ResearchSynthesisProposalCreateRequest = {
      question_id: questionId,
      candidate_type: candidateType,
      maintenance_intent: intent,
      target_candidate_id: intent === "replace" ? targetCandidateId : null,
      maintenance_goal: goal.trim(),
      include_review_background: includeReview,
      executor_id: executorId,
      approved_content_classes: approvedClasses,
      idempotency_key: crypto.randomUUID(),
    };
    const created = await runMutation(
      () => createResearchSynthesisProposal(request),
      "",
    );
    if (created) {
      setInspection(null);
      setHandoff(null);
      setPreview(null);
      setResultText("");
      setSelectedTaskId(created.task.task_id);
      setNotice("Research Synthesis Task 已创建");
    }
  }

  async function inspectPayload() {
    if (!task) return;
    setMutating(true);
    setError("");
    setNotice("");
    setInspection(null);
    setHandoff(null);
    setPreview(null);
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
    let result: Record<string, JsonValue>;
    try {
      const parsed = JSON.parse(resultText) as unknown;
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("object required");
      result = parsed as Record<string, JsonValue>;
    } catch {
      setError("JSON 格式无效");
      return;
    }
    const submitted = await runMutation(
      () => submitAgentResult(task.task_id, { ...expected(task), result }),
      "Research Synthesis 候选已暂存",
    );
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
    }), "Research Synthesis 候选已拒绝");
  }

  async function approve() {
    if (!task || approvalBlocked) return;
    const result = await runMutation(
      () => approveResearchSynthesisProposal(task.task_id, expected(task)),
      "Research Synthesis 候选已批准",
    );
    if (result) {
      setPreview(await getAgentPreview(task.task_id));
      setCandidateRefreshKey((current) => current + 1);
    }
  }

  function toggleReview(enabled: boolean) {
    setIncludeReview(enabled);
    setApprovedClasses((current) => enabled
      ? [...new Set([...current, "review_background"])].sort()
      : current.filter((item) => item !== "review_background"));
  }

  function changeIntent(nextIntent: ResearchSynthesisMaintenanceIntent) {
    setIntent(nextIntent);
    if (nextIntent === "append") setTargetCandidateId("");
  }

  function changeQuestion(nextQuestionId: string) {
    setQuestionId(nextQuestionId);
    setIntent("append");
  }

  function nextCandidatePage() {
    if (!candidateNextCursor) return;
    setCandidateCursors((current) => [...current.slice(0, candidatePageIndex + 1), candidateNextCursor]);
    setCandidatePageIndex((current) => current + 1);
  }

  function previousCandidatePage() {
    if (candidatePageIndex > 0) setCandidatePageIndex((current) => current - 1);
  }

  const primarySupport = payloadList(contextPayload, "primary_support");
  const canonicalEvidence = payloadList(contextPayload, "canonical_evidence");
  const reviewBoundaries = payloadList(contextPayload, "review_queue_boundaries");
  const reviewBackground = payloadList(contextPayload, "review_background");

  return (
    <section className="query-view synthesis-workspace" aria-labelledby="research-synthesis-title">
      <header className="view-heading query-heading">
        <div><p className="section-kicker">RESEARCH SYNTHESIS</p><h2 id="research-synthesis-title">科研综合与启发</h2></div>
        <span className={`operation-chip operation-${mutating ? "running" : "idle"}`}>{mutating ? "processing" : "explicit only"}</span>
      </header>
      {error ? <div className="error-banner" role="alert">{error}</div> : null}
      {notice ? <div className="notice-banner" role="status">{notice}</div> : null}

      <div className="query-grid synthesis-grid">
        <section className="query-compose-pane synthesis-compose-pane">
          <div className="subsection-heading"><h3>Question 与维护意图</h3><Sparkles size={17} /></div>
          <label htmlFor="synthesis-question">Research Question</label>
          <select id="synthesis-question" value={questionId} onChange={(event) => changeQuestion(event.target.value)} disabled={loading || mutating}>
            {!questions.length ? <option value="">尚无可用 Question</option> : null}
            {questions.map((question) => {
              const id = questionIdentity(question);
              return <option key={id} value={id}>{questionLabel(question)}</option>;
            })}
          </select>
          {questionContext ? <div className="synthesis-question-summary"><strong>{questionContext.question.question_text}</strong><span>{questionContext.candidate_count} candidates · {questionContext.stale_candidate_count} stale</span></div> : null}

          <div className="route-segments synthesis-type-tabs" role="tablist" aria-label="Candidate type">
            {CANDIDATE_TYPES.map((item) => <button key={item.value} type="button" role="tab" aria-selected={candidateType === item.value} className={candidateType === item.value ? "segment-active" : ""} onClick={() => setCandidateType(item.value)} disabled={mutating}>{item.label}</button>)}
          </div>
          <div className="route-segments synthesis-intent-tabs" role="tablist" aria-label="Maintenance intent">
            <button type="button" role="tab" aria-selected={intent === "append"} className={intent === "append" ? "segment-active" : ""} onClick={() => changeIntent("append")} disabled={mutating}>Append</button>
            <button type="button" role="tab" aria-selected={intent === "replace"} className={intent === "replace" ? "segment-active" : ""} onClick={() => changeIntent("replace")} disabled={mutating}>Replace</button>
          </div>

          <div className="synthesis-existing-header">
            <label htmlFor="synthesis-freshness">Existing candidates</label>
            <select id="synthesis-freshness" value={freshness} onChange={(event) => setFreshness(event.target.value as typeof freshness)} disabled={mutating}>
              <option value="all">all</option><option value="current">current</option><option value="stale">stale</option>
            </select>
          </div>
          {intent === "replace" ? <>
            <label htmlFor="synthesis-target">Replace target</label>
            <select id="synthesis-target" value={targetCandidateId} onChange={(event) => setTargetCandidateId(event.target.value)} disabled={mutating}>
              <option value="">选择 current candidate</option>
              {candidates.filter((item) => item.freshness.state === "current").map((item) => <option key={item.candidate_id} value={item.candidate_id}>{item.title} · {item.candidate_id}</option>)}
            </select>
          </> : <div className="synthesis-candidate-list" aria-label="Existing candidate list">{candidates.map((item) => <button key={item.candidate_id} type="button" onClick={() => setTargetCandidateId(item.candidate_id)} className={targetCandidateId === item.candidate_id ? "candidate-selected" : ""}><strong>{item.title}</strong><span>{item.freshness.state} · {item.not_fact ? "not fact" : "unexpected state"}</span></button>)}</div>}
          <div className="target-pagination" aria-label="Research Synthesis candidate pagination">
            <button className="secondary-button" type="button" onClick={previousCandidatePage} disabled={mutating || candidatePageIndex === 0}>上一页</button>
            <span>第 {candidatePageIndex + 1} 页</span>
            <button className="secondary-button" type="button" onClick={nextCandidatePage} disabled={mutating || !candidateNextCursor}>下一页</button>
          </div>
          {candidateDetail ? <pre className="synthesis-json">{JSON.stringify(candidateDetail, null, 2)}</pre> : null}

          <label htmlFor="synthesis-goal">Maintenance goal</label>
          <textarea id="synthesis-goal" value={goal} onChange={(event) => setGoal(event.target.value)} maxLength={2000} disabled={mutating} />
          <label htmlFor="synthesis-executor">External Agent</label>
          <select id="synthesis-executor" value={executorId} onChange={(event) => setExecutorId(event.target.value as typeof executorId)} disabled={mutating}>
            {registry?.executors.map((executor) => <option key={executor.executor_id} value={executor.executor_id}>{humanize(executor.executor_id)}</option>)}
          </select>
          <label className="synthesis-review-toggle"><input type="checkbox" checked={includeReview} onChange={(event) => toggleReview(event.target.checked)} disabled={mutating || !definition?.optional_content_classes.includes("review_background")} /><span>加入 Review Memory 背景</span></label>
          <button className="start-button" type="button" onClick={createTask} disabled={mutating || loading || !definition || !questionId || !goal.trim() || requiredMissing || (intent === "replace" && !targetCandidateId)}><Sparkles size={17} />创建 Research Synthesis Task</button>
        </section>

        <section className="query-handoff-pane synthesis-handoff-pane">
          <div className="subsection-heading"><h3>External Agent Handoff</h3><Send size={17} /></div>
          <label htmlFor="synthesis-task">Task</label>
          <select id="synthesis-task" value={selectedTaskId} onChange={(event) => setSelectedTaskId(event.target.value)} disabled={mutating}>
            <option value="">选择 Task</option>
            {tasks.map((item) => <option key={item.task_id} value={item.task_id}>{item.status} · {humanize(item.candidate_type ?? candidateType)}</option>)}
          </select>
          <div className="query-command-row">
            <button type="button" onClick={inspectPayload} disabled={mutating || !canInspect}>预览 Payload</button>
            <button type="button" onClick={preparePrompt} disabled={mutating || !task || !inspection}>生成 Prompt</button>
            <button type="button" onClick={copyPrompt} disabled={!handoff}><Clipboard size={16} />复制 Prompt</button>
            <button type="button" onClick={exportPrompt} disabled={!handoff}><Download size={16} />导出 Task</button>
          </div>
          {handoff ? <pre className="synthesis-json synthesis-prompt" aria-label="Agent handoff manifest">{JSON.stringify(handoff.handoff, null, 2)}</pre> : null}

          <div className="synthesis-provenance-grid">
            <ProvenanceBlock title="Primary Card Units" values={primarySupport} />
            <ProvenanceBlock title="Canonical Evidence" values={canonicalEvidence} />
            <ProvenanceBlock title="Review queue boundaries" values={reviewBoundaries} />
            <ProvenanceBlock title="Review Memory background" values={reviewBackground} background />
          </div>

          <label htmlFor="synthesis-agent-json">Agent JSON</label>
          <textarea id="synthesis-agent-json" value={resultText} onChange={(event) => setResultText(event.target.value)} spellCheck={false} disabled={mutating || !task} />
          <label className="file-command" htmlFor="synthesis-agent-file"><FileJson size={16} />JSON 文件</label>
          <input id="synthesis-agent-file" className="visually-hidden" type="file" accept="application/json,.json" onChange={loadJsonFile} />
          <button type="button" onClick={importResult} disabled={mutating || !canSubmit || !resultText.trim()}>导入结果</button>
        </section>

        <section className="query-report-pane synthesis-preview-pane">
          <div className="subsection-heading"><h3>候选预览</h3><ShieldCheck size={17} /></div>
          <div className="synthesis-boundary-strip" aria-label="Research Synthesis status"><span>not_fact: true</span><span>review_status: ai_draft</span><span>automation_status: pending</span></div>
          {candidate ? <>
            <div className="synthesis-candidate-meta"><span>{humanize(String(candidate.candidate_type ?? "candidate"))}</span><span>{String(candidate.maintenance_intent ?? "")}</span><span>{String(candidate.duplicate_disposition ?? "")}</span></div>
            <pre className="synthesis-json">{JSON.stringify(candidate.payload ?? candidate, null, 2)}</pre>
            {approvalBlocked ? <div className="error-banner" role="alert">Uncertain near-duplicate: approval blocked</div> : null}
          </> : <div className="compact-empty">尚无暂存候选</div>}
          <label htmlFor="synthesis-feedback">Revision feedback</label>
          <textarea id="synthesis-feedback" value={feedback} onChange={(event) => setFeedback(event.target.value)} maxLength={4000} disabled={!canDecide || mutating} />
          <div className="query-command-row">
            <button className="start-button" type="button" onClick={approve} disabled={!canDecide || approvalBlocked || mutating}><ShieldCheck size={16} />批准候选</button>
            <button type="button" onClick={revise} disabled={!canDecide || !feedback.trim() || mutating}><RotateCcw size={16} />请求修订</button>
            <button className="secondary-button danger-text" type="button" onClick={reject} disabled={!canDecide || mutating}><XCircle size={16} />拒绝</button>
          </div>
        </section>
      </div>
    </section>
  );
}

function ProvenanceBlock({ title, values, background = false }: { title: string; values: JsonValue[]; background?: boolean }) {
  return <section className={`synthesis-provenance-block${background ? " provenance-background" : ""}`}><div><h4>{title}</h4>{background ? <span>仅作背景，不进入事实支持</span> : <span>{values.length} items</span>}</div>{values.length ? <pre className="synthesis-json">{JSON.stringify(values, null, 2)}</pre> : <div className="compact-empty">none</div>}</section>;
}

function payloadList(payload: Record<string, JsonValue> | null, field: string): JsonValue[] {
  if (!payload) return [];
  const value = payload[field];
  return Array.isArray(value) ? value : [];
}

function questionIdentity(question: OrganizationTargetSummary | null): string {
  return question && typeof question.question_id === "string" ? question.question_id : "";
}

function questionLabel(question: OrganizationTargetSummary): string {
  const id = questionIdentity(question);
  const text = typeof question.question_text === "string" ? question.question_text : id;
  return `${text} · ${id}`;
}

function expected(task: AgentTaskProjection) {
  return { expected_state_id: task.state_id, expected_state_digest: task.state_digest };
}

function humanize(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function delay(milliseconds: number) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function errorMessage(caught: unknown): string {
  if (caught instanceof ApiError) return `${caught.code}: ${caught.message}`;
  return caught instanceof Error ? caught.message : "Research Synthesis 操作未完成";
}
