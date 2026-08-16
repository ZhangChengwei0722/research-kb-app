import { Database, Layers3, ShieldCheck } from "lucide-react";
import type { CapabilityResult, CatalogStatus, HealthResult, WorkspaceOption } from "../api";

type OverviewProps = {
  workspace: WorkspaceOption;
  status: CatalogStatus;
  capabilities: CapabilityResult | null;
  health: HealthResult | null;
  error: string;
};

export function Overview({ workspace, status, capabilities, health, error }: OverviewProps) {
  const adapters = Array.isArray(capabilities?.catalog.adapters)
    ? capabilities.catalog.adapters
    : [];
  return (
    <section className="overview" aria-labelledby="overview-title">
      <header className="view-heading">
        <div>
          <p className="section-kicker">OVERVIEW</p>
          <h2 id="overview-title">{workspace.label}</h2>
        </div>
        <span className={`projection-chip projection-${status.projection_state}`}>projection:{status.projection_state}</span>
      </header>

      <div className="metric-band">
        <Metric icon={Database} label="Catalog items" value={String(status.item_count)} />
        <Metric icon={Layers3} label="Projection" value={status.projection_state} />
        <Metric icon={ShieldCheck} label="Core" value={health?.core_compatible ? "compatible" : "unknown"} />
      </div>

      {error && <div className="inline-error" role="alert">{error}</div>}

      <div className="overview-columns">
        <section className="overview-section">
          <div className="subsection-heading">
            <h3>Workspace</h3>
            <span className="authority-badge authority-canonical">canonical</span>
          </div>
          <dl className="overview-facts">
            <Fact label="Workspace ID" value={workspace.workspace_id} mono />
            <Fact label="Domain" value={workspace.domain_name} />
            <Fact label="Domain version" value={workspace.domain_version} />
            <Fact label="Scientific writes" value={String(capabilities?.app.canonical_scientific_writes ?? false)} />
          </dl>
        </section>

        <section className="overview-section">
          <div className="subsection-heading">
            <h3>Runtime</h3>
            <span className="authority-badge authority-operational">operational</span>
          </div>
          <dl className="overview-facts">
            <Fact label="Process ready" value={String(health?.process_ready ?? false)} />
            <Fact label="Workspace selected" value={String(health?.workspace_selected ?? true)} />
            <Fact label="Operation" value={status.operation?.state ?? "idle"} />
            <Fact label="Raw parsed text indexed" value={String(capabilities?.catalog.raw_parsed_text_indexed ?? false)} />
            <Fact label="Source watermark" value={status.source_watermark ?? "unavailable"} mono />
          </dl>
        </section>
      </div>

      <section className="adapter-section">
        <div className="subsection-heading">
          <h3>Catalog adapters</h3>
          <span className="authority-badge authority-projection">projection</span>
        </div>
        <div className="adapter-grid">
          {adapters.map((adapter, index) => {
            const record = adapter && typeof adapter === "object" && !Array.isArray(adapter) ? adapter : {};
            return (
              <div className="adapter-row" key={String(record.record_kind ?? index)}>
                <strong>{String(record.record_kind ?? "unknown")}</strong>
                <span>{String(record.adapter_version ?? "-")}</span>
              </div>
            );
          })}
          {adapters.length === 0 && <div className="list-state">Capability facts unavailable</div>}
        </div>
      </section>
    </section>
  );
}

function Metric({ icon: Icon, label, value }: { icon: typeof Database; label: string; value: string }) {
  return (
    <div className="metric-item">
      <Icon size={19} aria-hidden="true" />
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function Fact({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd className={mono ? "mono" : undefined}>{value}</dd>
    </div>
  );
}
