import { FormEvent, useEffect, useMemo, useState } from "react";
import { Check, Download, RefreshCw, Search } from "lucide-react";
import {
  acquireDiscoveryCandidate,
  getAcquiredCandidateHandoff,
  listDiscoveryCandidates,
  resolveDiscoveryCandidate,
  searchDiscovery,
  selectDiscovery,
  type AcquiredCandidateHandoff,
  type DiscoveryCandidate,
  type DiscoveryReport,
  type DiscoveryResolution,
} from "../api";

export function DiscoveryView() {
  const [dateFrom, setDateFrom] = useState("");
  const [dateUntil, setDateUntil] = useState("");
  const [titleKeywords, setTitleKeywords] = useState("");
  const [abstractKeywords, setAbstractKeywords] = useState("");
  const [keywordMode, setKeywordMode] = useState<"any" | "all">("any");
  const [includePreprints, setIncludePreprints] = useState(true);
  const [maxResults, setMaxResults] = useState(15);
  const [report, setReport] = useState<DiscoveryReport | null>(null);
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);
  const [candidates, setCandidates] = useState<DiscoveryCandidate[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [resolutions, setResolutions] = useState<Record<string, DiscoveryResolution>>({});
  const [handoffs, setHandoffs] = useState<Record<string, AcquiredCandidateHandoff>>({});
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    refreshCandidates(false).catch((caught: unknown) => setError(message(caught)));
  }, []);

  const selectedCount = selectedKeys.length;
  const queryReady = useMemo(
    () => Boolean(dateFrom && dateUntil && (tokens(titleKeywords).length || tokens(abstractKeywords).length)),
    [dateFrom, dateUntil, titleKeywords, abstractKeywords],
  );

  async function refreshCandidates(append: boolean) {
    const result = await listDiscoveryCandidates(25, append ? nextCursor : null);
    setCandidates((current) => append ? [...current, ...result.candidates] : result.candidates);
    setNextCursor(result.next_cursor);
  }

  async function handleSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy("search");
    setError("");
    try {
      const result = await searchDiscovery({
        request_version: "1.0",
        date_from: dateFrom,
        date_until: dateUntil,
        title_keywords: tokens(titleKeywords),
        abstract_keywords: tokens(abstractKeywords),
        keyword_mode: keywordMode,
        include_preprints: includePreprints,
        max_results: maxResults,
      });
      setReport(result);
      setSelectedKeys([]);
    } catch (caught) {
      setError(message(caught));
    } finally {
      setBusy("");
    }
  }

  async function handleSelection() {
    if (!report || selectedCount === 0) return;
    setBusy("select");
    setError("");
    try {
      await selectDiscovery(report, selectedKeys);
      await refreshCandidates(false);
      setSelectedKeys([]);
    } catch (caught) {
      setError(message(caught));
    } finally {
      setBusy("");
    }
  }

  async function handleResolve(candidateId: string) {
    setBusy(`resolve:${candidateId}`);
    setError("");
    try {
      const result = await resolveDiscoveryCandidate(candidateId);
      setResolutions((current) => ({ ...current, [candidateId]: result }));
    } catch (caught) {
      setError(message(caught));
    } finally {
      setBusy("");
    }
  }

  async function handleAcquire(candidateId: string) {
    setBusy(`acquire:${candidateId}`);
    setError("");
    try {
      await acquireDiscoveryCandidate(candidateId);
      const handoff = await getAcquiredCandidateHandoff(candidateId);
      setHandoffs((current) => ({ ...current, [candidateId]: handoff }));
      await refreshCandidates(false);
    } catch (caught) {
      setError(message(caught));
    } finally {
      setBusy("");
    }
  }

  function toggleResult(resultKey: string) {
    setSelectedKeys((current) => current.includes(resultKey)
      ? current.filter((item) => item !== resultKey)
      : [...current, resultKey]);
  }

  return (
    <section className="discovery-view" aria-labelledby="discovery-title">
      <header className="work-view-header">
        <div><p className="section-kicker">EUROPE PMC</p><h2 id="discovery-title">论文发现</h2></div>
        <button className="icon-button" type="button" onClick={() => refreshCandidates(false).catch((caught: unknown) => setError(message(caught)))} title="刷新候选" aria-label="刷新候选">
          <RefreshCw size={17} aria-hidden="true" />
        </button>
      </header>

      <form className="discovery-query" onSubmit={handleSearch}>
        <label>起始日期<input type="date" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} required /></label>
        <label>结束日期<input type="date" value={dateUntil} onChange={(event) => setDateUntil(event.target.value)} required /></label>
        <label className="discovery-keywords">标题关键词<input value={titleKeywords} onChange={(event) => setTitleKeywords(event.target.value)} placeholder="逗号或换行分隔" /></label>
        <label className="discovery-keywords">摘要关键词<input value={abstractKeywords} onChange={(event) => setAbstractKeywords(event.target.value)} placeholder="逗号或换行分隔" /></label>
        <fieldset className="segmented-control">
          <legend>匹配</legend>
          <button type="button" className={keywordMode === "any" ? "segment-active" : ""} onClick={() => setKeywordMode("any")}>任一</button>
          <button type="button" className={keywordMode === "all" ? "segment-active" : ""} onClick={() => setKeywordMode("all")}>全部</button>
        </fieldset>
        <label className="check-control"><input type="checkbox" checked={includePreprints} onChange={(event) => setIncludePreprints(event.target.checked)} />接受预印本</label>
        <label>结果数<input type="number" min={1} max={15} value={maxResults} onChange={(event) => setMaxResults(Number(event.target.value))} /></label>
        <button type="submit" disabled={!queryReady || Boolean(busy)}><Search size={17} aria-hidden="true" />搜索</button>
      </form>

      {report && (
        <section className="discovery-results" aria-labelledby="transient-results-title">
          <div className="section-row"><h3 id="transient-results-title">本次结果 <span>{report.returned_result_count}</span></h3><button type="button" onClick={handleSelection} disabled={!selectedCount || Boolean(busy)}><Check size={17} aria-hidden="true" />保存所选 {selectedCount || ""}</button></div>
          {report.results.length === 0 ? <p className="empty-state">没有符合条件的结果</p> : (
            <div className="discovery-table" role="table" aria-label="本次检索结果">
              {report.results.map((item) => (
                <label className="discovery-result-row" key={item.result_key}>
                  <input type="checkbox" checked={selectedKeys.includes(item.result_key)} onChange={() => toggleResult(item.result_key)} />
                  <span className="discovery-result-main"><strong>{item.title}</strong><span>{item.authors.join(", ")}</span></span>
                  <span>{item.first_publication_date}</span><span>{item.paper_type}</span><span>{item.doi ?? "No DOI"}</span>
                </label>
              ))}
            </div>
          )}
        </section>
      )}

      <section className="discovery-candidates" aria-labelledby="candidate-title">
        <div className="section-row"><h3 id="candidate-title">跟进候选 <span>{candidates.length}</span></h3></div>
        {candidates.length === 0 ? <p className="empty-state">尚未保存候选</p> : candidates.map((candidate) => {
          const resolution = resolutions[candidate.candidate_id];
          const acquired = candidate.acquisition_status === "acquired";
          return (
            <article className="candidate-row" key={candidate.candidate_id}>
              <div><strong>{candidate.title}</strong><p>{candidate.first_publication_date} · {candidate.doi ?? "No DOI"}</p></div>
              <span className={`status-chip ${acquired ? "status-current" : ""}`}>{acquired ? "已获取" : "等待来源"}</span>
              <div className="candidate-actions">
                {!acquired && <button className="secondary-button" type="button" onClick={() => handleResolve(candidate.candidate_id)} disabled={Boolean(busy)}>检查 OA</button>}
                {!acquired && resolution?.resolution_status === "auto_acquisition_eligible" && <button className="icon-button" type="button" onClick={() => handleAcquire(candidate.candidate_id)} disabled={Boolean(busy)} title="下载 OA PDF" aria-label="下载 OA PDF"><Download size={17} /></button>}
              </div>
              {resolution && resolution.resolution_status !== "auto_acquisition_eligible" && <p className="candidate-note">人工下载：{candidate.title} · {candidate.doi ?? "No DOI"} · {resolution.manual_reason ?? "无可用 OA 路径"}</p>}
              {(acquired || handoffs[candidate.candidate_id]) && <p className="candidate-note">来源已就绪，等待单独纳入知识库</p>}
            </article>
          );
        })}
        {nextCursor && <button className="secondary-button load-more-button" type="button" onClick={() => refreshCandidates(true).catch((caught: unknown) => setError(message(caught)))} disabled={Boolean(busy)}>加载更多</button>}
      </section>
      {error && <div className="error-banner" role="alert">{error}</div>}
    </section>
  );
}

function tokens(value: string): string[] {
  return [...new Set(value.split(/[,\n]/).map((item) => item.trim()).filter(Boolean))];
}

function message(caught: unknown): string {
  return caught instanceof Error ? caught.message : "请求未完成";
}
