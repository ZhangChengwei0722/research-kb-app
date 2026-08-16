import { ChangeEvent, useEffect, useRef, useState } from "react";
import {
  CheckCircle2,
  CircleAlert,
  Clock3,
  FileText,
  Inbox,
  LoaderCircle,
  Play,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  Upload,
  X,
} from "lucide-react";
import {
  ApiError,
  approveTrustedParse,
  cancelIntakeJob,
  decideIntakeSourceAdequacyResolution,
  getCatalogStatus,
  getHealth,
  getIntakeJob,
  getIntakeSourceAdequacyResolution,
  listInboxCandidates,
  listIntakeJobs,
  openIntakeSourceAdequacyReview,
  prepareTrustedParse,
  resumeIntakeJob,
  startInboxIntake,
  startUploadIntake,
  type CatalogStatus,
  type HealthResult,
  type IntakeBibliography,
  type IntakeCandidate,
  type IntakeJobListFilter,
  type IntakeJobDetail,
  type IntakeSemanticRequest,
  type IntakeSourceAdequacyResolutionContext,
  type PipelineProjection,
  type TrustedParsePreparation,
} from "../api";

type IngressMode = "upload" | "inbox";
export type RoutePreset = "primary" | "review" | "mixed" | "undecided";

type ProcessingViewProps = {
  onCatalogStatus: (status: CatalogStatus) => void;
  onHealth: (health: HealthResult) => void;
};

const ROUTES: ReadonlyArray<{ id: RoutePreset; label: string }> = [
  { id: "primary", label: "原始研究" },
  { id: "review", label: "综述" },
  { id: "mixed", label: "混合型" },
  { id: "undecided", label: "暂不确定" },
];
const POLL_INTERVAL_MS = 250;
const MAX_POLL_ATTEMPTS = 120;
const DETERMINISTIC_INTAKE_ROUTE = "local_source";
const DETERMINISTIC_INTAKE_DEPTH = "semantic_gate";
const DETERMINISTIC_INTAKE_FILTER: IntakeJobListFilter = {
  requested_route: DETERMINISTIC_INTAKE_ROUTE,
  requested_depth: DETERMINISTIC_INTAKE_DEPTH,
};

const PIPELINE_STATUS_LABELS: Readonly<Record<string, string>> = {
  created: "已创建",
  queued: "排队中",
  pending: "等待处理",
  running: "处理中",
  processing: "处理中",
  building: "构建中",
  waiting_user: "等待用户",
  waiting_agent: "等待 Agent",
  waiting_source: "等待来源",
  paused: "已暂停",
  recovering: "恢复中",
  completed: "已完成",
  completed_with_findings: "完成但有待处理项",
  complete: "已完成",
  succeeded: "已成功",
  failed: "失败",
  cancelled: "已取消",
  canceled: "已取消",
  stopped: "已停止",
};

const PIPELINE_NODE_LABELS: Readonly<Record<string, string>> = {
  source_intake: "来源接收",
  source_check: "来源检查",
  registry: "文献登记",
  source_association: "来源关联",
  deterministic_trunk: "确定性处理",
  parse: "文档解析",
  source_adequacy: "来源充分性",
  semantic_route: "语义路线分流",
  primary_semantic_gate: "原始研究语义闸门",
  review_semantic_gate: "综述语义闸门",
  review_semantic_gate_mixed_document: "混合文献语义闸门",
  trusted_parse_authority_primary: "原始研究受监督解析授权",
  trusted_parse_authority_review: "综述受监督解析授权",
  trusted_parse_authority_undecided: "待定路线受监督解析授权",
};

const PIPELINE_WAIT_REASON_LABELS: Readonly<Record<string, string>> = {
  route_ambiguous: "文献路线待确认",
  authority_required: "需要用户授权",
  source_selection_required: "需要选择来源文件",
  source_incomplete: "来源不完整",
  source_missing: "缺少来源文件",
  source_inaccessible: "来源文件不可访问",
  source_changed: "来源文件已变化",
  parse_failed: "解析失败",
  supplement_missing: "缺少补充材料",
  source_adequacy_uncertain: "来源充分性待确认",
  source_adequacy_inadequate: "来源不足",
  source_adequacy_stale: "来源充分性已过期",
  ocr_required: "需要 OCR",
  layout_parse_required: "需要版面解析",
  reparse_required: "需要重新解析",
  user_paused: "用户已暂停",
  transaction_recovery: "正在恢复事务",
};

const APP_DIAGNOSTIC_LABELS: Readonly<Record<string, string>> = {
  "RKBAPP-INTAKE-FILTER": "处理任务筛选参数无效，请刷新页面后重试",
  "RKBAPP-INTAKE-FILTER-PROJECTION": "任务索引尚未就绪，请等待重建完成后刷新任务",
  "RKBAPP-INTAKE-FILTER-DETAIL": "任务状态已变化，请刷新任务后重试",
  "RKBAPP-SOURCE-REVIEW-REQUIRED": "请先打开原文并完成本次来源复核",
  "RKBAPP-SOURCE-REVIEW-CONFIRMATION": "当前操作不能使用这次来源复核确认",
  "RKBAPP-SOURCE-ADEQUACY-ACTION": "当前来源充分性操作不受支持，请刷新任务",
};

const INGRESS_LABELS: Readonly<Record<string, string>> = {
  upload: "上传",
  watched_inbox: "收件箱",
};

const SOURCE_ADEQUACY_LABELS: Readonly<Record<string, string>> = {
  basic_review_memory: "基础综述阅读记忆",
  basic_paper_card: "基础论文理解卡",
  basic_paper_understanding: "基础论文理解",
  complete_reading: "完整阅读",
  continuous_text_citation: "连续文本引用",
  figure_table_evidence_extraction: "图表证据提取",
  formula_or_layout_sensitive_analysis: "公式或版面敏感分析",
  supplementary_material_analysis: "补充材料分析",
  allowed: "允许",
  blocked: "受阻",
  yes: "适合",
  no: "不适合",
  uncertain: "需确认",
  pass: "通过",
  fail: "失败",
  current: "当前有效",
  stale: "已过期",
  available: "可用",
  inaccessible: "不可访问",
  not_required: "无需处理",
  review_required: "需要确认",
  accepted_continuation_required: "等待继续",
  continuation_in_progress: "正在继续",
  continued: "已继续",
  remediation_required: "需要重新处理",
  accepted_refresh_required: "等待刷新",
  remediation_refresh_required: "等待重新处理",
  not_resolvable: "无法处理",
  loading: "读取中",
  unavailable: "不可用",
  machine: "系统判定",
  user: "用户确认",
  reading_order_uncertain: "阅读顺序需确认",
  review_source: "复核原文",
  acquire_or_parse_supplement: "获取或解析补充材料",
  review_reading_order: "复核阅读顺序",
  run_layout_aware_parse: "进行版面感知解析",
  "The active main source is readable.": "当前主来源可读取。",
  "The active main source is not readable.": "当前主来源不可读取。",
  "The active main source digest matches.": "当前主来源摘要匹配。",
  "The active main source digest does not match.": "当前主来源摘要不匹配。",
  "The main source required for main-text operations is present.": "主文本操作所需的主来源已存在。",
  "All active pages share one parse run.": "所有当前页面共用同一次解析运行。",
  "The parser profile is registered and reproducible.": "解析器配置已注册且可复现。",
  "The parser profile is not registered.": "解析器配置未注册。",
  "Extracted text is present.": "已存在提取文本。",
  "No extracted text is present.": "没有提取到文本。",
  "Parsed pages are contiguous and contain text.": "解析页面连续且包含文本。",
  "Page coverage or text coverage is incomplete.": "页面覆盖或文本覆盖不完整。",
  "Page locators are unique and reproducible.": "页面定位器唯一且可复现。",
  "One or more page locators are not reproducible.": "一个或多个页面定位器不可复现。",
  "The parser profile guarantees deterministic reading order.": "解析器配置保证阅读顺序确定。",
  "Reading order requires review or a layout-aware parse.": "阅读顺序需要复核或进行版面感知解析。",
  "Figure/table context is available.": "图表语境可用。",
  "The active parser does not establish figure/table context.": "当前解析器未建立图表语境。",
  "Formula/layout context is available.": "公式或版面语境可用。",
  "The active parser does not establish formula/layout context.": "当前解析器未建立公式或版面语境。",
  "A current supplementary source is available.": "当前有可用的补充来源。",
  "No current supplementary source is available.": "当前没有可用的补充来源。",
  "Supplementary parsed content is available.": "补充解析内容可用。",
  "The active parse does not include supplementary content.": "当前解析不包含补充内容。",
  "All deterministic checks for this capability passed.": "该能力的确定性检查均已通过。",
  "User accepted the recorded non-hard uncertainty.": "用户已接受记录的非硬性不确定性。",
  "User requires remediation before this capability can be used.": "用户要求先完成处理，之后才能使用此能力。",
};

export function isDeterministicIntakeJob(job: PipelineProjection): boolean {
  return job.requested_route === DETERMINISTIC_INTAKE_ROUTE
    && job.requested_depth === DETERMINISTIC_INTAKE_DEPTH;
}

function filterDeterministicIntakeJobs(jobs: PipelineProjection[]): PipelineProjection[] {
  return jobs.filter(isDeterministicIntakeJob);
}

function sourceAdequacyLabel(value: string): string {
  if (SOURCE_ADEQUACY_LABELS[value]) return SOURCE_ADEQUACY_LABELS[value];
  return /[\u3400-\u9fff]/u.test(value) ? value : `未识别值（${value}）`;
}

function operationalLabel(
  value: string,
  labels: Readonly<Record<string, string>>,
  kind: string,
): string {
  return labels[value] ?? `未识别${kind}（${value}）`;
}

type OperationalCodeProps = {
  value: string;
  labels: Readonly<Record<string, string>>;
  kind: string;
  className?: string;
};

function OperationalCode({ value, labels, kind, className }: OperationalCodeProps) {
  return <span className={className} title={value}>{operationalLabel(value, labels, kind)}</span>;
}

type TrustedParseLease = {
  jobId: string;
  stateId: string;
  stateDigest: string;
  preparation: TrustedParsePreparation;
};

type IntakeSourceReviewConfirmation = {
  jobId: string;
  stateId: string;
  stateDigest: string;
  basisProfileId: string;
  confirmationId: string;
};

export function routeFieldsForPreset(preset: RoutePreset): Omit<IntakeSemanticRequest, "bibliography"> {
  if (preset === "primary") {
    return { requested_operation: "basic_paper_card", document_route: "primary", route_reason: null };
  }
  if (preset === "review") {
    return { requested_operation: "basic_review_memory", document_route: "review", route_reason: null };
  }
  if (preset === "mixed") {
    return {
      requested_operation: "basic_review_memory",
      document_route: "review",
      route_reason: "mixed_document",
    };
  }
  return { requested_operation: "basic_paper_card", document_route: null, route_reason: null };
}

export function isTrustedParseEligible(detail: IntakeJobDetail | null): boolean {
  const pipeline = detail?.pipeline;
  return Boolean(
    pipeline
    && pipeline.status === "waiting_user"
    && pipeline.wait_reason === "authority_required"
    && pipeline.current_node.startsWith("trusted_parse_authority_"),
  );
}

export function isIntakeSourceAdequacyResolutionEligible(detail: IntakeJobDetail | null): boolean {
  const pipeline = detail?.pipeline;
  const waitReason = pipeline?.wait_reason;
  return Boolean(
    pipeline
    && pipeline.current_node === "source_adequacy"
    && (pipeline.status === "waiting_user" || pipeline.status === "waiting_source")
    && waitReason
    && [
      "source_adequacy_uncertain",
      "source_adequacy_inadequate",
      "source_adequacy_stale",
      "source_incomplete",
      "ocr_required",
      "reparse_required",
    ].includes(waitReason),
  );
}

export function ProcessingView({ onCatalogStatus, onHealth }: ProcessingViewProps) {
  const [ingressMode, setIngressMode] = useState<IngressMode>("upload");
  const [routePreset, setRoutePreset] = useState<RoutePreset>("primary");
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [doi, setDoi] = useState("");
  const [year, setYear] = useState("");
  const [candidates, setCandidates] = useState<IntakeCandidate[]>([]);
  const [selectedCandidate, setSelectedCandidate] = useState("");
  const [jobs, setJobs] = useState<PipelineProjection[]>([]);
  const [selectedJobId, setSelectedJobId] = useState("");
  const [detail, setDetail] = useState<IntakeJobDetail | null>(null);
  const [loadingInbox, setLoadingInbox] = useState(false);
  const [loadingJobs, setLoadingJobs] = useState(false);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [mutating, setMutating] = useState(false);
  const [trustedMutating, setTrustedMutating] = useState(false);
  const [trustedLease, setTrustedLease] = useState<TrustedParseLease | null>(null);
  const [sourceResolution, setSourceResolution] = useState<IntakeSourceAdequacyResolutionContext | null>(null);
  const [sourceResolutionLoading, setSourceResolutionLoading] = useState(false);
  const [sourceResolutionMutating, setSourceResolutionMutating] = useState(false);
  const [sourceResolutionRefresh, setSourceResolutionRefresh] = useState(0);
  const [sourceReviewConfirmation, setSourceReviewConfirmation] = useState<IntakeSourceReviewConfirmation | null>(null);
  const [cancelPending, setCancelPending] = useState(false);
  const [pollRevision, setPollRevision] = useState(0);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const selectedJobIdRef = useRef("");
  const pollingJobIdRef = useRef("");
  const jobsBeforeStartRef = useRef<Set<string> | null>(null);
  const jobListRequestRef = useRef(0);
  const trustedRequestIdRef = useRef(0);

  useEffect(() => {
    selectedJobIdRef.current = selectedJobId;
  }, [selectedJobId]);

  useEffect(() => {
    trustedRequestIdRef.current += 1;
    setTrustedLease(null);
    setTrustedMutating(false);
    setSourceResolution(null);
    setSourceResolutionLoading(false);
    setSourceResolutionMutating(false);
    setSourceReviewConfirmation(null);
    setCancelPending(false);
  }, [selectedJobId]);

  function applyDetail(nextDetail: IntakeJobDetail) {
    setDetail(nextDetail);
    setJobs((current) => current.map((job) => (
      job.job_id === nextDetail.pipeline.job_id ? nextDetail.pipeline : job
    )));
    setTrustedLease((current) => {
      if (!current) return null;
      const pipeline = nextDetail.pipeline;
      return isTrustedParseEligible(nextDetail)
        && current.jobId === pipeline.job_id
        && current.stateId === pipeline.state_id
        && current.stateDigest === pipeline.state_digest
        ? current
        : null;
    });
    setSourceReviewConfirmation((current) => {
      if (!current) return null;
      const pipeline = nextDetail.pipeline;
      return isIntakeSourceAdequacyResolutionEligible(nextDetail)
        && current.jobId === pipeline.job_id
        && current.stateId === pipeline.state_id
        && current.stateDigest === pipeline.state_digest
        ? current
        : null;
    });
    if (!isIntakeSourceAdequacyResolutionEligible(nextDetail)) {
      setSourceResolution(null);
    }
  }

  useEffect(() => {
    let current = true;
    const jobsRequestId = ++jobListRequestRef.current;
    async function load() {
      setLoadingInbox(true);
      setLoadingJobs(true);
      const [inboxResult, jobsResult] = await Promise.allSettled([
        listInboxCandidates(20, 5),
        listIntakeJobs(20, null, DETERMINISTIC_INTAKE_FILTER),
      ]);
      if (!current) return;
      const failures: string[] = [];
      if (inboxResult.status === "fulfilled") {
        setCandidates(inboxResult.value.candidates);
      } else {
        failures.push(errorMessage(inboxResult.reason));
      }
      if (jobsResult.status === "fulfilled" && jobsRequestId === jobListRequestRef.current) {
        const nextJobs = filterDeterministicIntakeJobs(jobsResult.value.jobs);
        setJobs(nextJobs);
        setSelectedJobId((selected) => {
          const nextSelected = selected || nextJobs[0]?.job_id || "";
          selectedJobIdRef.current = nextSelected;
          return nextSelected;
        });
      } else if (jobsResult.status === "rejected" && jobsRequestId === jobListRequestRef.current) {
        failures.push(errorMessage(jobsResult.reason));
      }
      setError(failures.join(" | "));
      setLoadingInbox(false);
      setLoadingJobs(false);
    }
    void load();
    return () => {
      current = false;
    };
  }, []);

  useEffect(() => {
    if (!selectedJobId) {
      setDetail(null);
      return;
    }
    let current = true;
    setLoadingDetail(true);
    getIntakeJob(selectedJobId)
      .then((result) => {
        if (current) applyDetail(result);
      })
      .catch((caught: unknown) => {
        if (current) setError(errorMessage(caught));
      })
      .finally(() => {
        if (current) setLoadingDetail(false);
      });
    return () => {
      current = false;
    };
  }, [selectedJobId]);

  useEffect(() => {
    if (!isIntakeSourceAdequacyResolutionEligible(detail)) {
      setSourceResolution(null);
      setSourceResolutionLoading(false);
      return;
    }
    const pipeline = detail!.pipeline;
    let current = true;
    setSourceResolutionLoading(true);
    getIntakeSourceAdequacyResolution(pipeline.job_id)
      .then((result) => {
        if (!current) return;
        if (
          result.job.job_id !== pipeline.job_id
          || result.job.state_id !== pipeline.state_id
          || result.job.state_digest !== pipeline.state_digest
        ) {
          setSourceResolution(null);
          setSourceReviewConfirmation(null);
          setError("来源充分性状态已变化，请刷新任务");
          return;
        }
        setSourceResolution(result);
        setSourceReviewConfirmation((confirmation) => (
          confirmation?.basisProfileId === result.basis_profile_id ? confirmation : null
        ));
      })
      .catch((caught: unknown) => {
        if (current) {
          setSourceResolution(null);
          setSourceReviewConfirmation(null);
          setError(errorMessage(caught));
        }
      })
      .finally(() => {
        if (current) setSourceResolutionLoading(false);
      });
    return () => {
      current = false;
    };
  }, [
    detail?.pipeline.current_node,
    detail?.pipeline.job_id,
    detail?.pipeline.state_digest,
    detail?.pipeline.state_id,
    detail?.pipeline.status,
    detail?.pipeline.wait_reason,
    sourceResolutionRefresh,
  ]);

  useEffect(() => {
    if (pollRevision === 0) return;
    let current = true;
    let timer = 0;
    let attempts = 0;

    async function poll() {
      attempts += 1;
      const healthResult = await Promise.resolve(getHealth()).then(
        (value) => ({ status: "fulfilled" as const, value }),
        (reason: unknown) => ({ status: "rejected" as const, reason }),
      );
      if (!current) return;

      const failures: string[] = [];
      let operationState = "failed";
      let preferredJobId = pollingJobIdRef.current || selectedJobIdRef.current;
      if (healthResult.status === "fulfilled") {
        onHealth(healthResult.value);
        operationState = healthResult.value.operation.state;
        const healthJobId = healthResult.value.operation.job_id;
        if (healthJobId) {
          pollingJobIdRef.current = healthJobId;
          preferredJobId = healthJobId;
        }
      } else {
        failures.push(errorMessage(healthResult.reason));
      }

      const operationActive = operationState === "running" || operationState === "building";
      const jobsRequestId = operationActive ? null : ++jobListRequestRef.current;
      const [jobsResult, catalogResult] = await Promise.allSettled([
        operationActive ? Promise.resolve(null) : listIntakeJobs(20, null, DETERMINISTIC_INTAKE_FILTER),
        getCatalogStatus(),
      ]);
      if (!current) return;

      let catalogState = "unknown";
      if (catalogResult.status === "fulfilled") {
        onCatalogStatus(catalogResult.value);
        catalogState = catalogResult.value.projection_state;
      } else {
        failures.push(errorMessage(catalogResult.reason));
      }
      if (
        jobsResult.status === "fulfilled"
        && jobsResult.value !== null
        && jobsRequestId === jobListRequestRef.current
      ) {
        const nextJobs = filterDeterministicIntakeJobs(jobsResult.value.jobs);
        setJobs(nextJobs);
        const jobsBeforeStart = jobsBeforeStartRef.current;
        if (!pollingJobIdRef.current && jobsBeforeStart) {
          const created = nextJobs.find((job) => !jobsBeforeStart.has(job.job_id));
          if (created) {
            pollingJobIdRef.current = created.job_id;
            preferredJobId = created.job_id;
          }
        }
        const nextSelected = nextJobs.some((job) => job.job_id === preferredJobId)
          ? preferredJobId
          : nextJobs[0]?.job_id || "";
        const selectionChanged = nextSelected !== selectedJobIdRef.current;
        selectedJobIdRef.current = nextSelected;
        setSelectedJobId(nextSelected);
        if (nextSelected && !selectionChanged) {
          try {
            const nextDetail = await getIntakeJob(nextSelected);
            if (current) applyDetail(nextDetail);
          } catch (caught) {
            failures.push(errorMessage(caught));
          }
        }
      } else if (jobsResult.status === "rejected" && jobsRequestId === jobListRequestRef.current) {
        failures.push(errorMessage(jobsResult.reason));
      }

      if (!current) return;
      const awaitingCatalog = operationState !== "failed" && catalogState === "stale";
      if ((operationActive || awaitingCatalog) && attempts < MAX_POLL_ATTEMPTS) {
        timer = window.setTimeout(poll, POLL_INTERVAL_MS);
      } else {
        pollingJobIdRef.current = "";
        jobsBeforeStartRef.current = null;
        setMutating(false);
        setCancelPending(false);
        if (operationActive || awaitingCatalog) {
          failures.push("自动刷新已暂停，请手动刷新任务");
          setNotice("任务仍在后台运行");
        } else {
          setNotice(operationState === "failed" ? "任务已停止" : "任务状态已更新");
        }
      }
      setError(failures.join(" | "));
    }

    void poll();
    return () => {
      current = false;
      window.clearTimeout(timer);
    };
  }, [pollRevision, onCatalogStatus, onHealth]);

  async function refreshInbox() {
    setLoadingInbox(true);
    setError("");
    try {
      const result = await listInboxCandidates(20, 5);
      setCandidates(result.candidates);
      setSelectedCandidate((selected) => (
        result.candidates.some((candidate) => candidate.candidate_token === selected) ? selected : ""
      ));
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setLoadingInbox(false);
    }
  }

  async function refreshJobs() {
    setLoadingJobs(true);
    setError("");
    const jobsRequestId = ++jobListRequestRef.current;
    try {
      const result = await listIntakeJobs(20, null, DETERMINISTIC_INTAKE_FILTER);
      if (jobsRequestId !== jobListRequestRef.current) return;
      const nextJobs = filterDeterministicIntakeJobs(result.jobs);
      setJobs(nextJobs);
      const nextSelected = nextJobs.some((job) => job.job_id === selectedJobIdRef.current)
        ? selectedJobIdRef.current
        : nextJobs[0]?.job_id || "";
      const selectionChanged = nextSelected !== selectedJobIdRef.current;
      selectedJobIdRef.current = nextSelected;
      setSelectedJobId(nextSelected);
      if (nextSelected && !selectionChanged) applyDetail(await getIntakeJob(nextSelected));
      setSourceResolutionRefresh((revision) => revision + 1);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setLoadingJobs(false);
    }
  }

  function selectFile(event: ChangeEvent<HTMLInputElement>) {
    const selected = event.target.files?.[0] ?? null;
    setFile(selected);
    setNotice("");
  }

  function bibliography(): IntakeBibliography {
    const parsedYear = year ? Number(year) : null;
    return {
      title: title.trim() || null,
      authors: [],
      year: Number.isInteger(parsedYear) ? parsedYear : null,
      doi: doi.trim() || null,
    };
  }

  async function start() {
    if (ingressMode === "upload" && !file) return;
    if (ingressMode === "inbox" && !selectedCandidate) return;
    setMutating(true);
    setError("");
    setNotice("");
    jobsBeforeStartRef.current = null;
    pollingJobIdRef.current = "";
    const baselineRequestId = ++jobListRequestRef.current;
    try {
      const baselineResult = await listIntakeJobs(20, null, DETERMINISTIC_INTAKE_FILTER);
      if (baselineRequestId !== jobListRequestRef.current) {
        throw new Error("stale intake-job baseline");
      }
      const baselineJobs = filterDeterministicIntakeJobs(baselineResult.jobs);
      jobsBeforeStartRef.current = new Set(baselineJobs.map((job) => job.job_id));
      setJobs(baselineJobs);
      const baselineSelectedJobId = selectedJobIdRef.current;
      const nextSelectedJobId = baselineJobs.some((job) => job.job_id === baselineSelectedJobId)
        ? baselineSelectedJobId
        : "";
      selectedJobIdRef.current = nextSelectedJobId;
      setSelectedJobId(nextSelectedJobId);

      const payload = {
        idempotency_key: crypto.randomUUID(),
        ...routeFieldsForPreset(routePreset),
        bibliography: bibliography(),
      };
      let accepted;
      if (ingressMode === "upload") {
        accepted = await startUploadIntake(file!, payload);
      } else {
        accepted = await startInboxIntake({
          ...payload,
          candidate_token: selectedCandidate,
          min_stable_age_seconds: 5,
        });
      }
      if (accepted.operation.job_id) {
        pollingJobIdRef.current = accepted.operation.job_id;
        const acceptedJob = baselineJobs.find((job) => job.job_id === accepted.operation.job_id);
        if (acceptedJob && isDeterministicIntakeJob(acceptedJob)) {
          selectedJobIdRef.current = acceptedJob.job_id;
          setSelectedJobId(acceptedJob.job_id);
        }
      }
      setNotice("任务已接收");
      setPollRevision((revision) => revision + 1);
    } catch (caught) {
      jobsBeforeStartRef.current = null;
      setMutating(false);
      setError(errorMessage(caught));
    }
  }

  async function resume() {
    if (!detail?.pipeline.can_resume) return;
    setMutating(true);
    setError("");
    setNotice("");
    try {
      const accepted = await resumeIntakeJob(detail.pipeline.job_id, {
        expected_state_id: detail.pipeline.state_id,
        expected_state_digest: detail.pipeline.state_digest,
        ...routeFieldsForPreset(routePreset),
        bibliography: bibliography(),
      });
      jobsBeforeStartRef.current = null;
      pollingJobIdRef.current = accepted.operation.job_id || detail.pipeline.job_id;
      setNotice("继续请求已接收");
      setPollRevision((revision) => revision + 1);
    } catch (caught) {
      setMutating(false);
      setError(errorMessage(caught));
    }
  }

  async function prepareTrusted() {
    if (!detail || !isTrustedParseEligible(detail) || trustedMutating) return;
    const pipeline = detail.pipeline;
    const requestId = trustedRequestIdRef.current + 1;
    trustedRequestIdRef.current = requestId;
    setTrustedMutating(true);
    setError("");
    setNotice("");
    try {
      const preparation = await prepareTrustedParse(pipeline.job_id, {
        expected_state_id: pipeline.state_id,
        expected_state_digest: pipeline.state_digest,
      });
      if (requestId !== trustedRequestIdRef.current) return;
      setTrustedLease({
        jobId: pipeline.job_id,
        stateId: pipeline.state_id,
        stateDigest: pipeline.state_digest,
        preparation,
      });
      setNotice("解析准备已就绪，请确认后批准");
    } catch (caught) {
      if (requestId === trustedRequestIdRef.current) {
        setTrustedLease(null);
        setError(trustedParseErrorMessage(caught));
      }
    } finally {
      if (requestId === trustedRequestIdRef.current) setTrustedMutating(false);
    }
  }

  async function approveTrusted() {
    if (!detail || !isTrustedParseEligible(detail) || !trustedLease || trustedMutating) return;
    if (trustedLeaseExpired(trustedLease.preparation)) {
      setTrustedLease(null);
      setError("解析准备已过期，请重新准备解析");
      return;
    }
    const requestId = trustedRequestIdRef.current + 1;
    trustedRequestIdRef.current = requestId;
    setTrustedMutating(true);
    setError("");
    setNotice("");
    try {
      const accepted = await approveTrustedParse(trustedLease.jobId, {
        lease_token: trustedLease.preparation.lease_token,
        aggregate_preview_digest: trustedLease.preparation.aggregate_preview_digest,
      });
      if (requestId !== trustedRequestIdRef.current) return;
      setTrustedLease(null);
      jobsBeforeStartRef.current = null;
      pollingJobIdRef.current = accepted.operation.job_id || trustedLease.jobId;
      setMutating(true);
      setNotice("解析请求已接收");
      setPollRevision((revision) => revision + 1);
    } catch (caught) {
      if (requestId === trustedRequestIdRef.current) {
        setTrustedLease(null);
        setError(trustedParseErrorMessage(caught));
      }
    } finally {
      if (requestId === trustedRequestIdRef.current) setTrustedMutating(false);
    }
  }

  async function openSourceReview() {
    if (!detail || !sourceResolution || sourceResolutionMutating) return;
    if (sourceResolution.resolution_state !== "review_required" || !sourceResolution.source_review_required) return;
    const pipeline = detail.pipeline;
    setSourceResolutionMutating(true);
    setError("");
    setNotice("");
    try {
      const opened = await openIntakeSourceAdequacyReview(pipeline.job_id, {
        expected_state_id: pipeline.state_id,
        expected_state_digest: pipeline.state_digest,
      });
      if (
        opened.job_id !== pipeline.job_id
        || opened.basis_profile_id !== sourceResolution.basis_profile_id
      ) {
        throw new Error("来源充分性状态已变化，请刷新任务");
      }
      setSourceReviewConfirmation({
        jobId: pipeline.job_id,
        stateId: pipeline.state_id,
        stateDigest: pipeline.state_digest,
        basisProfileId: sourceResolution.basis_profile_id,
        confirmationId: opened.confirmation.confirmation_id,
      });
      setNotice("原文已打开");
    } catch (caught) {
      setSourceReviewConfirmation(null);
      setError(errorMessage(caught));
    } finally {
      setSourceResolutionMutating(false);
    }
  }

  async function decideSourceAdequacy(action: "accept_uncertainty" | "remediation_required") {
    if (!detail || !sourceResolution || sourceResolutionMutating) return;
    const pipeline = detail.pipeline;
    const recovery = sourceResolution.resolution_state === "accepted_continuation_required"
      || sourceResolution.resolution_state === "continuation_in_progress";
    if (!sourceResolution.allowed_actions.includes(action) && !(action === "accept_uncertainty" && recovery)) return;
    const confirmation = sourceReviewConfirmation
      && sourceReviewConfirmation.jobId === pipeline.job_id
      && sourceReviewConfirmation.stateId === pipeline.state_id
      && sourceReviewConfirmation.stateDigest === pipeline.state_digest
      && sourceReviewConfirmation.basisProfileId === sourceResolution.basis_profile_id
      ? sourceReviewConfirmation
      : null;
    if (action === "accept_uncertainty" && !recovery && !confirmation) return;

    setSourceResolutionMutating(true);
    setError("");
    setNotice("");
    try {
      const accepted = await decideIntakeSourceAdequacyResolution(pipeline.job_id, {
        expected_state_id: pipeline.state_id,
        expected_state_digest: pipeline.state_digest,
        action,
        ...(action === "accept_uncertainty" && confirmation
          ? { confirmation_id: confirmation.confirmationId }
          : {}),
      });
      setSourceReviewConfirmation(null);
      jobsBeforeStartRef.current = null;
      pollingJobIdRef.current = accepted.operation.job_id || pipeline.job_id;
      setMutating(true);
      setNotice(action === "accept_uncertainty" ? "继续请求已接收" : "已标记需要重新处理");
      setPollRevision((revision) => revision + 1);
    } catch (caught) {
      setSourceReviewConfirmation(null);
      setError(errorMessage(caught));
    } finally {
      setSourceResolutionMutating(false);
    }
  }

  async function cancel() {
    if (!detail?.pipeline.can_cancel || cancelPending) return;
    trustedRequestIdRef.current += 1;
    setTrustedLease(null);
    setTrustedMutating(false);
    setCancelPending(true);
    setMutating(true);
    setError("");
    setNotice("");
    try {
      const accepted = await cancelIntakeJob(detail.pipeline.job_id, {
        expected_state_id: detail.pipeline.state_id,
        expected_state_digest: detail.pipeline.state_digest,
      });
      jobsBeforeStartRef.current = null;
      pollingJobIdRef.current = accepted.operation.job_id || detail.pipeline.job_id;
      setNotice(accepted.cancel_outcome === "too_late"
        ? "解析已进入提交阶段，取消未生效"
        : "取消请求已接收");
      setPollRevision((revision) => revision + 1);
    } catch (caught) {
      setMutating(false);
      setCancelPending(false);
      setError(errorMessage(caught));
    }
  }

  const startDisabled = mutating || (ingressMode === "upload" ? !file : !selectedCandidate);

  return (
    <section className="processing-view" aria-labelledby="processing-title">
      <header className="view-heading">
        <div>
          <p className="section-kicker">处理</p>
          <h2 id="processing-title">文献处理</h2>
        </div>
        <span className={`operation-chip operation-${mutating ? "running" : "idle"}`}>
          {mutating ? "处理中" : "就绪"}
        </span>
      </header>

      {error ? <div className="error-banner" role="alert">{error}</div> : null}
      {notice ? <div className="notice-banner" role="status">{notice}</div> : null}

      <div className="processing-grid">
        <section className="intake-pane" aria-labelledby="intake-heading">
          <div className="subsection-heading">
            <h3 id="intake-heading">新增文献</h3>
            <FileText size={17} aria-hidden="true" />
          </div>

          <div className="segmented-control" role="group" aria-label="来源方式">
            <button
              type="button"
              className={ingressMode === "upload" ? "segment-active" : ""}
              aria-pressed={ingressMode === "upload"}
              onClick={() => setIngressMode("upload")}
              disabled={mutating}
            >
              <Upload size={16} aria-hidden="true" />
              上传 PDF
            </button>
            <button
              type="button"
              className={ingressMode === "inbox" ? "segment-active" : ""}
              aria-pressed={ingressMode === "inbox"}
              onClick={() => setIngressMode("inbox")}
              disabled={mutating}
            >
              <Inbox size={16} aria-hidden="true" />
              收件箱
            </button>
          </div>

          {ingressMode === "upload" ? (
            <div className="source-selector">
              <label htmlFor="intake-pdf">PDF 文件</label>
              <input
                id="intake-pdf"
                className="file-input"
                type="file"
                accept="application/pdf,.pdf"
                onChange={selectFile}
                disabled={mutating}
              />
              <div className="selected-source" aria-live="polite">
                <FileText size={16} aria-hidden="true" />
                <span>{file?.name ?? "尚未选择文件"}</span>
                {file ? <strong>{formatBytes(file.size)}</strong> : null}
              </div>
            </div>
          ) : (
            <div className="source-selector">
              <div className="pane-toolbar">
                <span>{candidates.length} 个候选</span>
                <button
                  className="icon-button"
                  type="button"
                  title="刷新收件箱"
                  aria-label="刷新收件箱"
                  onClick={refreshInbox}
                  disabled={loadingInbox || mutating}
                >
                  <RefreshCw size={16} className={loadingInbox ? "spin" : ""} />
                </button>
              </div>
              <div className="inbox-list">
                {candidates.length ? candidates.map((candidate) => (
                  <label className="inbox-option" key={candidate.candidate_token}>
                    <input
                      type="radio"
                      name="inbox-candidate"
                      value={candidate.candidate_token}
                      checked={selectedCandidate === candidate.candidate_token}
                      onChange={() => setSelectedCandidate(candidate.candidate_token)}
                      disabled={mutating}
                    />
                    <span>{candidate.name}</span>
                    <strong>{formatBytes(candidate.size_bytes)}</strong>
                  </label>
                )) : <div className="compact-empty">收件箱没有稳定 PDF</div>}
              </div>
            </div>
          )}

          <fieldset className="route-fieldset" disabled={mutating}>
            <legend>文献路线</legend>
            <div className="segmented-control route-segments">
              {ROUTES.map((route) => (
                <button
                  key={route.id}
                  type="button"
                  className={routePreset === route.id ? "segment-active" : ""}
                  aria-pressed={routePreset === route.id}
                  onClick={() => setRoutePreset(route.id)}
                >
                  {route.label}
                </button>
              ))}
            </div>
          </fieldset>

          <div className="bibliography-grid">
            <div className="field-wide">
              <label htmlFor="intake-title">标题</label>
              <input id="intake-title" value={title} onChange={(event) => setTitle(event.target.value)} maxLength={2048} disabled={mutating} />
            </div>
            <div>
              <label htmlFor="intake-doi">DOI</label>
              <input id="intake-doi" value={doi} onChange={(event) => setDoi(event.target.value)} maxLength={512} disabled={mutating} />
            </div>
            <div>
              <label htmlFor="intake-year">年份</label>
              <input id="intake-year" type="number" min="0" max="9999" value={year} onChange={(event) => setYear(event.target.value)} disabled={mutating} />
            </div>
          </div>

          <button className="start-button" type="button" onClick={start} disabled={startDisabled}>
            {mutating ? <LoaderCircle size={17} className="spin" aria-hidden="true" /> : <Play size={17} aria-hidden="true" />}
            开始处理
          </button>
        </section>

        <section className="jobs-pane" aria-labelledby="jobs-heading">
          <div className="subsection-heading pane-heading">
            <div>
              <h3 id="jobs-heading">处理任务</h3>
              <span>{jobs.length} 条</span>
            </div>
            <button
              className="icon-button"
              type="button"
              title="刷新任务"
              aria-label="刷新任务"
              onClick={refreshJobs}
              disabled={loadingJobs || mutating}
            >
              <RefreshCw size={16} className={loadingJobs ? "spin" : ""} />
            </button>
          </div>
          <div className="job-table-wrap">
            <table className="job-table">
              <thead>
                <tr><th>任务 ID</th><th>状态</th><th>节点</th><th>更新时间</th></tr>
              </thead>
              <tbody>
                {jobs.map((job) => (
                  <tr key={job.job_id} className={selectedJobId === job.job_id ? "job-row-selected" : ""}>
                    <td><button type="button" className="job-link" onClick={() => {
                      selectedJobIdRef.current = job.job_id;
                      setSelectedJobId(job.job_id);
                    }}>{job.job_id}</button></td>
                    <td>
                      <span className={`status-badge job-status-${job.status}`}>
                        <OperationalCode value={job.status} labels={PIPELINE_STATUS_LABELS} kind="状态" />
                      </span>
                    </td>
                    <td>
                      <OperationalCode value={job.current_node} labels={PIPELINE_NODE_LABELS} kind="节点" />
                      {job.wait_reason ? (
                        <small><OperationalCode value={job.wait_reason} labels={PIPELINE_WAIT_REASON_LABELS} kind="等待原因" /></small>
                      ) : null}
                    </td>
                    <td>{formatTimestamp(job.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!jobs.length ? <div className="compact-empty">尚无处理任务</div> : null}
          </div>
        </section>

        <JobInspector
          detail={detail}
          loading={loadingDetail}
          mutating={mutating}
          trustedMutating={trustedMutating}
          trustedLease={trustedLease}
          sourceResolution={sourceResolution}
          sourceResolutionLoading={sourceResolutionLoading}
          sourceResolutionMutating={sourceResolutionMutating}
          sourceReviewConfirmed={sourceReviewConfirmation !== null}
          cancelPending={cancelPending}
          routeAmbiguous={detail?.pipeline.wait_reason === "route_ambiguous"}
          onResume={resume}
          onPrepareTrustedParse={prepareTrusted}
          onApproveTrustedParse={approveTrusted}
          onOpenSourceReview={openSourceReview}
          onAcceptSourceAdequacy={() => decideSourceAdequacy("accept_uncertainty")}
          onRequireSourceRemediation={() => decideSourceAdequacy("remediation_required")}
          onCancel={cancel}
        />
      </div>
    </section>
  );
}

type JobInspectorProps = {
  detail: IntakeJobDetail | null;
  loading: boolean;
  mutating: boolean;
  trustedMutating: boolean;
  trustedLease: TrustedParseLease | null;
  sourceResolution: IntakeSourceAdequacyResolutionContext | null;
  sourceResolutionLoading: boolean;
  sourceResolutionMutating: boolean;
  sourceReviewConfirmed: boolean;
  cancelPending: boolean;
  routeAmbiguous: boolean;
  onResume: () => void;
  onPrepareTrustedParse: () => void;
  onApproveTrustedParse: () => void;
  onOpenSourceReview: () => void;
  onAcceptSourceAdequacy: () => void;
  onRequireSourceRemediation: () => void;
  onCancel: () => void;
};

function JobInspector({
  detail,
  loading,
  mutating,
  trustedMutating,
  trustedLease,
  sourceResolution,
  sourceResolutionLoading,
  sourceResolutionMutating,
  sourceReviewConfirmed,
  cancelPending,
  routeAmbiguous,
  onResume,
  onPrepareTrustedParse,
  onApproveTrustedParse,
  onOpenSourceReview,
  onAcceptSourceAdequacy,
  onRequireSourceRemediation,
  onCancel,
}: JobInspectorProps) {
  if (loading) {
    return <section className="job-inspector processing-inspector"><LoaderCircle className="spin" aria-hidden="true" /><span>读取任务</span></section>;
  }
  if (!detail) {
    return <section className="job-inspector processing-inspector"><Inbox aria-hidden="true" /><span>选择一个任务</span></section>;
  }
  const { pipeline, source_adequacy: adequacy } = detail;
  const trustedParseEligible = isTrustedParseEligible(detail);
  return (
    <section className="job-inspector" aria-labelledby="job-detail-heading">
      <header className="job-inspector-header">
        <div>
          <p className="section-kicker">任务详情</p>
          <h3 id="job-detail-heading">{pipeline.job_id}</h3>
        </div>
        <span className={`status-badge job-status-${pipeline.status}`}>
          <OperationalCode value={pipeline.status} labels={PIPELINE_STATUS_LABELS} kind="状态" />
        </span>
      </header>

      {pipeline.wait_reason ? (
        <div className="wait-banner">
          <CircleAlert size={17} aria-hidden="true" />
          <div>
            <strong><OperationalCode value={pipeline.wait_reason} labels={PIPELINE_WAIT_REASON_LABELS} kind="等待原因" /></strong>
            <span>
              {routeAmbiguous
                ? "需要确认文献路线"
                : <OperationalCode value={pipeline.current_node} labels={PIPELINE_NODE_LABELS} kind="节点" />}
            </span>
          </div>
        </div>
      ) : null}

      <dl className="job-facts">
        <div>
          <dt>当前节点</dt>
          <dd><OperationalCode value={pipeline.current_node} labels={PIPELINE_NODE_LABELS} kind="节点" /></dd>
        </div>
        <div>
          <dt>来源方式</dt>
          <dd>
            {detail.ingress_mode
              ? <OperationalCode value={detail.ingress_mode} labels={INGRESS_LABELS} kind="来源方式" />
              : "待定"}
          </dd>
        </div>
        <div><dt>论文 ID</dt><dd className="mono">{detail.paper_id ?? "待定"}</dd></div>
        <div><dt>修订版本</dt><dd>{pipeline.revision}</dd></div>
      </dl>

      {trustedParseEligible ? (
        <TrustedParseApproval
          preparation={trustedLease?.preparation ?? null}
          busy={mutating || trustedMutating}
          onPrepare={onPrepareTrustedParse}
          onApprove={onApproveTrustedParse}
        />
      ) : null}

      {isIntakeSourceAdequacyResolutionEligible(detail) ? (
        <IntakeSourceAdequacyResolutionPanel
          context={sourceResolution}
          loading={sourceResolutionLoading}
          busy={mutating || sourceResolutionMutating}
          sourceReviewConfirmed={sourceReviewConfirmed}
          onOpen={onOpenSourceReview}
          onAccept={onAcceptSourceAdequacy}
          onRemediate={onRequireSourceRemediation}
        />
      ) : null}

      {adequacy ? (
        <section className="adequacy-section" aria-labelledby="adequacy-heading">
          <div className="subsection-heading">
            <h3 id="adequacy-heading">用途能力</h3>
            <span className={`status-badge adequacy-${adequacy.gate_status}`}>{sourceAdequacyLabel(adequacy.gate_status)}</span>
          </div>
          <div className="adequacy-summary">
            <span>用途 {sourceAdequacyLabel(adequacy.requested_operation)}</span>
            <span>新鲜度 {sourceAdequacyLabel(adequacy.freshness)}</span>
          </div>
          <div className="capability-list">
            {Object.entries(adequacy.capabilities).map(([name, capability]) => (
              <div className="capability-row" key={name}>
                <div>
                  <strong>{sourceAdequacyLabel(name)}</strong>
                  <span>{capability.authority_layers.map(sourceAdequacyLabel).join(" + ")}</span>
                </div>
                <span className={`capability-state capability-${capability.status}`}>{sourceAdequacyLabel(capability.status)}</span>
                {capability.reasons.length ? <p>{capability.reasons.map(sourceAdequacyLabel).join(" ")}</p> : null}
              </div>
            ))}
          </div>
          {adequacy.known_limitations.length ? (
            <div className="adequacy-notes">
              <h4>已知限制</h4>
              <ul>{adequacy.known_limitations.map((item) => <li key={item}>{sourceAdequacyLabel(item)}</li>)}</ul>
            </div>
          ) : null}
          {adequacy.recommended_actions.length ? (
            <div className="adequacy-notes action-notes">
              <h4>建议处理</h4>
              <ul>{adequacy.recommended_actions.map((item) => <li key={item}>{sourceAdequacyLabel(item)}</li>)}</ul>
            </div>
          ) : null}
        </section>
      ) : null}

      <div className={`job-actions ${routeAmbiguous ? "" : "job-actions-single"}`}>
        {routeAmbiguous ? (
          <button type="button" onClick={onResume} disabled={mutating || !pipeline.can_resume}>
            <RotateCcw size={16} aria-hidden="true" />
            继续处理
          </button>
        ) : null}
        <button className="secondary-button cancel-job-button" type="button" onClick={onCancel} disabled={cancelPending || !pipeline.can_cancel}>
          <X size={16} aria-hidden="true" />
          {trustedParseEligible ? "取消当前解析" : "取消任务"}
        </button>
      </div>
    </section>
  );
}

type IntakeSourceAdequacyResolutionPanelProps = {
  context: IntakeSourceAdequacyResolutionContext | null;
  loading: boolean;
  busy: boolean;
  sourceReviewConfirmed: boolean;
  onOpen: () => void;
  onAccept: () => void;
  onRemediate: () => void;
};

function IntakeSourceAdequacyResolutionPanel({
  context,
  loading,
  busy,
  sourceReviewConfirmed,
  onOpen,
  onAccept,
  onRemediate,
}: IntakeSourceAdequacyResolutionPanelProps) {
  const reviewRequired = context?.resolution_state === "review_required";
  const recovery = context?.resolution_state === "accepted_continuation_required"
    || context?.resolution_state === "continuation_in_progress";
  const currentAndAcceptable = Boolean(context && !context.hard_failure && context.freshness === "current");
  const canOpen = Boolean(
    reviewRequired
    && context?.source_review_required
    && context.allowed_actions.includes("accept_uncertainty")
    && currentAndAcceptable,
  );
  const canAccept = Boolean(
    currentAndAcceptable
    && (recovery || context?.allowed_actions.includes("accept_uncertainty"))
    && (recovery || (reviewRequired && sourceReviewConfirmed)),
  );
  const canRemediate = Boolean(
    reviewRequired && context?.allowed_actions.includes("remediation_required"),
  );

  return (
    <section className="adequacy-section" aria-labelledby="source-resolution-heading">
      <div className="subsection-heading">
        <h3 id="source-resolution-heading">来源充分性处理</h3>
        <span className={`status-badge adequacy-${context?.resolution_state ?? "loading"}`}>
          {loading ? sourceAdequacyLabel("loading") : sourceAdequacyLabel(context?.resolution_state ?? "unavailable")}
        </span>
      </div>
      {context ? (
        <>
          <div className="adequacy-summary">
            <span>用途 {sourceAdequacyLabel(context.requested_operation)}</span>
            <span>能力 {sourceAdequacyLabel(context.required_capability)}</span>
          </div>
          {context.known_limitations.length ? (
            <div className="adequacy-notes">
              <h4>已知限制</h4>
              <ul>{context.known_limitations.map((item) => <li key={item}>{sourceAdequacyLabel(item)}</li>)}</ul>
            </div>
          ) : null}
          {context.recommended_actions.length ? (
            <div className="adequacy-notes action-notes">
              <h4>建议处理</h4>
              <ul>{context.recommended_actions.map((item) => <li key={item}>{sourceAdequacyLabel(item)}</li>)}</ul>
            </div>
          ) : null}
          {(canOpen || canAccept || canRemediate) ? (
            <div className="job-actions">
              {canOpen ? (
                <button className="secondary-button" type="button" onClick={onOpen} disabled={busy}>
                  <FileText size={16} aria-hidden="true" />
                  打开原文
                </button>
              ) : null}
              {canAccept ? (
                <button type="button" onClick={onAccept} disabled={busy}>
                  <CheckCircle2 size={16} aria-hidden="true" />
                  {recovery ? "继续已接受处理" : "接受当前限制"}
                </button>
              ) : reviewRequired && canOpen ? (
                <button type="button" disabled>
                  <CheckCircle2 size={16} aria-hidden="true" />
                  接受当前限制
                </button>
              ) : null}
              {canRemediate ? (
                <button className="secondary-button" type="button" onClick={onRemediate} disabled={busy}>
                  <RotateCcw size={16} aria-hidden="true" />
                  需要重新处理
                </button>
              ) : null}
            </div>
          ) : null}
        </>
      ) : (
        <div className="processing-inspector">
          {loading ? <LoaderCircle className="spin" aria-hidden="true" /> : <CircleAlert aria-hidden="true" />}
          <span>{loading ? "读取来源状态" : "来源状态不可用"}</span>
        </div>
      )}
    </section>
  );
}

type TrustedParseApprovalProps = {
  preparation: TrustedParsePreparation | null;
  busy: boolean;
  onPrepare: () => void;
  onApprove: () => void;
};

function TrustedParseApproval({ preparation, busy, onPrepare, onApprove }: TrustedParseApprovalProps) {
  const expired = preparation ? trustedLeaseExpired(preparation) : false;
  return (
    <section className="trusted-parse-section" aria-labelledby="trusted-parse-heading">
      <header className="trusted-parse-header">
        <div>
          <p className="section-kicker">受监督解析</p>
          <h3 id="trusted-parse-heading">受监督解析批准</h3>
        </div>
        <ShieldCheck size={18} aria-hidden="true" />
      </header>

      {preparation ? (
        <>
          <dl className="trusted-parse-facts">
            <div><dt>来源</dt><dd><span>{safeDisplayName(preparation.source.display_name)}</span><strong>{formatBytes(preparation.source.size_bytes)}</strong></dd></div>
            <div>
              <dt>来源身份</dt>
              <dd><OperationalCode value={preparation.source.identity_status} labels={SOURCE_ADEQUACY_LABELS} kind="状态" /></dd>
            </div>
            <div><dt>解析器</dt><dd><span>{preparation.parser.adapter}</span><strong>{preparation.parser.version}</strong></dd></div>
            <div><dt>解析配置 ID</dt><dd className="mono">{preparation.parser_profile_id}</dd></div>
            <div><dt>策略版本</dt><dd className="mono">{preparation.policy_version}</dd></div>
            <div><dt>允许操作</dt><dd className="mono">{preparation.allowed_operation}</dd></div>
            <div><dt>有效期至</dt><dd><Clock3 size={14} aria-hidden="true" />{formatTimestamp(preparation.expires_at)}</dd></div>
            <div><dt>需要受监督重新解析</dt><dd>{preparation.supervised_reparse_required ? "是" : "否"}</dd></div>
          </dl>
          <div className="trusted-parse-warning">
            <CircleAlert size={17} aria-hidden="true" />
            <div><strong>有限信任警告</strong><span className="mono">{preparation.limited_trust_warning}</span></div>
          </div>
          {expired ? <p className="trusted-parse-expired" role="status">解析准备已过期，请重新准备解析</p> : null}
        </>
      ) : (
        <p className="trusted-parse-empty">当前任务需要一次新的本地用户批准。先准备解析，再检查公开摘要。</p>
      )}

      <div className="trusted-parse-actions">
        <button type="button" onClick={onPrepare} disabled={busy}>
          {busy ? <LoaderCircle size={16} className="spin" aria-hidden="true" /> : <ShieldCheck size={16} aria-hidden="true" />}
          准备解析
        </button>
        <button className="secondary-button" type="button" onClick={onApprove} disabled={busy || !preparation || expired}>
          <CheckCircle2 size={16} aria-hidden="true" />
          批准并解析
        </button>
      </div>
    </section>
  );
}

function errorMessage(caught: unknown): string {
  if (caught instanceof ApiError) {
    return `${APP_DIAGNOSTIC_LABELS[caught.code] ?? "请求未完成，请根据诊断代码重试"}（${caught.code}）`;
  }
  return "请求未完成";
}

function trustedParseErrorMessage(caught: unknown): string {
  const detail = caught instanceof ApiError
    ? `${caught.code} ${caught.message}`
    : caught instanceof Error ? caught.message : String(caught);
  if (/stale|expired|expiry|lease|wrong.?session|session|protected.?input|write.?conflict/i.test(detail)) {
    return "解析准备已失效，请重新准备解析";
  }
  return errorMessage(caught);
}

function trustedLeaseExpired(preparation: TrustedParsePreparation): boolean {
  const expiresAt = Date.parse(preparation.expires_at);
  return Number.isFinite(expiresAt) && expiresAt <= Date.now();
}

function safeDisplayName(value: string): string {
  const normalized = value.replaceAll("\\", "/");
  const basename = normalized.slice(normalized.lastIndexOf("/") + 1).trim();
  return basename || "source.pdf";
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function formatTimestamp(value: string): string {
  const timestamp = new Date(value);
  return Number.isNaN(timestamp.getTime()) ? value : timestamp.toLocaleString("zh-CN", { hour12: false });
}
