import { useEffect, useRef, useState } from "react";
import {
  BookOpen,
  FileSearch,
  GitCompareArrows,
  Quote,
  X,
} from "lucide-react";
import {
  ApiError,
  compareReadingPapers,
  getEvidenceTrace,
  getReadingPaper,
  type EvidenceTrace,
  type PaperCardUnit,
  type ReadingPaper,
  type ReadingSourcePage,
  type ReviewUnit,
} from "../api";
import { EvidencePdfViewer } from "./EvidencePdfViewer";

type ReadingWorkspaceProps = {
  paperIds: string[];
  onRemovePaper: (paperId: string) => void;
};

const PRIMARY_SECTION_LABELS: Readonly<Record<string, string>> = {
  research_background_significance: "1. 研究背景与研究意义",
  research_problem: "2. 该研究所解决的问题",
  method_principle_advantages: "3. 研究方法的原理与优势",
  conclusions_applications: "4. 研究结论与应用",
  innovation: "5. 总结研究的创新性",
  limitations: "6. 现有研究的不足之处",
  future_outlook: "7. 对于未来研究的展望",
};

const REVIEW_SECTION_LABELS: Readonly<Record<string, string>> = {
  review_objective_scope: "1. 综述目标与覆盖范围",
  review_question_search_boundaries: "2. 研究问题与检索边界",
  taxonomy_field_structure: "3. 分类框架与领域结构",
  major_synthesis: "4. 主要综合观点",
  methods_metrics_guardrails: "5. 方法、指标与阅读边界",
  gaps_frontiers: "6. 研究缺口与前沿",
  primary_leads_reuse: "7. 原始论文线索与复用方向",
};

export function ReadingWorkspace({ paperIds, onRemovePaper }: ReadingWorkspaceProps) {
  const [papers, setPapers] = useState<ReadingPaper[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [trace, setTrace] = useState<EvidenceTrace | null>(null);
  const [traceLoading, setTraceLoading] = useState(false);
  const [traceError, setTraceError] = useState("");
  const traceRequest = useRef(0);

  useEffect(() => {
    let active = true;
    traceRequest.current += 1;
    setTrace(null);
    setTraceLoading(false);
    setTraceError("");
    if (paperIds.length === 0) {
      setPapers([]);
      setLoading(false);
      setError("");
      return () => { active = false; };
    }
    setLoading(true);
    setError("");
    const operation = paperIds.length === 1
      ? getReadingPaper(paperIds[0]).then((paper) => [paper])
      : compareReadingPapers(paperIds).then((comparison) => comparison.papers);
    operation.then((result) => {
      if (active) setPapers(result);
    }).catch((caught: unknown) => {
      if (active) {
        setPapers([]);
        setError(errorMessage(caught));
      }
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, [paperIds]);

  async function openEvidence(evidenceId: string) {
    const requestId = traceRequest.current + 1;
    traceRequest.current = requestId;
    setTrace(null);
    setTraceLoading(true);
    setTraceError("");
    try {
      const result = await getEvidenceTrace(evidenceId);
      if (traceRequest.current === requestId) setTrace(result);
    } catch (caught) {
      if (traceRequest.current === requestId) setTraceError(errorMessage(caught));
    } finally {
      if (traceRequest.current === requestId) setTraceLoading(false);
    }
  }

  function closeEvidence() {
    traceRequest.current += 1;
    setTrace(null);
    setTraceLoading(false);
    setTraceError("");
  }

  return (
    <section className="reading-workspace" aria-labelledby="reading-workspace-title">
      <header className="view-heading reading-heading">
        <div>
          <p className="section-kicker">READING WORKSPACE</p>
          <h2 id="reading-workspace-title">论文阅读</h2>
        </div>
        {paperIds.length > 1 && (
          <span className="reading-mode-badge">
            <GitCompareArrows size={15} aria-hidden="true" />
            {paperIds.length} 篇并排阅读
          </span>
        )}
      </header>

      {paperIds.length === 0 && (
        <ReadingState icon={BookOpen} label="从文献库打开论文，或选择 2-4 篇加入比较" />
      )}
      {paperIds.length > 0 && loading && <ReadingState icon={FileSearch} label="正在装载已提交阅读上下文" />}
      {!loading && error && <div className="inline-error" role="alert">{error}</div>}

      {!loading && !error && papers.length > 0 && (
        <div className={`reading-columns reading-columns-${papers.length}`}>
          {papers.map((paper) => (
            <PaperColumn
              key={paper.paper.paper_id}
              reading={paper}
              removable={paperIds.length > 1}
              onRemove={() => onRemovePaper(paper.paper.paper_id)}
              onOpenEvidence={(evidenceId) => void openEvidence(evidenceId)}
            />
          ))}
        </div>
      )}

      {(trace || traceLoading || traceError) && (
        <EvidenceDrawer
          trace={trace}
          loading={traceLoading}
          error={traceError}
          onClose={closeEvidence}
        />
      )}
    </section>
  );
}

function PaperColumn({
  reading,
  removable,
  onRemove,
  onOpenEvidence,
}: {
  reading: ReadingPaper;
  removable: boolean;
  onRemove: () => void;
  onOpenEvidence: (evidenceId: string) => void;
}) {
  const bibliography = reading.paper.bibliography;
  return (
    <article className="reading-paper-column" data-testid="reading-paper-column">
      <header className="paper-reading-header">
        <div className="paper-route-line">
          <span className={`route-badge route-${reading.document_route}`}>{routeLabel(reading.document_route)}</span>
          {reading.document_route === "review" && <span className="background-badge">仅作背景</span>}
          {removable && (
            <button className="icon-button compact-icon" type="button" onClick={onRemove} title="移出并排阅读" aria-label={`移出 ${bibliography.title}`}>
              <X size={16} />
            </button>
          )}
        </div>
        <h3>{bibliography.title}</h3>
        <p className="paper-citation-line">
          {bibliography.authors.join(", ") || "作者未知"}
          {bibliography.year ? ` · ${bibliography.year}` : ""}
          {bibliography.doi ? ` · ${bibliography.doi}` : ""}
        </p>
        <div className="reading-status-strip" aria-label="阅读状态">
          <ReadingBadge label={`source:${reading.source.source_availability}`} value={reading.source.source_availability} />
          <ReadingBadge label={`trace:${reading.source.source_currentness}`} value={reading.source.source_currentness} />
          <ReadingBadge label={`parse:${reading.parse.binding_state}`} value={reading.parse.binding_state} />
          <ReadingBadge
            label={`revision:${reading.primary?.revision_status ?? reading.review?.revision_status ?? "none"}`}
            value={reading.primary?.revision_status ?? reading.review?.revision_status ?? "none"}
          />
        </div>
      </header>

      <AdequacyStrip reading={reading} />
      {reading.primary && <PrimaryReading reading={reading} onOpenEvidence={onOpenEvidence} />}
      {reading.review && <ReviewReading reading={reading} />}
      {!reading.primary && !reading.review && (
        <ReadingState icon={FileSearch} label="该论文尚无已提交的 Paper Card 或 Review Memory" compact />
      )}
      <QuestionContext reading={reading} />
    </article>
  );
}

function AdequacyStrip({ reading }: { reading: ReadingPaper }) {
  if (reading.adequacy.length === 0) {
    return <div className="adequacy-strip adequacy-unknown">Source Adequacy 尚无记录</div>;
  }
  return (
    <div className="adequacy-strip" aria-label="Source Adequacy">
      {reading.adequacy.map((item) => (
        <span key={item.requested_operation}>
          {humanize(item.requested_operation)}
          <strong className={statusTone(`${item.freshness} ${item.capability_status ?? "unknown"}`)}>
            {item.capability_status ?? "unknown"} · {item.freshness}
          </strong>
        </span>
      ))}
    </div>
  );
}

function PrimaryReading({ reading, onOpenEvidence }: { reading: ReadingPaper; onOpenEvidence: (id: string) => void }) {
  const primary = reading.primary;
  if (!primary) return null;
  const admissibility = new Map(primary.unit_admissibility.map((item) => [item.unit_id, item]));
  return (
    <div className="reading-outline">
      {primary.paper_card.sections.map((section) => (
        <section className="reading-section" key={section.section_id}>
          <h4>{PRIMARY_SECTION_LABELS[section.section_id] ?? humanize(section.section_id)}</h4>
          {section.units.length === 0 ? (
            <p className="section-empty">本节暂无保留单元</p>
          ) : section.units.map((unit) => (
            <PrimaryUnit
              key={unit.unit_id}
              unit={unit}
              factualEligible={admissibility.get(unit.unit_id)?.factual_support_eligible ?? false}
              onOpenEvidence={onOpenEvidence}
            />
          ))}
        </section>
      ))}
    </div>
  );
}

function PrimaryUnit({
  unit,
  factualEligible,
  onOpenEvidence,
}: {
  unit: PaperCardUnit;
  factualEligible: boolean;
  onOpenEvidence: (id: string) => void;
}) {
  return (
    <div className="reading-unit">
      <div className="unit-status-line">
        <ReadingBadge label={unit.grounding_status} value={unit.grounding_status} />
        <ReadingBadge label={factualEligible ? "单元状态可采信" : "单元状态受限"} value={factualEligible ? "current" : "warning"} />
        <span>{humanize(unit.statement_type)}</span>
      </div>
      <p>{unit.statement}</p>
      {unit.source_page && <SourceLocator page={unit.source_page} />}
      {unit.evidence_ids.length > 0 && (
        <div className="evidence-actions">
          {unit.evidence_ids.map((evidenceId) => (
            <button className="evidence-link" type="button" key={evidenceId} onClick={() => onOpenEvidence(evidenceId)} aria-label={`查看 Evidence ${evidenceId}`}>
              <Quote size={15} aria-hidden="true" />
              Evidence
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function ReviewReading({ reading }: { reading: ReadingPaper }) {
  const review = reading.review;
  if (!review) return null;
  const memory = review.review_memory;
  const retainedUnits = memory.sections.reduce((total, section) => total + section.units.length, 0);
  return (
    <div className="reading-outline review-outline">
      <div className="review-boundary" role="note">
        <strong>仅作背景</strong>
        <span>Review Memory 不进入 canonical Evidence，也不单独支持实验性结论。</span>
      </div>
      <div className="memory-value-line">
        <ReadingBadge label={humanize(memory.memory_value.status)} value={memory.memory_value.status} />
        <span>{memory.memory_value.reason}</span>
      </div>
      {retainedUnits === 0 && (
        <div className="low-value-state">已记录为低价值或重复综述；保留此记录以避免重复阅读。</div>
      )}
      {memory.sections.map((section) => (
        <section className="reading-section" key={section.section_id}>
          <h4>{REVIEW_SECTION_LABELS[section.section_id] ?? humanize(section.section_id)}</h4>
          {section.units.length === 0 ? (
            <p className="section-empty">本节暂无可复用单元</p>
          ) : section.units.map((unit) => <ReviewUnitView key={unit.review_unit_id} unit={unit} />)}
        </section>
      ))}
      <div className="coverage-limits">
        <strong>Coverage limits</strong>
        <span>{memory.coverage_limits.reason}</span>
        {memory.coverage_limits.unread_sections.length > 0 && <span>未读：{memory.coverage_limits.unread_sections.join("、")}</span>}
        {memory.coverage_limits.weakly_read_sections.length > 0 && <span>弱覆盖：{memory.coverage_limits.weakly_read_sections.join("、")}</span>}
      </div>
    </div>
  );
}

function ReviewUnitView({ unit }: { unit: ReviewUnit }) {
  return (
    <div className="reading-unit review-unit">
      <div className="unit-status-line">
        <span className="background-badge">仅作背景</span>
        <span>{humanize(unit.unit_type)}</span>
      </div>
      <p>{unit.content}</p>
      {unit.source_notes.map((note, index) => (
        <div className="review-source-note" key={`${note.pdf_page}-${index}`}>
          <SourceLocator page={note} locator={note.locator} />
          <span className="note-type">{humanize(note.note_type)}</span>
          <q><span className="source-note-label">定位文本：</span>{note.text}</q>
        </div>
      ))}
    </div>
  );
}

function QuestionContext({ reading }: { reading: ReadingPaper }) {
  if (reading.questions.length === 0) return null;
  return (
    <section className="question-context">
      <h4>关联研究问题</h4>
      {reading.questions.map((question) => (
        <div key={question.question_id}>
          <div className="question-line">
            <strong>{question.question_text}</strong>
            <ReadingBadge label={question.freshness} value={question.freshness} />
          </div>
          <p>{question.scope}</p>
        </div>
      ))}
    </section>
  );
}

function EvidenceDrawer({
  trace,
  loading,
  error,
  onClose,
}: {
  trace: EvidenceTrace | null;
  loading: boolean;
  error: string;
  onClose: () => void;
}) {
  return (
    <aside className="evidence-drawer" aria-label="Evidence 回源">
      <header>
        <div>
          <p className="section-kicker">EVIDENCE TRACE</p>
          <h3>{trace?.evidence.claim ?? "Evidence 回源"}</h3>
        </div>
        <button className="icon-button" type="button" onClick={onClose} title="关闭 Evidence" aria-label="关闭 Evidence">
          <X size={18} />
        </button>
      </header>
      {loading && <ReadingState icon={FileSearch} label="正在读取 Evidence provenance" compact />}
      {!loading && error && <div className="inline-error" role="alert">{error}</div>}
      {!loading && trace && (
        <div className="evidence-drawer-body">
          <div className="reading-status-strip">
            <ReadingBadge label={trace.factual_support_eligible ? "可用于事实支持" : "不可用于事实支持"} value={trace.factual_support_eligible ? "current" : "warning"} />
            <ReadingBadge label={`source:${trace.source.source_currentness}`} value={trace.source.source_currentness} />
            <ReadingBadge label={`parse:${trace.parse.binding_state}`} value={trace.parse.binding_state} />
            <ReadingBadge label={`revision:${trace.primary_revision.revision_status}`} value={trace.primary_revision.revision_status} />
          </div>
          <section>
            <h4>原文摘录</h4>
            <blockquote>{trace.evidence.quote}</blockquote>
            <SourceLocator page={trace.evidence.source_page} locator={trace.evidence.locator} />
          </section>
          <section className="evidence-pdf-section">
            <h4>PDF 回源</h4>
            <EvidencePdfViewer
              evidenceId={trace.evidence.evidence_id}
              quote={trace.evidence.quote}
              targetPage={trace.evidence.source_page.pdf_page}
              locator={trace.evidence.locator}
            />
          </section>
          <section>
            <h4>支持范围</h4>
            <p>{trace.evidence.support_scope}</p>
          </section>
          <section>
            <h4>不支持</h4>
            <ul>{trace.evidence.what_it_does_not_support.map((item) => <li key={item}>{item}</li>)}</ul>
          </section>
        </div>
      )}
    </aside>
  );
}

function SourceLocator({ page, locator }: { page: ReadingSourcePage; locator?: string | null }) {
  return (
    <div className="source-locator">
      <span>PDF {page.pdf_page}</span>
      {page.printed_page && <span>印刷页 {page.printed_page}</span>}
      {page.section && <span>{page.section}</span>}
      {page.figure_or_table && <span>{page.figure_or_table}</span>}
      {locator && <code>{locator}</code>}
    </div>
  );
}

function ReadingBadge({ label, value }: { label: string; value: string }) {
  return <span className={`reading-badge ${statusTone(value)}`}>{label}</span>;
}

function ReadingState({ icon: Icon, label, compact = false }: { icon: typeof BookOpen; label: string; compact?: boolean }) {
  return (
    <div className={`reading-state${compact ? " reading-state-compact" : ""}`} role="status">
      <Icon size={compact ? 19 : 26} aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}

function routeLabel(route: string): string {
  if (route === "primary") return "原始研究";
  if (route === "review") return "综述";
  return "尚未加工";
}

function humanize(value: string): string {
  return value.replaceAll("_", " ");
}

function statusTone(value: string): string {
  if (/current|available|active|grounded|revised|yes|reusable/i.test(value)) return "reading-current";
  if (/background|not_fact|low_value/i.test(value)) return "reading-context";
  if (/missing|stale|changed|unavailable|historical|no|uncertain|warning|rejected|needs/i.test(value)) return "reading-warning";
  return "reading-neutral";
}

function errorMessage(caught: unknown): string {
  if (caught instanceof ApiError) return `${caught.code}: ${caught.message}`;
  return "阅读上下文未能载入";
}
