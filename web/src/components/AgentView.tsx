import {
  BookOpenCheck,
  Bot,
  Check,
  Clipboard,
  Download,
  FileJson,
  LoaderCircle,
  RefreshCw,
  RotateCcw,
  Send,
  ShieldCheck,
  X,
} from "lucide-react";
import { ChangeEvent, useEffect, useMemo, useState } from "react";
import {
  ApiError,
  approveAgentResult,
  createAgentTask,
  decideSourceAdequacyResolution,
  getAgentPreview,
  getAgentRegistry,
  getAgentTask,
  getCatalogStatus,
  getHealth,
  getIntakeJob,
  getSourceAdequacyResolution,
  inspectAgentHandoff,
  listAgentTasks,
  listIntakeJobs,
  openSourceAdequacyReview,
  prepareAgentHandoff,
  refreshAgentTask,
  rejectAgentResult,
  requestAgentRevision,
  submitAgentResult,
  type AgentHandoffResult,
  type AgentInspectResult,
  type AgentPreviewResult,
  type AgentRegistry,
  type AgentTaskKind,
  type AgentTaskProjection,
  type CatalogStatus,
  type HealthResult,
  type IntakeJobDetail,
  type JsonValue,
  type PipelineProjection,
  type SourceAdequacyResolutionContext,
} from "../api";
import { copyHandoffToClipboard, exportHandoffPackage, handoffManifestText } from "../egress";

type AgentViewProps = {
  onCatalogStatus: (status: CatalogStatus) => void;
  onHealth: (health: HealthResult) => void;
};

const SETTLE_ATTEMPTS = 80;
const SETTLE_INTERVAL_MS = 150;

export function AgentView({ onCatalogStatus, onHealth }: AgentViewProps) {
  const [registry, setRegistry] = useState<AgentRegistry | null>(null);
  const [jobs, setJobs] = useState<PipelineProjection[]>([]);
  const [tasks, setTasks] = useState<AgentTaskProjection[]>([]);
  const [selectedJobId, setSelectedJobId] = useState("");
  const [selectedTaskId, setSelectedTaskId] = useState("");
  const [jobDetail, setJobDetail] = useState<IntakeJobDetail | null>(null);
  const [task, setTask] = useState<AgentTaskProjection | null>(null);
  const [taskKind, setTaskKind] = useState<AgentTaskKind>("document_route_resolution");
  const [executorId, setExecutorId] = useState<"codex_cli" | "claude_code_cli">("codex_cli");
  const [approvedClasses, setApprovedClasses] = useState<string[]>([]);
  const [inspection, setInspection] = useState<AgentInspectResult | null>(null);
  const [handoff, setHandoff] = useState<AgentHandoffResult | null>(null);
  const [resultText, setResultText] = useState("");
  const [preview, setPreview] = useState<AgentPreviewResult | null>(null);
  const [feedback, setFeedback] = useState("");
  const [adequacyResolution, setAdequacyResolution] = useState<SourceAdequacyResolutionContext | null>(null);
  const [sourceReviewConfirmation, setSourceReviewConfirmation] = useState("");
  const [readingOrderConfirmed, setReadingOrderConfirmed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [mutating, setMutating] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const definition = useMemo(
    () => registry?.task_kinds.find((item) => item.task_kind === taskKind) ?? null,
    [registry, taskKind],
  );

  useEffect(() => {
    let current = true;
    async function load() {
      setLoading(true);
      setError("");
      try {
        const [registryResult, jobsResult, tasksResult] = await Promise.all([
          getAgentRegistry(),
          listIntakeJobs(50),
          listAgentTasks(50),
        ]);
        if (!current) return;
        setRegistry(registryResult);
        setJobs(jobsResult.jobs);
        const pipelineTasks = tasksResult.tasks.filter((item) => !["knowledge_query_report", "organization_proposal"].includes(item.task_kind));
        setTasks(pipelineTasks);
        const firstAvailable = registryResult.task_kinds.find(
          (item) => item.runtime_status === "available" && !["knowledge_query_report", "organization_proposal"].includes(item.task_kind),
        );
        if (firstAvailable) setTaskKind(firstAvailable.task_kind as AgentTaskKind);
        setSelectedJobId(jobsResult.jobs[0]?.job_id ?? "");
        setSelectedTaskId(pipelineTasks[0]?.task_id ?? "");
      } catch (caught) {
        if (current) setError(errorMessage(caught));
      } finally {
        if (current) setLoading(false);
      }
    }
    void load();
    return () => {
      current = false;
    };
  }, []);

  useEffect(() => {
    if (!selectedJobId) {
      setJobDetail(null);
      return;
    }
    let current = true;
    getIntakeJob(selectedJobId)
      .then((detail) => {
        if (!current) return;
        setJobDetail(detail);
        const suggested = suggestedTaskKind(detail.pipeline);
        if (suggested && registry?.task_kinds.some(
          (item) => item.task_kind === suggested && item.runtime_status === "available",
        )) {
          setTaskKind(suggested);
        }
      })
      .catch((caught: unknown) => {
        if (current) setError(errorMessage(caught));
      });
    return () => {
      current = false;
    };
  }, [registry, selectedJobId]);

  useEffect(() => {
    if (!selectedTaskId) {
      setTask(null);
      return;
    }
    let current = true;
    setInspection(null);
    setHandoff(null);
    setPreview(null);
    setResultText("");
    setAdequacyResolution(null);
    setSourceReviewConfirmation("");
    setReadingOrderConfirmed(false);
    getAgentTask(selectedTaskId)
      .then(async (detail) => {
        if (!current) return;
        setTask(detail.current_task);
        if (["primary_semantic_processing", "review_semantic_processing"].includes(detail.current_task.task_kind)) {
          const resolution = await getSourceAdequacyResolution(selectedTaskId);
          if (current) setAdequacyResolution(resolution);
        }
        if (detail.current_task.status === "submitted") {
          const nextPreview = await getAgentPreview(selectedTaskId);
          if (current) setPreview(nextPreview);
        }
      })
      .catch((caught: unknown) => {
        if (current) setError(errorMessage(caught));
      });
    return () => {
      current = false;
    };
  }, [selectedTaskId]);

  useEffect(() => {
    if (!definition) {
      setApprovedClasses([]);
      return;
    }
    setApprovedClasses([...definition.required_content_classes]);
  }, [definition]);

  function replaceTask(nextTask: AgentTaskProjection) {
    setTask(nextTask);
    setTasks((current) => {
      const index = current.findIndex((item) => item.task_id === nextTask.task_id);
      if (index < 0) return [nextTask, ...current];
      return current.map((item) => item.task_id === nextTask.task_id ? nextTask : item);
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

  async function refreshLists() {
    setLoading(true);
    setError("");
    try {
      const [jobsResult, tasksResult] = await Promise.all([
        listIntakeJobs(50),
        listAgentTasks(50),
      ]);
      setJobs(jobsResult.jobs);
      const pipelineTasks = tasksResult.tasks.filter((item) => !["knowledge_query_report", "organization_proposal"].includes(item.task_kind));
      setTasks(pipelineTasks);
      if (!pipelineTasks.some((item) => item.task_id === selectedTaskId)) {
        setSelectedTaskId(pipelineTasks[0]?.task_id ?? "");
      }
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setLoading(false);
    }
  }

  async function createTask() {
    if (!jobDetail?.paper_id || !selectedJobId) return;
    const created = await runMutation(
      () => createAgentTask(selectedJobId, {
        paper_id: jobDetail.paper_id!,
        task_kind: taskKind,
        executor_id: executorId,
        approved_content_classes: approvedClasses,
        idempotency_key: crypto.randomUUID(),
      }),
      "Task 已创建",
    );
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
      "候选已暂存",
    );
    if (!submitted) return;
    if (submitted.status === "blocked") {
      const gate = submitted.source_adequacy;
      setPreview(null);
      setNotice(
        gate
          ? `Source Adequacy 阻断：${humanize(gate.requested_operation)} · ${gate.capability_status ?? gate.freshness}`
          : "Source Adequacy 阻断了候选暂存",
      );
      if (["primary_semantic_processing", "review_semantic_processing"].includes(submitted.task.task_kind)) {
        setAdequacyResolution(await getSourceAdequacyResolution(submitted.task.task_id));
      }
      return;
    }
    setPreview(await getAgentPreview(task.task_id));
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

  async function approve() {
    if (!task) return;
    await runMutation(
      () => approveAgentResult(task.task_id, expected(task)),
      "候选已批准",
    );
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
      "候选已拒绝",
    );
  }

  async function refreshInputs() {
    if (!task) return;
    const result = await runMutation(
      () => refreshAgentTask(task.task_id, expected(task)),
      "输入已刷新",
    );
    if (result && "successor_task" in result && result.successor_task) {
      replaceTask(result.successor_task);
      setSelectedTaskId(result.successor_task.task_id);
    }
  }

  async function openAdequacySource() {
    if (!task) return;
    setMutating(true);
    setError("");
    setNotice("");
    try {
      const opened = await openSourceAdequacyReview(task.task_id, expected(task));
      setSourceReviewConfirmation(opened.confirmation.confirmation_id);
      setReadingOrderConfirmed(false);
      setNotice(opened.reader.provider === "updf" ? "已在 UPDF 中打开" : "已在系统阅读器中打开");
    } catch (caught) {
      setSourceReviewConfirmation("");
      setReadingOrderConfirmed(false);
      setError(errorMessage(caught));
    } finally {
      setMutating(false);
    }
  }

  async function decideAdequacy(action: "accept_uncertainty" | "remediation_required") {
    if (!task) return;
    const result = await runMutation(
      () => decideSourceAdequacyResolution(task.task_id, {
        ...expected(task),
        action,
        ...(action === "accept_uncertainty" && sourceReviewConfirmation
          ? { confirmation_id: sourceReviewConfirmation }
          : {}),
      }),
      action === "accept_uncertainty" ? "输入已更新，请重新生成 Prompt" : "已转入重新解析",
    );
    if (!result) return;
    setInspection(null);
    setHandoff(null);
    setPreview(null);
    setResultText("");
    setSourceReviewConfirmation("");
    setReadingOrderConfirmed(false);
    if (result.successor_task) {
      replaceTask(result.successor_task);
      setSelectedTaskId(result.successor_task.task_id);
      setAdequacyResolution(null);
    } else {
      setAdequacyResolution(await getSourceAdequacyResolution(task.task_id));
    }
  }

  function toggleOptionalClass(contentClass: string) {
    setApprovedClasses((current) => current.includes(contentClass)
      ? current.filter((item) => item !== contentClass)
      : [...current, contentClass].sort());
  }

  const canInspect = task?.status === "created" || task?.status === "leased";
  const canSubmit = task?.status === "leased" && handoff !== null;
  const canDecide = task?.status === "submitted" && preview !== null;

  return (
    <section className="agent-view" aria-labelledby="agent-view-title">
      <header className="view-heading">
        <div>
          <p className="section-kicker">AGENT HANDOFF</p>
          <h2 id="agent-view-title">Agent 工作台</h2>
        </div>
        <div className="agent-heading-actions">
          <span className={`operation-chip operation-${mutating ? "running" : "idle"}`}>
            {mutating ? "processing" : "ready"}
          </span>
          <button className="icon-button" type="button" onClick={refreshLists} disabled={loading || mutating} title="刷新" aria-label="刷新">
            <RefreshCw size={16} className={loading ? "spin" : ""} />
          </button>
        </div>
      </header>

      {error ? <div className="error-banner" role="alert">{error}</div> : null}
      {notice ? <div className="notice-banner" role="status">{notice}</div> : null}

      <div className="agent-grid">
        <section className="agent-source-pane" aria-labelledby="agent-source-title">
          <div className="subsection-heading">
            <h3 id="agent-source-title">任务路由</h3>
            <Bot size={17} aria-hidden="true" />
          </div>

          <label htmlFor="agent-job">Pipeline Job</label>
          <select id="agent-job" value={selectedJobId} onChange={(event) => setSelectedJobId(event.target.value)} disabled={mutating}>
            <option value="">选择 Job</option>
            {jobs.map((job) => <option key={job.job_id} value={job.job_id}>{job.job_id} · {job.current_node}</option>)}
          </select>

          <label htmlFor="agent-kind">Task kind</label>
          <select id="agent-kind" value={taskKind} onChange={(event) => setTaskKind(event.target.value as AgentTaskKind)} disabled={mutating}>
            {registry?.task_kinds.filter(
              (item) => item.runtime_status === "available" && !["knowledge_query_report", "organization_proposal"].includes(item.task_kind),
            ).map((item) => (
              <option key={item.task_kind} value={item.task_kind}>{humanize(item.task_kind)}</option>
            ))}
          </select>

          <label htmlFor="agent-executor">External Agent</label>
          <select id="agent-executor" value={executorId} onChange={(event) => setExecutorId(event.target.value as "codex_cli" | "claude_code_cli")} disabled={mutating}>
            {registry?.executors.map((executor) => <option key={executor.executor_id} value={executor.executor_id}>{humanize(executor.executor_id)}</option>)}
          </select>

          <fieldset className="agent-scope" disabled={mutating}>
            <legend>Content scope</legend>
            {definition?.required_content_classes.map((contentClass) => (
              <label key={contentClass}>
                <input type="checkbox" checked readOnly disabled />
                <span>{humanize(contentClass)}</span>
                <strong>required</strong>
              </label>
            ))}
            {definition?.optional_content_classes.map((contentClass) => (
              <label key={contentClass}>
                <input
                  type="checkbox"
                  checked={approvedClasses.includes(contentClass)}
                  onChange={() => toggleOptionalClass(contentClass)}
                />
                <span>{humanize(contentClass)}</span>
                <strong>optional</strong>
              </label>
            ))}
          </fieldset>

          <button type="button" className="start-button" onClick={createTask} disabled={mutating || !jobDetail?.paper_id || !definition}>
            <Bot size={17} aria-hidden="true" />
            创建 Task
          </button>

          <div className="agent-task-list" aria-label="Agent Tasks">
            {tasks.map((item) => (
              <button
                key={item.task_id}
                type="button"
                className={selectedTaskId === item.task_id ? "agent-task-selected" : ""}
                onClick={() => setSelectedTaskId(item.task_id)}
              >
                <span>{item.task_id}</span>
                <small>{humanize(item.task_kind)}</small>
                <strong>{item.status}</strong>
              </button>
            ))}
            {!tasks.length && !loading ? <div className="compact-empty">尚无 Agent Task</div> : null}
          </div>
        </section>

        <section className="agent-handoff-pane" aria-labelledby="agent-handoff-title">
          <div className="subsection-heading">
            <div>
              <h3 id="agent-handoff-title">Handoff</h3>
              <span>{task?.result_contract ?? "no task selected"}</span>
            </div>
            {task ? <span className={`status-badge job-status-${task.status}`}>{task.status}</span> : null}
          </div>

          {task ? (
            <dl className="agent-task-facts">
              <div><dt>Task</dt><dd>{task.task_id}</dd></div>
              <div><dt>Executor</dt><dd>{humanize(task.executor_id)}</dd></div>
              <div><dt>Revision</dt><dd>{task.revision}</dd></div>
              <div><dt>Scope</dt><dd>{task.effective_content_classes.length}</dd></div>
            </dl>
          ) : <div className="compact-empty">选择一个 Agent Task</div>}

          {adequacyResolution && adequacyResolution.resolution_state !== "not_required" ? (
            <div className={`source-resolution-panel source-resolution-${adequacyResolution.resolution_state}`}>
              <div className="source-resolution-heading">
                <BookOpenCheck size={17} aria-hidden="true" />
                <div>
                  <strong>{resolutionTitle(adequacyResolution.resolution_state)}</strong>
                  <span>{resolutionSummary(adequacyResolution)}</span>
                </div>
              </div>
              {adequacyResolution.resolution_state === "review_required" ? (
                <>
                  <button type="button" className="secondary-button" onClick={openAdequacySource} disabled={mutating}>
                    <BookOpenCheck size={16} aria-hidden="true" />
                    在 UPDF 中检查
                  </button>
                  <label className="source-resolution-confirmation">
                    <input
                      type="checkbox"
                      checked={readingOrderConfirmed}
                      onChange={(event) => setReadingOrderConfirmed(event.target.checked)}
                      disabled={!sourceReviewConfirmation || mutating}
                    />
                    <span>我已确认正文段落可按顺序连续阅读</span>
                  </label>
                </>
              ) : null}
              <div className="source-resolution-actions">
                {adequacyResolution.resolution_state === "review_required" || adequacyResolution.resolution_state === "accepted_refresh_required" ? (
                  <button
                    type="button"
                    onClick={() => decideAdequacy("accept_uncertainty")}
                    disabled={mutating || (adequacyResolution.resolution_state === "review_required" && (!sourceReviewConfirmation || !readingOrderConfirmed))}
                  >
                    <Check size={16} aria-hidden="true" />
                    {adequacyResolution.resolution_state === "accepted_refresh_required" ? "继续更新输入" : "接受并重新生成候选"}
                  </button>
                ) : null}
                {adequacyResolution.resolution_state === "review_required" || adequacyResolution.resolution_state === "remediation_refresh_required" ? (
                  <button type="button" className="secondary-button" onClick={() => decideAdequacy("remediation_required")} disabled={mutating}>
                    <RotateCcw size={16} aria-hidden="true" />
                    {adequacyResolution.resolution_state === "remediation_refresh_required" ? "继续进入重解析" : "需要重新解析"}
                  </button>
                ) : null}
              </div>
            </div>
          ) : null}

          <div className="agent-action-row">
            <button type="button" onClick={inspectPayload} disabled={mutating || !canInspect}>
              <ShieldCheck size={16} aria-hidden="true" />
              预览 Payload
            </button>
            <button type="button" onClick={preparePrompt} disabled={mutating || !inspection || !canInspect}>
              <Send size={16} aria-hidden="true" />
              {task?.status === "leased" ? "恢复 Prompt" : "生成 Prompt"}
            </button>
            <button className="icon-button" type="button" onClick={copyPrompt} disabled={!handoff} title="复制 Prompt" aria-label="复制 Prompt">
              <Clipboard size={16} />
            </button>
            <button className="icon-button" type="button" onClick={exportPrompt} disabled={!handoff} title="导出 Task package" aria-label="导出 Task package">
              <Download size={16} />
            </button>
            <button className="icon-button" type="button" onClick={refreshInputs} disabled={mutating || !task || !["created", "leased", "submitted"].includes(task.status) || task.task_kind === "document_route_resolution"} title="刷新输入" aria-label="刷新输入">
              <RotateCcw size={16} />
            </button>
          </div>

          {inspection ? (
            <div className="agent-code-block">
              <div><strong>Payload</strong><span>{formatBytes(inspection.handoff_preview.prompt_bytes)}</span></div>
              <pre>{JSON.stringify(inspection.handoff_preview.payload, null, 2)}</pre>
            </div>
          ) : null}

          {handoff ? (
            <div className="agent-code-block prompt-block">
              <div><strong>Prompt manifest</strong><span>{humanize(handoff.handoff.executor_id)}</span></div>
              <pre>{handoffManifestText(handoff)}</pre>
            </div>
          ) : null}
        </section>

        <section className="agent-result-pane" aria-labelledby="agent-result-title">
          <div className="subsection-heading">
            <h3 id="agent-result-title">Candidate</h3>
            <FileJson size={17} aria-hidden="true" />
          </div>

          <label htmlFor="agent-result-json">Agent JSON</label>
          <textarea
            id="agent-result-json"
            value={resultText}
            onChange={(event) => setResultText(event.target.value)}
            placeholder="{}"
            spellCheck={false}
            disabled={mutating || task?.status === "submitted"}
          />
          <div className="agent-import-row">
            <label className="file-command" htmlFor="agent-result-file">
              <FileJson size={16} aria-hidden="true" />
              JSON 文件
            </label>
            <input id="agent-result-file" className="visually-hidden" type="file" accept="application/json,.json" onChange={loadJsonFile} />
            <button type="button" onClick={importResult} disabled={mutating || !canSubmit || !resultText.trim()}>
              {mutating ? <LoaderCircle size={16} className="spin" /> : <Send size={16} />}
              导入结果
            </button>
          </div>

          {preview ? (
            <div className="agent-code-block candidate-block">
              <div><strong>Candidate preview</strong><span>{task?.status ?? preview.task.status}</span></div>
              <pre>{JSON.stringify(preview.candidate, null, 2)}</pre>
            </div>
          ) : null}

          <label htmlFor="agent-feedback">Revision feedback</label>
          <textarea
            id="agent-feedback"
            className="feedback-input"
            value={feedback}
            onChange={(event) => setFeedback(event.target.value)}
            maxLength={4000}
            disabled={mutating || !canDecide}
          />
          <div className="agent-decision-row">
            <button type="button" onClick={approve} disabled={mutating || !canDecide}>
              <Check size={16} aria-hidden="true" />
              批准写入
            </button>
            <button type="button" className="secondary-button" onClick={requestRevision} disabled={mutating || !canDecide || !feedback.trim()}>
              <RotateCcw size={16} aria-hidden="true" />
              请求修订
            </button>
            <button type="button" className="secondary-button danger-button" onClick={reject} disabled={mutating || !canDecide}>
              <X size={16} aria-hidden="true" />
              拒绝
            </button>
          </div>
        </section>
      </div>
    </section>
  );
}

function expected(task: AgentTaskProjection) {
  return {
    expected_state_id: task.state_id,
    expected_state_digest: task.state_digest,
  };
}

function suggestedTaskKind(job: PipelineProjection): AgentTaskKind | null {
  if (job.wait_reason === "route_ambiguous") return "document_route_resolution";
  if (job.current_node === "primary_semantic_gate") return "primary_semantic_processing";
  if (["review_semantic_gate", "review_semantic_gate_mixed_document"].includes(job.current_node)) {
    return "review_semantic_processing";
  }
  return null;
}

function errorMessage(caught: unknown): string {
  if (caught instanceof ApiError) return `${caught.code}: ${caught.message}`;
  return "请求未完成";
}

function humanize(value: string): string {
  return value.replaceAll("_", " ");
}

function resolutionTitle(state: SourceAdequacyResolutionContext["resolution_state"]): string {
  if (state === "review_required") return "需要检查正文阅读顺序";
  if (state === "accepted_refresh_required") return "阅读顺序已确认，等待更新输入";
  if (state === "remediation_refresh_required") return "等待重新解析";
  if (state === "stale") return "来源或解析已变化";
  return "当前来源不足以支持连续正文引用";
}

function resolutionSummary(context: SourceAdequacyResolutionContext): string {
  if (context.resolution_state === "review_required") return "当前解析可用于基础理解，但连续正文引用需要人工确认。";
  if (context.resolution_state === "stale") return "请先重新检查来源或解析状态。";
  if (context.resolution_state === "not_resolvable") return "确定性检查未通过，不能人工覆盖。";
  return context.known_limitations[0] ?? "按当前状态继续处理。";
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}
