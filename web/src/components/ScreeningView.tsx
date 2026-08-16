import {
  Clipboard,
  Download,
  FileJson,
  ListChecks,
  Save,
  Send,
  ShieldCheck,
  XCircle,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  ApiError,
  approveScreeningProposal,
  createScreeningCriteriaProposal,
  createScreeningDecisionProposal,
  getAgentPreview,
  getAgentRegistry,
  getAgentTask,
  getCatalogStatus,
  getHealth,
  inspectAgentHandoff,
  listAgentTasks,
  listScreeningCriteria,
  listScreeningDecisions,
  prepareAgentHandoff,
  promoteScreeningCriteria,
  promoteScreeningDecision,
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
  type ScreeningCriteria,
  type ScreeningDisposition,
} from "../api";
import { copyHandoffToClipboard, exportHandoffPackage } from "../egress";

type Props = {
  initialPaperIds?: string[];
  onCatalogStatus?: (status: CatalogStatus) => void;
  onHealth?: (health: HealthResult) => void;
};

type ObjectMode = "criteria" | "decision";
type WorkMode = "manual" | "agent";
const SCREENING_TASKS = new Set(["question_screening_criteria_proposal", "question_screening_decision_proposal"]);

export function ScreeningView({ initialPaperIds = [], onCatalogStatus, onHealth }: Props) {
  const [objectMode, setObjectMode] = useState<ObjectMode>("criteria");
  const [workMode, setWorkMode] = useState<WorkMode>("manual");
  const [questionId, setQuestionId] = useState("");
  const [paperId, setPaperId] = useState(initialPaperIds[0] ?? "");
  const [criteria, setCriteria] = useState<ScreeningCriteria[]>([]);
  const [selectedCriteriaId, setSelectedCriteriaId] = useState("");
  const selectedCriteria = useMemo(() => criteria.find((item) => item.criteria_id === selectedCriteriaId) ?? criteria[0] ?? null, [criteria, selectedCriteriaId]);
  const [criteriaLoading, setCriteriaLoading] = useState(false);
  const criteriaEditRevision = useRef(0);
  const lookupBaseEditRevision = useRef(0);
  const [title, setTitle] = useState("");
  const [scope, setScope] = useState("");
  const [inclusionText, setInclusionText] = useState("");
  const [exclusionText, setExclusionText] = useState("");
  const [notes, setNotes] = useState("");
  const [outcome, setOutcome] = useState<"included" | "excluded">("included");
  const [basisScope, setBasisScope] = useState<"metadata" | "available_abstract" | "paper_card" | "user_full_text_review" | "mixed">("metadata");
  const [rationale, setRationale] = useState("");
  const [limitations, setLimitations] = useState("");
  const [dispositions, setDispositions] = useState<ScreeningDisposition[]>([]);
  const [registry, setRegistry] = useState<AgentRegistry | null>(null);
  const [executorId, setExecutorId] = useState<"codex_cli" | "claude_code_cli">("codex_cli");
  const [proposalGoal, setProposalGoal] = useState("");
  const [includePaperCard, setIncludePaperCard] = useState(false);
  const [tasks, setTasks] = useState<AgentTaskProjection[]>([]);
  const [task, setTask] = useState<AgentTaskProjection | null>(null);
  const [inspection, setInspection] = useState<AgentInspectResult | null>(null);
  const [handoff, setHandoff] = useState<AgentHandoffResult | null>(null);
  const [preview, setPreview] = useState<AgentPreviewResult | null>(null);
  const [resultText, setResultText] = useState("{}");
  const [feedback, setFeedback] = useState("");
  const [mutating, setMutating] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    const resetCriteriaDraft = () => {
      setTitle("");
      setScope("");
      setInclusionText("");
      setExclusionText("");
      setNotes("");
      setDispositions([]);
    };
    if (!questionId.trim()) {
      setCriteria([]);
      setSelectedCriteriaId("");
      setCriteriaLoading(false);
      resetCriteriaDraft();
      return;
    }
    let current = true;
    const baseEditRevision = lookupBaseEditRevision.current;
    setCriteriaLoading(true);
    listScreeningCriteria(questionId.trim()).then((result) => {
      if (!current) return;
      setCriteria(result.criteria);
      setSelectedCriteriaId((current) => result.criteria.some((item) => item.criteria_id === current) ? current : result.criteria[0]?.criteria_id ?? "");
      if (!result.criteria.length && criteriaEditRevision.current === baseEditRevision) resetCriteriaDraft();
    }).catch((caught: unknown) => {
      if (current) setError(errorMessage(caught));
    }).finally(() => {
      if (current) setCriteriaLoading(false);
    });
    return () => { current = false; };
  }, [questionId]);

  useEffect(() => {
    if (!selectedCriteria) return;
    setTitle(selectedCriteria.title);
    setScope(selectedCriteria.scope);
    setInclusionText(selectedCriteria.inclusion_criteria.map((item) => item.text).join("\n"));
    setExclusionText(selectedCriteria.exclusion_criteria.map((item) => item.text).join("\n"));
    setNotes(selectedCriteria.notes);
    setDispositions(allCriteria(selectedCriteria).map((item) => ({ criterion_id: item.criterion_id, disposition: "uncertain", rationale: "" })));
  }, [selectedCriteria]);

  useEffect(() => {
    Promise.all([getAgentRegistry(), listAgentTasks(100)]).then(([nextRegistry, result]) => {
      setRegistry(nextRegistry);
      setTasks(result.tasks.filter((item) => SCREENING_TASKS.has(item.task_kind)));
    }).catch((caught: unknown) => setError(errorMessage(caught)));
  }, []);

  useEffect(() => {
    if (!task) return;
    getAgentTask(task.task_id).then(async (result) => {
      const current = result.current_task;
      setTask(current);
      if (current.status === "submitted" || current.status === "approved") setPreview(await getAgentPreview(current.task_id));
    }).catch((caught: unknown) => setError(errorMessage(caught)));
  }, [task?.task_id]);

  const taskDefinition = registry?.task_kinds.find((item) => item.task_kind === (objectMode === "criteria" ? "question_screening_criteria_proposal" : "question_screening_decision_proposal"));
  const candidate = preview?.candidate ?? null;
  const approvalBlocked = Boolean(candidate?.approval_blocked) || candidate?.outcome === "uncertain";
  const canDecide = task?.status === "submitted";

  async function settle() {
    if (!onCatalogStatus || !onHealth) return;
    for (let attempt = 0; attempt < 80; attempt += 1) {
      const [health, catalog] = await Promise.all([getHealth(), getCatalogStatus()]);
      onHealth(health);
      onCatalogStatus(catalog);
      if (!["running", "building"].includes(health.operation.state) && catalog.projection_state !== "stale") return;
      await new Promise((resolve) => setTimeout(resolve, 150));
    }
  }

  async function run<T>(action: () => Promise<T>, message: string): Promise<T | null> {
    setMutating(true);
    setError("");
    setNotice("");
    try {
      const result = await action();
      setNotice(message);
      await settle();
      return result;
    } catch (caught) {
      setError(errorMessage(caught));
      return null;
    } finally {
      setMutating(false);
    }
  }

  async function saveCriteria() {
    const include = lines(inclusionText);
    const exclude = lines(exclusionText);
    if (!questionId.trim() || !title.trim() || !scope.trim() || include.length + exclude.length === 0) return;
    const result = await run(() => promoteScreeningCriteria({
      ...(selectedCriteria ? { criteria_id: selectedCriteria.criteria_id, expected_revision_id: selectedCriteria.revision_id } : {}),
      question_id: questionId.trim(), title: title.trim(), scope: scope.trim(),
      inclusion_criteria: retainCriteria(include, selectedCriteria?.inclusion_criteria ?? []),
      exclusion_criteria: retainCriteria(exclude, selectedCriteria?.exclusion_criteria ?? []),
      notes: notes.trim(), status: "active",
    }), "筛选标准已保存");
    if (result?.criteria) {
      setCriteria([result.criteria]);
      setSelectedCriteriaId(result.criteria.criteria_id);
    }
  }

  async function saveDecision() {
    if (!selectedCriteria || !questionId.trim() || !paperId.trim() || !rationale.trim() || dispositions.some((item) => !item.rationale.trim())) return;
    const existing = (await listScreeningDecisions(questionId.trim(), paperId.trim())).decisions[0];
    await run(() => promoteScreeningDecision({
      ...(existing ? { decision_id: existing.decision_id, expected_revision_id: existing.revision_id } : {}),
      question_id: questionId.trim(), paper_id: paperId.trim(), outcome,
      criteria_revision_id: selectedCriteria.revision_id, criteria_digest: selectedCriteria.criteria_digest,
      criterion_dispositions: dispositions, basis_scope: basisScope, rationale: rationale.trim(),
      known_limitations: lines(limitations),
    }), "Question-specific decision 已保存");
  }

  async function createTask() {
    if (!questionId.trim() || !proposalGoal.trim() || (objectMode === "decision" && (!paperId.trim() || !selectedCriteria))) return;
    const required = taskDefinition?.required_content_classes ?? [];
    const optional = objectMode === "decision" && includePaperCard ? ["paper_card_content"] : [];
    const result = await run(() => objectMode === "criteria"
      ? createScreeningCriteriaProposal({ question_id: questionId.trim(), criteria_id: selectedCriteria?.criteria_id ?? null, proposal_goal: proposalGoal.trim(), executor_id: executorId, approved_content_classes: [...required], idempotency_key: crypto.randomUUID() })
      : createScreeningDecisionProposal({ question_id: questionId.trim(), paper_id: paperId.trim(), basis_scope: includePaperCard ? "paper_card" : "metadata", include_paper_card: includePaperCard, executor_id: executorId, approved_content_classes: [...required, ...optional], idempotency_key: crypto.randomUUID() }), "Screening Task 已创建");
    if (result?.task) {
      setTask(result.task);
      setTasks((current) => [result.task, ...current.filter((item) => item.task_id !== result.task.task_id)]);
      clearAgentArtifacts();
    }
  }

  async function inspect() {
    if (!task) return;
    const result = await run(() => inspectAgentHandoff(task.task_id, { ...expected(task), executor_id: executorId }), "Payload 已检查");
    if (result) setInspection(result);
  }

  async function prepare() {
    if (!task) return;
    const result = await run(() => prepareAgentHandoff(task.task_id, { ...expected(task), executor_id: executorId }), "Prompt 已生成");
    if (result) { setHandoff(result); setTask(result.task); }
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

  async function submit() {
    if (!task) return;
    let parsed: { [key: string]: JsonValue };
    try { parsed = JSON.parse(resultText) as { [key: string]: JsonValue }; } catch { setError("Agent JSON 不是有效 JSON"); return; }
    const result = await run(() => submitAgentResult(task.task_id, { ...expected(task), result: parsed }), "Agent 候选已暂存");
    if (result) { setTask(result.task); setPreview(await getAgentPreview(result.task.task_id)); }
  }

  async function approve() {
    if (!task || approvalBlocked) return;
    const result = await run(() => approveScreeningProposal(task.task_id, expected(task)), "Screening revision 已批准");
    if (result) { setTask(result.task); setPreview(await getAgentPreview(result.task.task_id)); }
  }

  async function revise() {
    if (!task || !feedback.trim()) return;
    const result = await run(() => requestAgentRevision(task.task_id, { ...expected(task), feedback: feedback.trim() }), "已创建修订 Task");
    if (result?.successor_task) { setTask(result.successor_task); clearAgentArtifacts(); }
  }

  async function reject() {
    if (!task) return;
    const result = await run(() => rejectAgentResult(task.task_id, { ...expected(task), reason_code: "user_rejected" }), "Agent 候选已拒绝");
    if (result) setTask(result.task);
  }

  function clearAgentArtifacts() { setInspection(null); setHandoff(null); setPreview(null); setResultText("{}"); setFeedback(""); }

  return (
    <section className="screening-view" aria-labelledby="screening-title">
      <header className="view-heading">
        <div><p className="section-kicker">QUESTION-SPECIFIC SCREENING</p><h2 id="screening-title">问题筛选</h2></div>
        <span className={`operation-chip operation-${mutating ? "running" : "idle"}`}>{mutating ? "processing" : "optional"}</span>
      </header>
      {error ? <div className="error-banner" role="alert">{error}</div> : null}
      {notice ? <div className="notice-banner" role="status">{notice}</div> : null}

      <div className="screening-toolbar">
        <div className="route-segments" role="tablist" aria-label="筛选对象">
          <button type="button" className={objectMode === "criteria" ? "segment-active" : ""} onClick={() => setObjectMode("criteria")}>纳排标准</button>
          <button type="button" className={objectMode === "decision" ? "segment-active" : ""} onClick={() => setObjectMode("decision")}>论文决策</button>
        </div>
        <div className="route-segments" role="tablist" aria-label="执行方式">
          <button type="button" className={workMode === "manual" ? "segment-active" : ""} onClick={() => setWorkMode("manual")}>人工填写</button>
          <button type="button" className={workMode === "agent" ? "segment-active" : ""} onClick={() => setWorkMode("agent")}>Agent 候选</button>
        </div>
      </div>

      <div className="screening-grid">
        <section className="screening-context-pane">
          <div className="subsection-heading"><div><h3>Question 范围</h3><span>Library inclusion 不受此处影响</span></div><ListChecks size={17} /></div>
          <label htmlFor="screening-question">Question ID</label>
          <input id="screening-question" value={questionId} onChange={(event) => {
            lookupBaseEditRevision.current = criteriaEditRevision.current;
            setQuestionId(event.target.value);
            setCriteria([]);
            setSelectedCriteriaId("");
            setCriteriaLoading(Boolean(event.target.value.trim()));
            setDispositions([]);
          }} spellCheck={false} disabled={mutating} />
          <label htmlFor="screening-criteria">当前标准</label>
          <select id="screening-criteria" value={selectedCriteria?.criteria_id ?? ""} onChange={(event) => setSelectedCriteriaId(event.target.value)} disabled={mutating || !criteria.length}>
            {!criteria.length ? <option value="">尚无 active criteria</option> : null}
            {criteria.map((item) => <option key={item.criteria_id} value={item.criteria_id}>{item.title}</option>)}
          </select>
          {objectMode === "decision" ? <><label htmlFor="screening-paper">Paper ID</label><input id="screening-paper" value={paperId} onChange={(event) => setPaperId(event.target.value)} spellCheck={false} disabled={mutating} /></> : null}
          {selectedCriteria ? <pre className="screening-json">{JSON.stringify(selectedCriteria, null, 2)}</pre> : <div className="compact-empty">当前 Question 尚无筛选标准</div>}
        </section>

        {workMode === "manual" ? (
          <section className="screening-editor-pane">
            <div className="subsection-heading"><div><h3>{objectMode === "criteria" ? "标准 revision" : "Question-specific decision"}</h3><span>explicit user authority</span></div><Save size={17} /></div>
            {objectMode === "criteria" ? <>
              <label htmlFor="screening-title-field">标题</label><input id="screening-title-field" value={title} onChange={(event) => { criteriaEditRevision.current += 1; setTitle(event.target.value); }} maxLength={200} disabled={mutating || criteriaLoading} />
              <label htmlFor="screening-scope">范围</label><textarea id="screening-scope" value={scope} onChange={(event) => { criteriaEditRevision.current += 1; setScope(event.target.value); }} maxLength={4000} disabled={mutating || criteriaLoading} />
              <label htmlFor="screening-inclusion">纳入标准（每行一条）</label><textarea id="screening-inclusion" value={inclusionText} onChange={(event) => { criteriaEditRevision.current += 1; setInclusionText(event.target.value); }} disabled={mutating || criteriaLoading} />
              <label htmlFor="screening-exclusion">排除标准（每行一条）</label><textarea id="screening-exclusion" value={exclusionText} onChange={(event) => { criteriaEditRevision.current += 1; setExclusionText(event.target.value); }} disabled={mutating || criteriaLoading} />
              <label htmlFor="screening-notes">备注</label><textarea id="screening-notes" value={notes} onChange={(event) => { criteriaEditRevision.current += 1; setNotes(event.target.value); }} maxLength={4000} disabled={mutating || criteriaLoading} />
              <button className="start-button screening-save" type="button" onClick={() => void saveCriteria()} disabled={mutating || criteriaLoading || !questionId.trim() || !title.trim() || !scope.trim()}><Save size={16} />保存标准</button>
            </> : <>
              <label htmlFor="screening-outcome">结果</label><select id="screening-outcome" value={outcome} onChange={(event) => setOutcome(event.target.value as typeof outcome)} disabled={mutating}><option value="included">included</option><option value="excluded">excluded</option></select>
              <label htmlFor="screening-basis">判断依据</label><select id="screening-basis" value={basisScope} onChange={(event) => setBasisScope(event.target.value as typeof basisScope)} disabled={mutating}><option value="metadata">metadata</option><option value="available_abstract">available_abstract</option><option value="paper_card">paper_card</option><option value="user_full_text_review">user_full_text_review</option><option value="mixed">mixed</option></select>
              <div className="screening-dispositions">{dispositions.map((item, index) => <div className="screening-disposition" key={item.criterion_id}><strong>{allCriteria(selectedCriteria)[index]?.text ?? item.criterion_id}</strong><select aria-label={`Disposition ${index + 1}`} value={item.disposition} onChange={(event) => setDispositions((current) => current.map((value, position) => position === index ? { ...value, disposition: event.target.value as ScreeningDisposition["disposition"] } : value))}><option value="met">met</option><option value="not_met">not_met</option><option value="not_applicable">not_applicable</option><option value="uncertain">uncertain</option></select><input aria-label={`Rationale ${index + 1}`} value={item.rationale} onChange={(event) => setDispositions((current) => current.map((value, position) => position === index ? { ...value, rationale: event.target.value } : value))} placeholder="Rationale" /></div>)}</div>
              <label htmlFor="screening-rationale">总体理由</label><textarea id="screening-rationale" value={rationale} onChange={(event) => setRationale(event.target.value)} maxLength={4000} disabled={mutating} />
              <label htmlFor="screening-limitations">已知限制（每行一条）</label><textarea id="screening-limitations" value={limitations} onChange={(event) => setLimitations(event.target.value)} disabled={mutating} />
              <button className="start-button screening-save" type="button" onClick={() => void saveDecision()} disabled={mutating || !selectedCriteria || !paperId.trim() || !rationale.trim()}><ShieldCheck size={16} />保存决策</button>
            </>}
          </section>
        ) : (
          <section className="screening-editor-pane">
            <div className="subsection-heading"><div><h3>External Agent Handoff</h3><span>{taskDefinition?.result_contract ?? "unavailable"}</span></div><Send size={17} /></div>
            <label htmlFor="screening-goal">Proposal goal</label><textarea id="screening-goal" value={proposalGoal} onChange={(event) => setProposalGoal(event.target.value)} maxLength={2000} disabled={mutating} />
            <label htmlFor="screening-executor">External Agent</label><select id="screening-executor" value={executorId} onChange={(event) => setExecutorId(event.target.value as typeof executorId)} disabled={mutating}><option value="codex_cli">Codex CLI</option><option value="claude_code_cli">Claude Code CLI</option></select>
            {objectMode === "decision" ? <label className="screening-card-toggle"><input type="checkbox" checked={includePaperCard} onChange={(event) => setIncludePaperCard(event.target.checked)} disabled={mutating} /><span>加入 Paper Card content</span></label> : null}
            <button className="start-button screening-save" type="button" onClick={() => void createTask()} disabled={mutating || !proposalGoal.trim() || !questionId.trim()}><Send size={16} />创建 Screening Task</button>
            <label htmlFor="screening-task">Task</label><select id="screening-task" value={task?.task_id ?? ""} onChange={(event) => { const selected = tasks.find((item) => item.task_id === event.target.value) ?? null; setTask(selected); clearAgentArtifacts(); }} disabled={mutating}><option value="">选择 Task</option>{tasks.map((item) => <option key={item.task_id} value={item.task_id}>{item.task_kind} · {item.status}</option>)}</select>
            <div className="screening-command-row"><button type="button" onClick={() => void inspect()} disabled={!task || mutating}>预览 Payload</button><button type="button" onClick={() => void prepare()} disabled={!task || mutating}>生成 Prompt</button><button type="button" onClick={() => void copyPrompt()} disabled={!handoff}><Clipboard size={16} />复制</button><button type="button" onClick={() => void exportPrompt()} disabled={!handoff}><Download size={16} />导出 Task</button></div>
            {inspection ? <pre className="screening-json">{JSON.stringify(inspection.handoff_preview.payload, null, 2)}</pre> : null}
            {handoff ? <pre className="screening-json">{JSON.stringify(handoff.handoff, null, 2)}</pre> : null}
            <label htmlFor="screening-agent-json">Agent JSON</label><textarea id="screening-agent-json" value={resultText} onChange={(event) => setResultText(event.target.value)} spellCheck={false} disabled={mutating || !task} />
            <label className="file-command" htmlFor="screening-agent-file"><FileJson size={16} />JSON 文件</label><input id="screening-agent-file" className="visually-hidden" type="file" accept="application/json,.json" onChange={(event) => { const file = event.target.files?.[0]; if (file) file.text().then(setResultText); }} />
            <button type="button" onClick={() => void submit()} disabled={mutating || !task || task.status !== "leased"}>导入结果</button>
          </section>
        )}

        <section className="screening-preview-pane">
          <div className="subsection-heading"><div><h3>候选预览</h3><span>{task?.status ?? "no active task"}</span></div></div>
          {candidate ? <><pre className="screening-json">{JSON.stringify(candidate, null, 2)}</pre>{approvalBlocked ? <div className="error-banner" role="alert">Approval blocked</div> : null}</> : <div className="compact-empty">尚无暂存候选</div>}
          {workMode === "agent" ? <>
            <label htmlFor="screening-feedback">Revision feedback</label><textarea id="screening-feedback" value={feedback} onChange={(event) => setFeedback(event.target.value)} maxLength={4000} disabled={!canDecide || mutating} />
            <div className="screening-command-row"><button className="start-button" type="button" onClick={() => void approve()} disabled={!canDecide || approvalBlocked || mutating}><ShieldCheck size={16} />批准 revision</button><button type="button" onClick={() => void revise()} disabled={!canDecide || !feedback.trim() || mutating}>请求修订</button><button className="secondary-button danger-text" type="button" onClick={() => void reject()} disabled={!canDecide || mutating}><XCircle size={16} />拒绝</button></div>
          </> : <pre className="screening-json">{JSON.stringify({ question_id: questionId || null, paper_id: objectMode === "decision" ? paperId || null : null, selected_criteria_revision: selectedCriteria?.revision_id ?? null }, null, 2)}</pre>}
        </section>
      </div>
    </section>
  );
}

function lines(value: string): string[] { return [...new Set(value.split("\n").map((item) => item.trim()).filter(Boolean))]; }
function retainCriteria(values: string[], existing: Array<{ criterion_id: string; text: string }>) { return values.map((text, index) => existing[index] ? { criterion_id: existing[index].criterion_id, text } : { text }); }
function allCriteria(criteria: ScreeningCriteria | null) { return criteria ? [...criteria.inclusion_criteria, ...criteria.exclusion_criteria] : []; }
function expected(task: AgentTaskProjection) { return { expected_state_id: task.state_id, expected_state_digest: task.state_digest }; }
function errorMessage(caught: unknown): string { if (caught instanceof ApiError) return `${caught.code}: ${caught.message}`; return caught instanceof Error ? caught.message : "筛选操作未完成"; }
