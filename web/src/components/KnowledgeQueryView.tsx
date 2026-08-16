import {
  Check,
  Clipboard,
  Copy,
  Download,
  FileJson,
  History,
  LoaderCircle,
  MessageSquareText,
  RotateCcw,
  Send,
  ShieldCheck,
  X,
} from "lucide-react";
import { ChangeEvent, useEffect, useMemo, useState } from "react";
import {
  ApiError,
  acceptKnowledgeQueryReport,
  createKnowledgeQuery,
  getAgentPreview,
  getAgentRegistry,
  getAgentTask,
  getCatalogStatus,
  getHealth,
  getReadingPaper,
  inspectAgentHandoff,
  listAgentTasks,
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
  type KnowledgeQueryCreateRequest,
  type KnowledgeQueryType,
} from "../api";
import {
  copyKnowledgeQueryAnswer,
  copyHandoffToClipboard,
  exportHandoffPackage,
  handoffManifestText,
} from "../egress";

type KnowledgeQueryViewProps = {
  paperIds: string[];
  onRemovePaper?: (paperId: string) => void;
  onCatalogStatus: (status: CatalogStatus) => void;
  onHealth: (health: HealthResult) => void;
};

type QueryAnswerBlock = {
  block_role: string;
  text: string;
  support_refs: Array<{ paper_id: string; card_unit_id: string; evidence_ids: string[] }>;
  background_refs: Array<{ paper_id: string; review_memory_id: string; review_unit_id: string }>;
  background_only: boolean;
};

type QueryCandidate = {
  answer_blocks: QueryAnswerBlock[];
  unresolved_items: string[];
  retention_class: string;
  persistence_status: string;
  canonical_scientific_write: boolean;
};

const SETTLE_ATTEMPTS = 80;
const SETTLE_INTERVAL_MS = 150;

const QUERY_OPTIONS: ReadonlyArray<{ value: KnowledgeQueryType; label: string }> = [
  { value: "single_paper_explanation", label: "概述单篇论文" },
  { value: "seven_section_overview", label: "七段论" },
  { value: "methods", label: "研究方法" },
  { value: "selected_paper_comparison", label: "跨论文比较" },
  { value: "trend_problem_discussion", label: "趋向与问题" },
  { value: "evidence_find", label: "找证据" },
];

const OPTIONAL_SCOPE = {
  metadata: "metadata",
  review: "review_background",
  routing: "research_routing_context",
} as const;

export function KnowledgeQueryView({
  paperIds,
  onRemovePaper,
  onCatalogStatus,
  onHealth,
}: KnowledgeQueryViewProps) {
  const [registry, setRegistry] = useState<AgentRegistry | null>(null);
  const [tasks, setTasks] = useState<AgentTaskProjection[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState("");
  const [task, setTask] = useState<AgentTaskProjection | null>(null);
  const [paperTitles, setPaperTitles] = useState<Record<string, string>>({});
  const [queryType, setQueryType] = useState<KnowledgeQueryType>("seven_section_overview");
  const [queryText, setQueryText] = useState("");
  const [executorId, setExecutorId] = useState<"codex_cli" | "claude_code_cli">("codex_cli");
  const [approvedClasses, setApprovedClasses] = useState<string[]>([]);
  const [includeReviewBackground, setIncludeReviewBackground] = useState(false);
  const [includeRoutingContext, setIncludeRoutingContext] = useState(false);
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
    () => registry?.task_kinds.find((item) => item.task_kind === "knowledge_query_report") ?? null,
    [registry],
  );
  const optionalAllowed = useMemo(() => {
    const policy = new Set(registry?.workspace_policy?.allowed_content_classes ?? []);
    return new Set((definition?.optional_content_classes ?? []).filter((item) => policy.has(item)));
  }, [definition, registry]);
  const cardinalityMessage = queryCardinalityMessage(queryType, paperIds.length);

  useEffect(() => {
    let current = true;
    async function load() {
      setLoading(true);
      setError("");
      try {
        const [registryResult, taskResult] = await Promise.all([
          getAgentRegistry(),
          listAgentTasks(50),
        ]);
        if (!current) return;
        const queryTasks = taskResult.tasks.filter((item) => item.task_kind === "knowledge_query_report");
        setRegistry(registryResult);
        setTasks(queryTasks);
        setSelectedTaskId(queryTasks[0]?.task_id ?? "");
      } catch (caught) {
        if (current) setError(errorMessage(caught));
      } finally {
        if (current) setLoading(false);
      }
    }
    void load();
    return () => { current = false; };
  }, []);

  useEffect(() => {
    if (!definition || !registry?.workspace_policy) {
      setApprovedClasses([]);
      return;
    }
    const policy = new Set(registry.workspace_policy.allowed_content_classes);
    const required = definition.required_content_classes.filter((item) => policy.has(item));
    if (optionalAllowed.has(OPTIONAL_SCOPE.metadata)) required.push(OPTIONAL_SCOPE.metadata);
    setApprovedClasses([...new Set(required)].sort());
  }, [definition, optionalAllowed, registry]);

  useEffect(() => {
    let current = true;
    if (paperIds.length === 0) {
      setPaperTitles({});
      return () => { current = false; };
    }
    Promise.all(paperIds.map(async (paperId) => {
      const reading = await getReadingPaper(paperId);
      return [paperId, reading.paper.bibliography.title] as const;
    })).then((items) => {
      if (current) setPaperTitles(Object.fromEntries(items));
    }).catch((caught: unknown) => {
      if (current) setError(errorMessage(caught));
    });
    return () => { current = false; };
  }, [paperIds]);

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
        const candidate = await getAgentPreview(selectedTaskId);
        if (current) setPreview(candidate);
      }
    }).catch((caught: unknown) => {
      if (current) setError(errorMessage(caught));
    });
    return () => { current = false; };
  }, [selectedTaskId]);

  function replaceTask(nextTask: AgentTaskProjection) {
    setTask(nextTask);
    setTasks((current) => {
      const visible = current.filter((item) => item.task_kind === "knowledge_query_report");
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
      const active = health.operation.state === "running" || health.operation.state === "building";
      if (!active && catalog.projection_state !== "stale") return;
      await delay(SETTLE_INTERVAL_MS);
    }
    setNotice("后台索引仍在更新");
  }

  async function runMutation<T extends { task: AgentTaskProjection }>(
    action: () => Promise<T>,
    message: string,
  ): Promise<T | null> {
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
    if (!definition || cardinalityMessage || !queryText.trim()) return;
    const request: KnowledgeQueryCreateRequest = {
      query_type: queryType,
      query_text: queryText.trim(),
      paper_ids: paperIds,
      include_review_background: includeReviewBackground,
      include_routing_context: includeRoutingContext,
      executor_id: executorId,
      approved_content_classes: approvedClasses,
      idempotency_key: crypto.randomUUID(),
    };
    const created = await runMutation(() => createKnowledgeQuery(request), "问答 Task 已创建");
    if (created) {
      setSelectedTaskId(created.task.task_id);
      setInspection(null);
      setHandoff(null);
      setPreview(null);
    }
  }

  async function inspectPayload() {
    if (!task) return;
    setMutating(true);
    setError("");
    try {
      const result = await inspectAgentHandoff(task.task_id, {
        ...expected(task),
        executor_id: task.executor_id as "codex_cli" | "claude_code_cli",
      });
      setInspection(result);
      setNotice("Payload 已就绪");
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setMutating(false);
    }
  }

  async function preparePrompt() {
    if (!task || !inspection) return;
    const result = await runMutation(
      () => prepareAgentHandoff(task.task_id, {
        ...expected(task),
        executor_id: task.executor_id as "codex_cli" | "claude_code_cli",
      }),
      task.status === "leased" ? "Prompt 已恢复" : "Prompt 已生成",
    );
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
    let parsed: { [key: string]: JsonValue };
    try {
      const value = JSON.parse(resultText) as unknown;
      if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("object required");
      parsed = value as { [key: string]: JsonValue };
    } catch {
      setError("JSON 格式无效");
      return;
    }
    const submitted = await runMutation(
      () => submitAgentResult(task.task_id, { ...expected(task), result: parsed }),
      "报告候选已暂存",
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
    setError("");
  }

  async function requestRevision() {
    if (!task || !feedback.trim()) return;
    const result = await runMutation(
      () => requestAgentRevision(task.task_id, { ...expected(task), feedback: feedback.trim() }),
      "已创建修订 Task",
    );
    if (result && "successor_task" in result && result.successor_task) {
      replaceTask(result.successor_task);
      setSelectedTaskId(result.successor_task.task_id);
    }
  }

  async function reject() {
    if (!task) return;
    await runMutation(
      () => rejectAgentResult(task.task_id, { ...expected(task), reason_code: "user_rejected" }),
      "报告候选已拒绝",
    );
  }

  async function acceptReport() {
    if (!task) return;
    await runMutation(
      () => acceptKnowledgeQueryReport(task.task_id, expected(task)),
      "报告已接受；未写入 canonical scientific knowledge",
    );
  }

  async function copyReadableAnswer() {
    const candidate = queryCandidate(preview);
    if (!candidate || !task) return;
    try {
      await copyKnowledgeQueryAnswer(task);
      setNotice("回答已复制");
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  function toggleScope(contentClass: string, enabled: boolean) {
    setApprovedClasses((current) => enabled
      ? [...new Set([...current, contentClass])].sort()
      : current.filter((item) => item !== contentClass));
  }

  function toggleReview(enabled: boolean) {
    setIncludeReviewBackground(enabled);
    toggleScope(OPTIONAL_SCOPE.review, enabled);
  }

  function toggleRouting(enabled: boolean) {
    setIncludeRoutingContext(enabled);
    toggleScope(OPTIONAL_SCOPE.routing, enabled);
  }

  const candidate = queryCandidate(preview);
  const canInspect = task?.status === "created" || task?.status === "leased";
  const canSubmit = task?.status === "leased" && handoff !== null;
  const canDecide = task?.status === "submitted" && candidate !== null;
  const requiredMissing = definition?.required_content_classes.some(
    (item) => !approvedClasses.includes(item),
  ) ?? true;

  return (
    <section className="query-view" aria-labelledby="knowledge-query-title">
      <header className="view-heading query-heading">
        <div>
          <p className="section-kicker">KNOWLEDGE QUERY</p>
          <h2 id="knowledge-query-title">知识库问答</h2>
        </div>
        <span className={`operation-chip operation-${mutating ? "running" : "idle"}`}>
          {mutating ? "processing" : "ready"}
        </span>
      </header>

      {error ? <div className="error-banner" role="alert">{error}</div> : null}
      {notice ? <div className="notice-banner" role="status">{notice}</div> : null}

      <div className="query-grid">
        <section className="query-compose-pane" aria-labelledby="query-compose-title">
          <div className="subsection-heading">
            <h3 id="query-compose-title">问题与范围</h3>
            <MessageSquareText size={17} aria-hidden="true" />
          </div>

          <div className="query-paper-selection" aria-label="已选择论文">
            <div className="query-paper-count">
              <strong>{paperIds.length}</strong>
              <span>篇已选择</span>
            </div>
            {paperIds.map((paperId) => (
              <div className="query-paper-row" key={paperId}>
                <span>{paperTitles[paperId] ?? paperId}</span>
                {onRemovePaper ? (
                  <button className="icon-button compact-icon" type="button" onClick={() => onRemovePaper(paperId)} title="移出问答" aria-label={`移出 ${paperTitles[paperId] ?? paperId}`}>
                    <X size={15} />
                  </button>
                ) : null}
              </div>
            ))}
            {paperIds.length === 0 ? <div className="compact-empty">从文献库选择 1-4 篇论文</div> : null}
          </div>

          <label htmlFor="query-type">问题类型</label>
          <select id="query-type" value={queryType} onChange={(event) => setQueryType(event.target.value as KnowledgeQueryType)} disabled={mutating}>
            {QUERY_OPTIONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
          </select>
          {cardinalityMessage ? <p className="query-validation" role="status">{cardinalityMessage}</p> : null}

          <label htmlFor="query-text">研究问题</label>
          <textarea id="query-text" value={queryText} onChange={(event) => setQueryText(event.target.value)} maxLength={2000} disabled={mutating} />

          <label htmlFor="query-executor">External Agent</label>
          <select id="query-executor" value={executorId} onChange={(event) => setExecutorId(event.target.value as "codex_cli" | "claude_code_cli")} disabled={mutating}>
            {registry?.executors.map((executor) => <option key={executor.executor_id} value={executor.executor_id}>{humanize(executor.executor_id)}</option>)}
          </select>

          <fieldset className="query-scope" disabled={mutating || !definition}>
            <legend>Prompt 内容范围</legend>
            <label>
              <input type="checkbox" checked={approvedClasses.includes(OPTIONAL_SCOPE.metadata)} onChange={(event) => toggleScope(OPTIONAL_SCOPE.metadata, event.target.checked)} disabled={!optionalAllowed.has(OPTIONAL_SCOPE.metadata)} />
              <span>文献 metadata</span>
            </label>
            <label>
              <input type="checkbox" checked={includeReviewBackground} onChange={(event) => toggleReview(event.target.checked)} disabled={!optionalAllowed.has(OPTIONAL_SCOPE.review)} />
              <span>加入 Review Memory 背景</span>
            </label>
            <label>
              <input type="checkbox" checked={includeRoutingContext} onChange={(event) => toggleRouting(event.target.checked)} disabled={!optionalAllowed.has(OPTIONAL_SCOPE.routing)} />
              <span>加入研究问题上下文</span>
            </label>
          </fieldset>

          <button className="start-button" type="button" onClick={createTask} disabled={mutating || loading || !definition || !queryText.trim() || Boolean(cardinalityMessage) || requiredMissing}>
            <MessageSquareText size={17} aria-hidden="true" />
            创建问答 Task
          </button>

          <div className="query-task-history">
            <div className="subsection-heading"><h3>当前任务报告</h3><History size={16} /></div>
            {tasks.map((item) => (
              <button key={item.task_id} type="button" className={selectedTaskId === item.task_id ? "agent-task-selected" : ""} onClick={() => setSelectedTaskId(item.task_id)}>
                <span>{item.task_id}</span>
                <small>{item.query_type ? queryLabel(item.query_type) : "Knowledge Query"}</small>
                <strong>{item.status}</strong>
              </button>
            ))}
            {!tasks.length && !loading ? <div className="compact-empty">尚无问答 Task</div> : null}
          </div>
        </section>

        <section className="query-handoff-pane" aria-labelledby="query-handoff-title">
          <div className="subsection-heading">
            <div><h3 id="query-handoff-title">Agent Handoff</h3><span>{task?.result_contract ?? "no task selected"}</span></div>
            {task ? <span className={`status-badge job-status-${task.status}`}>{task.status}</span> : null}
          </div>

          <div className="agent-action-row">
            <button type="button" onClick={inspectPayload} disabled={mutating || !canInspect}>
              <ShieldCheck size={16} />预览 Payload
            </button>
            <button type="button" onClick={preparePrompt} disabled={mutating || !inspection || !canInspect}>
              <Send size={16} />{task?.status === "leased" ? "恢复 Prompt" : "生成 Prompt"}
            </button>
            <button className="icon-button" type="button" onClick={copyPrompt} disabled={!handoff} title="复制 Prompt" aria-label="复制 Prompt">
              <Clipboard size={16} />
            </button>
            <button className="icon-button" type="button" onClick={exportPrompt} disabled={!handoff} title="导出 Task package" aria-label="导出 Task package">
              <Download size={16} />
            </button>
          </div>

          {inspection ? (
            <div className="agent-code-block">
              <div><strong>Admissible payload</strong><span>{formatBytes(inspection.handoff_preview.prompt_bytes)}</span></div>
              <pre>{JSON.stringify(inspection.handoff_preview.payload, null, 2)}</pre>
            </div>
          ) : null}
          {handoff ? (
            <div className="agent-code-block prompt-block">
              <div><strong>Prompt manifest</strong><span>{humanize(handoff.handoff.executor_id)}</span></div>
              <pre>{handoffManifestText(handoff)}</pre>
            </div>
          ) : null}

          <label htmlFor="query-result-json">Agent JSON</label>
          <textarea id="query-result-json" value={resultText} onChange={(event) => setResultText(event.target.value)} placeholder="{}" spellCheck={false} disabled={mutating || task?.status === "submitted" || task?.status === "approved"} />
          <div className="agent-import-row">
            <label className="file-command" htmlFor="query-result-file"><FileJson size={16} />JSON 文件</label>
            <input id="query-result-file" className="visually-hidden" type="file" accept="application/json,.json" onChange={loadJsonFile} />
            <button type="button" onClick={importResult} disabled={mutating || !canSubmit || !resultText.trim()}>
              {mutating ? <LoaderCircle size={16} className="spin" /> : <Send size={16} />}导入结果
            </button>
          </div>
        </section>

        <section className="query-report-pane" aria-labelledby="query-report-title">
          <div className="subsection-heading">
            <div><h3 id="query-report-title">报告预览</h3><span>仅限当前任务报告</span></div>
            <div className="query-export-actions">
              <button className="icon-button" type="button" onClick={copyReadableAnswer} disabled={!candidate} title="复制回答" aria-label="复制回答"><Copy size={16} /></button>
            </div>
          </div>

          {candidate ? <QueryReport candidate={candidate} /> : <div className="compact-empty">导入并通过 Core 校验后显示报告</div>}

          <label htmlFor="query-revision-feedback">Revision feedback</label>
          <textarea id="query-revision-feedback" value={feedback} onChange={(event) => setFeedback(event.target.value)} maxLength={4000} disabled={!canDecide || mutating} />
          <div className="agent-decision-row">
            <button type="button" onClick={requestRevision} disabled={!canDecide || !feedback.trim() || mutating}><RotateCcw size={16} />请求修订</button>
            <button type="button" onClick={reject} disabled={!canDecide || mutating}><X size={16} />拒绝</button>
            <button className="approve-button" type="button" onClick={acceptReport} disabled={!canDecide || mutating}><Check size={16} />接受报告</button>
          </div>
        </section>
      </div>
    </section>
  );
}

function QueryReport({ candidate }: { candidate: QueryCandidate }) {
  return (
    <div className="query-report">
      <div className="query-report-boundary" role="note">
        <strong>Report only</strong>
        <span>不会刷新 Question Mapping 或 Research Synthesis。</span>
      </div>
      {candidate.answer_blocks.map((block, index) => (
        <article className={`query-answer-block query-role-${block.block_role}`} key={`${block.block_role}-${index}`}>
          <header>
            <span>{answerRoleLabel(block.block_role)}</span>
            {block.background_only ? <strong>仅作背景</strong> : null}
          </header>
          <p>{block.text}</p>
          <div className="query-support-line">
            {block.support_refs.length > 0 ? <span>{block.support_refs.length} 个 Card Unit 支持</span> : null}
            {block.support_refs.length > 0 ? <span>{block.support_refs.reduce((total, item) => total + item.evidence_ids.length, 0)} 条 Evidence</span> : null}
            {block.background_refs.length > 0 ? <span>{block.background_refs.length} 条 Review 背景</span> : null}
          </div>
        </article>
      ))}
      {candidate.unresolved_items.length > 0 ? (
        <section className="query-unresolved">
          <h4>尚未解决</h4>
          <ul>{candidate.unresolved_items.map((item) => <li key={item}>{item}</li>)}</ul>
        </section>
      ) : null}
    </div>
  );
}

function queryCandidate(preview: AgentPreviewResult | null): QueryCandidate | null {
  if (!preview) return null;
  const value = preview.candidate as Record<string, unknown>;
  if (!Array.isArray(value.answer_blocks) || !Array.isArray(value.unresolved_items)) return null;
  return value as unknown as QueryCandidate;
}

function queryCardinalityMessage(type: KnowledgeQueryType, count: number): string {
  if (["single_paper_explanation", "seven_section_overview", "methods"].includes(type)) {
    return count === 1 ? "" : "该问题类型需要恰好 1 篇论文";
  }
  if (["selected_paper_comparison", "trend_problem_discussion"].includes(type)) {
    return count >= 2 && count <= 4 ? "" : "该问题类型需要 2-4 篇论文";
  }
  return count >= 1 && count <= 4 ? "" : "找证据需要 1-4 篇论文";
}

function expected(task: AgentTaskProjection) {
  return { expected_state_id: task.state_id, expected_state_digest: task.state_digest };
}

function queryLabel(type: KnowledgeQueryType): string {
  return QUERY_OPTIONS.find((item) => item.value === type)?.label ?? humanize(type);
}

function answerRoleLabel(role: string): string {
  if (role === "factual") return "事实回答";
  if (role === "cross_paper_synthesis") return "跨论文综合";
  if (role === "background") return "综述背景";
  return "尚未解决";
}

function humanize(value: string): string {
  return value.replaceAll("_", " ");
}

function formatBytes(value: number): string {
  return value < 1024 ? `${value} B` : `${(value / 1024).toFixed(1)} KB`;
}

function delay(milliseconds: number) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function errorMessage(caught: unknown): string {
  if (caught instanceof ApiError) return `${caught.code}: ${caught.message}`;
  return "问答任务未完成";
}
