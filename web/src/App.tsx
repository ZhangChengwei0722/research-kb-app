import { FormEvent, useEffect, useState } from "react";
import {
  Database,
  FolderOpen,
  Power,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";
import {
  ApiError,
  bootstrap,
  getCapabilities,
  getCatalogStatus,
  getHealth,
  getSetupStatus,
  listWorkspaces,
  openWorkspace,
  rebuildCatalog,
  shutdown,
  type CapabilityResult,
  type CatalogStatus,
  type HealthResult,
  type SetupStatus,
  type WorkspaceOption,
} from "./api";
import { catalogViews, navigation, type ViewId } from "./catalogViews";
import { CatalogBrowser } from "./components/CatalogBrowser";
import { AgentView } from "./components/AgentView";
import { Overview } from "./components/Overview";
import { ProcessingView } from "./components/ProcessingView";
import { ReadingWorkspace } from "./components/ReadingWorkspace";
import { KnowledgeQueryView } from "./components/KnowledgeQueryView";
import { DiscoveryView } from "./components/DiscoveryView";
import { ResearchOrganizationView } from "./components/ResearchOrganizationView";
import { TagsView } from "./components/TagsView";
import { ScreeningView } from "./components/ScreeningView";
import { ResearchSynthesisView } from "./components/ResearchSynthesisView";
import { ObsidianView } from "./components/ObsidianView";
import { ExchangeView } from "./components/ExchangeView";
import { SetupWorkspace } from "./components/SetupWorkspace";

type Phase = "locked" | "setup" | "ready" | "stopped";

function App() {
  const [phase, setPhase] = useState<Phase>("locked");
  const [token, setToken] = useState("");
  const [setupStatus, setSetupStatus] = useState<SetupStatus | null>(null);
  const [workspaces, setWorkspaces] = useState<WorkspaceOption[]>([]);
  const [selected, setSelected] = useState("");
  const [activeWorkspace, setActiveWorkspace] = useState<WorkspaceOption | null>(null);
  const [status, setStatus] = useState<CatalogStatus | null>(null);
  const [capabilities, setCapabilities] = useState<CapabilityResult | null>(null);
  const [health, setHealth] = useState<HealthResult | null>(null);
  const [view, setView] = useState<ViewId>("overview");
  const [readingPaperIds, setReadingPaperIds] = useState<string[]>([]);
  const [refreshKey, setRefreshKey] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [panelError, setPanelError] = useState("");

  useEffect(() => {
    if (!status || status.operation?.state !== "building") return;
    const timer = window.setTimeout(async () => {
      try {
        const nextStatus = await getCatalogStatus();
        setStatus(nextStatus);
        if (nextStatus.operation?.state !== "building") {
          setRefreshKey((current) => current + 1);
          getHealth().then(setHealth).catch((caught: unknown) => setPanelError(errorMessage(caught)));
        }
      } catch (caught) {
        setError(errorMessage(caught));
      }
    }, 180);
    return () => window.clearTimeout(timer);
  }, [status]);

  async function handleBootstrap(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await bootstrap(token);
      setToken("");
      const [setup, options] = await Promise.all([getSetupStatus(), listWorkspaces()]);
      setSetupStatus(setup);
      setWorkspaces(options);
      setSelected(options[0]?.option_id ?? "");
      setPhase(setup.mode === "first_run" || setup.mode === "recovery" ? "setup" : "ready");
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function handleSetupComplete() {
    setBusy(true);
    setError("");
    try {
      const [setup, options] = await Promise.all([getSetupStatus(), listWorkspaces()]);
      setSetupStatus(setup);
      setWorkspaces(options);
      setSelected(options[0]?.option_id ?? "");
      setPhase(setup.mode === "recovery" ? "setup" : "ready");
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function handleOpen() {
    if (!selected) return;
    setBusy(true);
    setError("");
    setPanelError("");
    try {
      const catalog = await openWorkspace(selected);
      setStatus(catalog);
      setActiveWorkspace(workspaces.find((workspace) => workspace.option_id === selected) ?? null);
      setView("overview");
      setReadingPaperIds([]);
      const [capabilityResult, healthResult] = await Promise.allSettled([getCapabilities(), getHealth()]);
      const panelFailures: string[] = [];
      if (capabilityResult.status === "fulfilled") {
        setCapabilities(capabilityResult.value);
      } else {
        setCapabilities(null);
        panelFailures.push(errorMessage(capabilityResult.reason));
      }
      if (healthResult.status === "fulfilled") {
        setHealth(healthResult.value);
      } else {
        setHealth(null);
        panelFailures.push(errorMessage(healthResult.reason));
      }
      setPanelError(panelFailures.join(" | "));
      setRefreshKey((current) => current + 1);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function handleRebuild() {
    setBusy(true);
    setError("");
    try {
      await rebuildCatalog();
      setStatus(await getCatalogStatus());
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function handleShutdown() {
    setBusy(true);
    setError("");
    try {
      await shutdown();
      setPhase("stopped");
      setStatus(null);
      setActiveWorkspace(null);
      setReadingPaperIds([]);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  if (phase === "locked") {
    return (
      <main className="bootstrap-shell">
        <header className="bootstrap-brand">
          <div className="brand-mark" aria-hidden="true"><Database size={21} /></div>
          <div>
            <h1>Research KB</h1>
            <p>Local Workspace Manager</p>
          </div>
          <span className="phase-indicator">LOCKED</span>
        </header>
        <section className="bootstrap-panel" aria-labelledby="bootstrap-title">
          <ShieldCheck size={28} aria-hidden="true" />
          <div>
            <p className="section-kicker">LOCAL SESSION</p>
            <h2 id="bootstrap-title">启动验证</h2>
            <form onSubmit={handleBootstrap}>
              <label htmlFor="startup-token">一次性 Token</label>
              <div className="input-row">
                <input id="startup-token" type="password" value={token} onChange={(event) => setToken(event.target.value)} autoComplete="off" spellCheck={false} required minLength={32} />
                <button type="submit" disabled={busy}>
                  <ShieldCheck size={17} aria-hidden="true" />
                  验证
                </button>
              </div>
            </form>
          </div>
        </section>
        {error && <div className="error-banner" role="alert">{error}</div>}
      </main>
    );
  }

  if (phase === "stopped") {
    return (
      <main className="bootstrap-shell">
        <header className="bootstrap-brand">
          <div className="brand-mark" aria-hidden="true"><Database size={21} /></div>
          <div><h1>Research KB</h1><p>Local Workspace Manager</p></div>
          <span className="phase-indicator phase-stopped">STOPPED</span>
        </header>
        <section className="stopped-panel" aria-live="polite">
          <Power size={24} aria-hidden="true" />
          <h2>服务已停止</h2>
        </section>
      </main>
    );
  }

  if (phase === "setup" && setupStatus) {
    return <SetupWorkspace initialStatus={setupStatus} onComplete={handleSetupComplete} />;
  }

  const queryable = status?.projection_state === "current" || status?.projection_state === "stale";
  const activeCatalogView = view === "overview" || view === "discovery" || view === "processing" || view === "agent" || view === "reading" || view === "query" || view === "organization" || view === "tags" || view === "screening" || view === "synthesis" || view === "obsidian" || view === "exchange" ? null : catalogViews[view];

  function openReadingPaper(paperId: string) {
    setReadingPaperIds([paperId]);
    setView("reading");
  }

  function toggleComparisonPaper(paperId: string) {
    setReadingPaperIds((current) => {
      if (current.includes(paperId)) return current.filter((item) => item !== paperId);
      return current.length < 4 ? [...current, paperId] : current;
    });
  }

  return (
    <div className="product-shell">
      <header className="product-header">
        <div className="product-brand">
          <div className="brand-mark" aria-hidden="true"><Database size={20} /></div>
          <div><h1>Research KB</h1><p>Local Workspace Manager</p></div>
        </div>
        <div className="workspace-switcher">
          <label htmlFor="workspace-option">工作区</label>
          <select id="workspace-option" value={selected} onChange={(event) => setSelected(event.target.value)} disabled={busy}>
            {workspaces.map((workspace) => <option key={workspace.option_id} value={workspace.option_id}>{workspace.label}</option>)}
          </select>
          <button className="secondary-button compact-button" type="button" onClick={handleOpen} disabled={busy || !selected}>
            <FolderOpen size={17} aria-hidden="true" />
            打开
          </button>
        </div>
        <div className="header-actions">
          <span className={`header-status projection-${status?.projection_state ?? "missing"}`}>{status?.projection_state ?? "not selected"}</span>
          <button className="icon-button" type="button" onClick={handleRebuild} disabled={busy || !status || status.operation?.state === "building"} title="重建索引" aria-label="重建索引">
            <RefreshCw size={18} className={status?.operation?.state === "building" ? "spin" : ""} />
          </button>
          <button className="icon-button danger-button" type="button" onClick={handleShutdown} disabled={busy} title="停止服务" aria-label="停止服务">
            <Power size={18} />
          </button>
        </div>
      </header>

      <nav className="product-nav" aria-label="主要视图">
        {navigation.map((item) => {
          const Icon = item.icon;
          const operationalView = item.id === "overview" || item.id === "discovery" || item.id === "processing" || item.id === "agent" || item.id === "reading" || item.id === "query" || item.id === "tags" || item.id === "screening" || item.id === "synthesis" || item.id === "obsidian" || item.id === "exchange";
          const disabled = !activeWorkspace || (!operationalView && !queryable);
          return (
            <button
              key={item.id}
              type="button"
              className={view === item.id ? "nav-active" : ""}
              onClick={() => setView(item.id)}
              disabled={disabled}
              aria-current={view === item.id ? "page" : undefined}
              title={item.label}
            >
              <Icon size={18} aria-hidden="true" />
              <span>{item.label}</span>
            </button>
          );
        })}
      </nav>

      <main className="product-main">
        {!activeWorkspace || !status ? (
          <section className="workspace-empty" aria-labelledby="workspace-empty-title">
            <FolderOpen size={28} aria-hidden="true" />
            <div><p className="section-kicker">WORKSPACE</p><h2 id="workspace-empty-title">只读工作区</h2></div>
          </section>
        ) : view === "overview" ? (
          <Overview workspace={activeWorkspace} status={status} capabilities={capabilities} health={health} error={panelError} />
        ) : view === "discovery" ? (
          <DiscoveryView />
        ) : view === "processing" ? (
          <ProcessingView onCatalogStatus={setStatus} onHealth={setHealth} />
        ) : view === "agent" ? (
          <AgentView onCatalogStatus={setStatus} onHealth={setHealth} />
        ) : view === "reading" ? (
          <ReadingWorkspace paperIds={readingPaperIds} onRemovePaper={toggleComparisonPaper} />
        ) : view === "query" ? (
          <KnowledgeQueryView
            paperIds={readingPaperIds}
            onRemovePaper={toggleComparisonPaper}
            onCatalogStatus={setStatus}
            onHealth={setHealth}
          />
        ) : view === "organization" ? (
          <ResearchOrganizationView
            initialPaperIds={readingPaperIds}
            onCatalogStatus={setStatus}
            onHealth={setHealth}
          />
        ) : view === "tags" ? (
          <TagsView onCatalogStatus={setStatus} onHealth={setHealth} />
        ) : view === "screening" ? (
          <ScreeningView initialPaperIds={readingPaperIds} onCatalogStatus={setStatus} onHealth={setHealth} />
        ) : view === "synthesis" ? (
          <ResearchSynthesisView onCatalogStatus={setStatus} onHealth={setHealth} />
        ) : view === "obsidian" ? (
          <ObsidianView />
        ) : view === "exchange" ? (
          <ExchangeView />
        ) : activeCatalogView ? (
          <>
            {view === "health" && health && (
              <div className="health-band" aria-label="运行状态">
                <span>process:{String(health.process_ready)}</span>
                <span>core:{String(health.core_compatible)}</span>
                <span>operation:{health.operation.state}</span>
              </div>
            )}
            <CatalogBrowser
              view={activeCatalogView}
              projectionState={status.projection_state}
              refreshKey={refreshKey}
              readingPaperIds={readingPaperIds}
              onOpenPaper={view === "library" ? openReadingPaper : undefined}
              onToggleComparison={view === "library" ? toggleComparisonPaper : undefined}
            />
          </>
        ) : null}
        {error && <div className="error-banner" role="alert">{error}</div>}
      </main>
    </div>
  );
}

function errorMessage(caught: unknown): string {
  if (caught instanceof ApiError) return `${caught.code}: ${caught.message}`;
  return "请求未完成";
}

export default App;
