import { afterEach, describe, expect, it, vi } from "vitest";
import {
  bootstrap,
  approveTrustedParse,
  approveOrganizationProposal,
  approveResearchSynthesisProposal,
  approveAgentResult,
  cancelIntakeJob,
  clearClientSecurityState,
  createOrganizationProposal,
  createResearchSynthesisProposal,
  createEvidencePdfHandle,
  evidencePdfUrl,
  getCatalogItem,
  getIntakeJob,
  getIntakeSourceAdequacyResolution,
  getOrganizationTarget,
  getObsidianStatus,
  getObsidianTargets,
  getPaperOrganizationContext,
  getResearchSynthesisCandidate,
  getResearchSynthesisLimits,
  getResearchSynthesisQuestionContext,
  getSetupStatus,
  getTag,
  listTags,
  listTargetTags,
  inspectAgentHandoff,
  listInboxCandidates,
  listIntakeJobs,
  listOrganizationTargets,
  listResearchSynthesisCandidates,
  listCatalogItems,
  resumeIntakeJob,
  prepareAgentHandoff,
  prepareTrustedParse,
  openIntakeSourceAdequacyReview,
  prepareWorkspaceSetup,
  selectSetupFolder,
  openEvidencePdfExternally,
  previewObsidianRender,
  previewObsidianSync,
  applyObsidianRender,
  applyObsidianSync,
  startInboxIntake,
  startUploadIntake,
  decideIntakeSourceAdequacyResolution,
  submitAgentResult,
  promoteTag,
  setTagAssignment,
  commitWorkspaceSetup,
  copyToClipboard,
  exportAgentTaskPackage,
  getEgressPolicy,
} from "../src/api";

describe("browser security handoff", () => {
  afterEach(() => {
    clearClientSecurityState();
    vi.unstubAllGlobals();
  });

  it("submits the startup token only in the bootstrap body", async () => {
    const calls: Array<[RequestInfo | URL, RequestInit | undefined]> = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push([input, init]);
      if (String(input).endsWith("/api/session/csrf")) {
        return new Response(JSON.stringify({ csrf_token: "csrf-value" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(JSON.stringify({ status: "success" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    await bootstrap("one-time-secret-value");

    expect(calls[0][0]).toBe("/api/session/bootstrap");
    expect(calls[0][1]?.body).toBe(JSON.stringify({ startup_token: "one-time-secret-value" }));
    expect(String(calls[0][0])).not.toContain("one-time-secret-value");
    expect(calls[1][0]).toBe("/api/session/csrf");
  });

  it("uses only opaque setup leases and preview identity in browser requests", async () => {
    const calls: Array<[RequestInfo | URL, RequestInit | undefined]> = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push([input, init]);
      if (String(input).endsWith("/api/session/csrf")) {
        return new Response(JSON.stringify({ csrf_token: "csrf-value" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(JSON.stringify({ status: "success", mode: "first_run", recovery_available: false }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    await bootstrap("one-time-secret-value");

    await getSetupStatus();
    await selectSetupFolder("workspace_parent", { allowNewChild: true, initialLocationId: "documents" });
    await prepareWorkspaceSetup({
      workspace_parent_lease_id: `selection_${"a".repeat(48)}`,
      source_roots: [{ root_id: "source-1", selection_lease_id: `selection_${"b".repeat(48)}` }],
      local_inbox_lease_id: `selection_${"c".repeat(48)}`,
      workspace_name: "tpd-main",
      workspace_label: "TPD Knowledge Base",
      idempotency_key: "setup-one",
    });
    await commitWorkspaceSetup(`setup_${"d".repeat(48)}`, "e".repeat(64));

    expect(calls[2][0]).toBe("/api/setup/status");
    expect(calls[3][0]).toBe("/api/setup/select-folder");
    expect(calls[4][0]).toBe("/api/setup/prepare-workspace");
    expect(calls[5][0]).toBe("/api/setup/commit-workspace");
    expect(String(calls.slice(2))).not.toMatch(/[A-Z]:\\|workspace_parent_path|source_root_path/i);
    expect(String(calls[4][1]?.body)).not.toContain("expires_at");
    expect(new Headers(calls[5][1]?.headers).get("x-rkb-csrf")).toBe("csrf-value");
  });

  it("serializes bounded catalog filters and repeated item kinds", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      void input;
      void init;
      return new Response(JSON.stringify({
        status: "success",
        query: "response timing",
        item_kinds: ["evidence", "paper"],
        page_size: 12,
        items: [],
        next_cursor: null,
        has_more: false,
        projection_state: "current",
        source_watermark: "digest",
      }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    await listCatalogItems({
      query: "response timing",
      itemKinds: ["evidence", "paper"],
      pageSize: 12,
      cursor: "cursor/value+safe=",
      tagId: "tag_one",
    });

    const requested = String(fetchMock.mock.calls[0][0]);
    const url = new URL(requested, "http://127.0.0.1");
    expect(url.pathname).toBe("/api/catalog/items");
    expect(url.searchParams.get("query")).toBe("response timing");
    expect(url.searchParams.getAll("item_kinds")).toEqual(["evidence", "paper"]);
    expect(url.searchParams.get("page_size")).toBe("12");
    expect(url.searchParams.get("cursor")).toBe("cursor/value+safe=");
    expect(url.searchParams.get("tag_id")).toBe("tag_one");
  });

  it("serializes Tag reads and CSRF-protected deterministic mutations", async () => {
    const calls: Array<[RequestInfo | URL, RequestInit | undefined]> = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push([input, init]);
      if (String(input).endsWith("/api/session/csrf")) {
        return new Response(JSON.stringify({ csrf_token: "csrf-value" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(JSON.stringify({ status: "success", tags: [], assignments: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    await bootstrap("one-time-secret-value");

    await listTags(true, 25, "cursor/value");
    await getTag("tag one/value");
    await listTargetTags("field_map_entry", "field one/value");
    await promoteTag({
      tag_id: "tag_one",
      name: "Revised",
      expected_revision_id: "tagrev_one",
    });
    await setTagAssignment({
      tag_id: "tag_one",
      target_kind: "question",
      target_id: "question_one",
      state: "removed",
      expected_revision_id: "taglinkrev_one",
    });

    expect(calls[2][0]).toBe("/api/tags?include_archived=true&page_size=25&cursor=cursor%2Fvalue");
    expect(calls[3][0]).toBe("/api/tags/tag%20one%2Fvalue");
    expect(calls[4][0]).toBe("/api/tag-targets/field_map_entry/field%20one%2Fvalue");
    expect(calls[5][0]).toBe("/api/tags/promote");
    expect(calls[5][1]?.body).toBe(JSON.stringify({ tag_id: "tag_one", name: "Revised", expected_revision_id: "tagrev_one" }));
    expect(calls[6][0]).toBe("/api/tag-assignments");
    expect(calls[6][1]?.body).toBe(JSON.stringify({
      tag_id: "tag_one",
      target_kind: "question",
      target_id: "question_one",
      state: "removed",
      expected_revision_id: "taglinkrev_one",
    }));
    expect(new Headers(calls[5][1]?.headers).get("x-rkb-csrf")).toBe("csrf-value");
    expect(new Headers(calls[6][1]?.headers).get("x-rkb-csrf")).toBe("csrf-value");
  });

  it("encodes the catalog item ID as one path segment", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      void input;
      void init;
      return new Response(JSON.stringify({
        status: "success",
        projection_state: "current",
        current_record_status: "current",
        item: {},
        detail: {},
      }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    await getCatalogItem("catalog_1234");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/catalog/items/catalog_1234",
      expect.objectContaining({ credentials: "same-origin" }),
    );
  });

  it("creates opaque Evidence PDF handles and opens only handle URLs", async () => {
    const calls: Array<[RequestInfo | URL, RequestInit | undefined]> = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push([input, init]);
      if (String(input).endsWith("/api/session/csrf")) {
        return new Response(JSON.stringify({ csrf_token: "csrf-value" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (String(input).endsWith("/open")) {
        return new Response(JSON.stringify({
          status: "success",
          reader: "updf",
          page_targeting: "manual",
          pdf_page: 3,
          locator: "page:3:char:20-50",
        }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      return new Response(JSON.stringify({
        status: "success",
        handle_id: "opaque+handle/value",
        evidence_id: "evidence primary",
        pdf_page: 3,
        expires_in_seconds: 900,
      }), { status: 200, headers: { "Content-Type": "application/json" } });
    });
    vi.stubGlobal("fetch", fetchMock);
    await bootstrap("one-time-secret-value");

    const issued = await createEvidencePdfHandle("evidence primary");
    await openEvidencePdfExternally(issued.handle_id);

    expect(calls[2][0]).toBe("/api/reading/evidence/evidence%20primary/source-handle");
    expect(calls[2][1]).toEqual(expect.objectContaining({ method: "POST", body: "{}" }));
    expect(calls[3][0]).toBe("/api/reading/pdf/opaque%2Bhandle%2Fvalue/open");
    expect(evidencePdfUrl(issued.handle_id)).toBe("/api/reading/pdf/opaque%2Bhandle%2Fvalue");
    expect(JSON.stringify(calls)).not.toContain("C:\\\\");
  });

  it("builds the exact upload envelope without exposing the browser filename", async () => {
    const calls: Array<[RequestInfo | URL, RequestInit | undefined]> = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push([input, init]);
      if (String(input).endsWith("/api/session/csrf")) {
        return new Response(JSON.stringify({ csrf_token: "csrf-value" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(JSON.stringify({ status: "accepted", operation: {} }), {
        status: 202,
        headers: { "Content-Type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    await bootstrap("one-time-secret-value");

    const file = new File(["%PDF-synthetic"], "private-browser-name.pdf", {
      type: "application/pdf",
    });
    await startUploadIntake(file, {
      idempotency_key: "upload-key",
      requested_operation: "basic_paper_card",
      document_route: "primary",
      route_reason: null,
      bibliography: { title: "Synthetic", authors: [], year: 2026, doi: null },
    });

    const [url, init] = calls.at(-1)!;
    const headers = new Headers(init?.headers);
    const body = init?.body as Blob;
    const rendered = await body.text();
    expect(url).toBe("/api/intake/upload");
    expect(headers.get("content-type")).toBe(body.type);
    expect(headers.get("x-rkb-csrf")).toBe("csrf-value");
    expect(body.type).toMatch(/^multipart\/form-data; boundary=research-kb-/);
    expect(rendered).toContain('name="metadata"\r\nContent-Type: application/json');
    expect(rendered).toContain('name="file"; filename="source.pdf"\r\nContent-Type: application/pdf');
    expect(rendered).not.toContain("private-browser-name.pdf");
  });

  it("serializes inbox, Job and CAS operations through bounded endpoints", async () => {
    const calls: Array<[RequestInfo | URL, RequestInit | undefined]> = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push([input, init]);
      if (String(input).endsWith("/api/session/csrf")) {
        return new Response(JSON.stringify({ csrf_token: "csrf-value" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      const payload = String(input).includes("/jobs/")
        ? { status: "success", pipeline: {} }
        : String(input).endsWith("/jobs?page_size=12")
          ? { status: "success", jobs: [], next_cursor: null }
          : String(input).includes("/inbox?")
            ? { status: "success", candidates: [] }
            : { status: "accepted", operation: {} };
      return new Response(JSON.stringify(payload), {
        status: init?.method === "POST" ? 202 : 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    await bootstrap("one-time-secret-value");

    await listInboxCandidates(12, 9);
    await startInboxIntake({
      candidate_token: "candidate-token",
      min_stable_age_seconds: 9,
      idempotency_key: "inbox-key",
      requested_operation: "basic_review_memory",
      document_route: "review",
      route_reason: null,
      bibliography: { title: null, authors: [], year: null, doi: null },
    });
    await listIntakeJobs(12);
    await getIntakeJob("job_1234");
    await resumeIntakeJob("job_1234", {
      expected_state_id: "jobstate_1234",
      expected_state_digest: "a".repeat(64),
      requested_operation: "basic_paper_card",
      document_route: "primary",
      route_reason: null,
      bibliography: { title: null, authors: [], year: null, doi: null },
    });
    await cancelIntakeJob("job_1234", {
      expected_state_id: "jobstate_1234",
      expected_state_digest: "a".repeat(64),
    });

    expect(String(calls[2][0])).toContain("/api/intake/inbox?max_entries=12&min_stable_age_seconds=9");
    expect(calls[3][0]).toBe("/api/intake/inbox/start");
    expect(calls[4][0]).toBe("/api/intake/jobs?page_size=12");
    expect(calls[5][0]).toBe("/api/intake/jobs/job_1234");
    expect(calls[6][0]).toBe("/api/intake/jobs/job_1234/resume");
    expect(calls[7][0]).toBe("/api/intake/jobs/job_1234/cancel");
    expect(new Headers(calls[7][1]?.headers).get("x-rkb-csrf")).toBe("csrf-value");
  });

  it("serializes the optional intake Job list filter without changing the default URL", async () => {
    const calls: Array<[RequestInfo | URL, RequestInit | undefined]> = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push([input, init]);
      if (String(input).endsWith("/api/session/csrf")) {
        return new Response(JSON.stringify({ csrf_token: "csrf-value" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(JSON.stringify({ status: "success", jobs: [], next_cursor: null, persistent_writes: 0 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    await bootstrap("one-time-secret-value");

    await listIntakeJobs(12);
    await listIntakeJobs(12, null, {
      requested_route: "local_source",
      requested_depth: "semantic_gate",
    });

    const listUrls = calls
      .map(([input]) => String(input))
      .filter((input) => input.includes("/api/intake/jobs?"));
    expect(listUrls).toEqual([
      "/api/intake/jobs?page_size=12",
      "/api/intake/jobs?page_size=12&requested_route=local_source&requested_depth=semantic_gate",
    ]);
  });

  it("submits only CAS identity and opaque lease fields for trusted Parse", async () => {
    const calls: Array<[RequestInfo | URL, RequestInit | undefined]> = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push([input, init]);
      if (String(input).endsWith("/api/session/csrf")) {
        return new Response(JSON.stringify({ csrf_token: "csrf-value" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(JSON.stringify({ status: "success" }), {
        status: init?.method === "POST" ? 202 : 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    await bootstrap("one-time-secret-value");

    await prepareTrustedParse("job 1234", {
      expected_state_id: "jobstate_1234",
      expected_state_digest: "a".repeat(64),
    });
    await approveTrustedParse("job 1234", {
      lease_token: "opaque-lease-token",
      aggregate_preview_digest: "b".repeat(64),
    });

    expect(calls[2][0]).toBe("/api/intake/jobs/job%201234/trusted-parse/prepare");
    expect(JSON.parse(String(calls[2][1]?.body))).toEqual({
      expected_state_id: "jobstate_1234",
      expected_state_digest: "a".repeat(64),
    });
    expect(calls[3][0]).toBe("/api/intake/jobs/job%201234/trusted-parse/approve");
    expect(JSON.parse(String(calls[3][1]?.body))).toEqual({
      lease_token: "opaque-lease-token",
      aggregate_preview_digest: "b".repeat(64),
    });
    expect(String(calls.slice(2))).not.toMatch(/source_ref|source_path|authority_id|parser_path|document_route/i);
    expect(new Headers(calls[3][1]?.headers).get("x-rkb-csrf")).toBe("csrf-value");
  });

  it("serializes intake Source Adequacy review with only Job CAS and opaque confirmation", async () => {
    const calls: Array<[RequestInfo | URL, RequestInit | undefined]> = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push([input, init]);
      if (String(input).endsWith("/api/session/csrf")) {
        return new Response(JSON.stringify({ csrf_token: "csrf-value" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(JSON.stringify({ status: "success" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    await bootstrap("one-time-secret-value");

    const expected = {
      expected_state_id: "jobstate_1234",
      expected_state_digest: "a".repeat(64),
    };
    await getIntakeSourceAdequacyResolution("job 1234");
    await openIntakeSourceAdequacyReview("job 1234", expected);
    await decideIntakeSourceAdequacyResolution("job 1234", {
      ...expected,
      action: "accept_uncertainty",
      confirmation_id: "confirmation-" + "c".repeat(32),
    });

    expect(calls[2][0]).toBe("/api/intake/jobs/job%201234/source-adequacy-resolution");
    expect(calls[3][0]).toBe("/api/intake/jobs/job%201234/source-adequacy-resolution/open");
    expect(calls[4][0]).toBe("/api/intake/jobs/job%201234/source-adequacy-resolution/decide");
    expect(JSON.parse(String(calls[3][1]?.body))).toEqual(expected);
    expect(JSON.parse(String(calls[4][1]?.body))).toEqual({
      ...expected,
      action: "accept_uncertainty",
      confirmation_id: "confirmation-" + "c".repeat(32),
    });
    expect(String(calls.slice(2))).not.toMatch(/source_ref|source_path|fingerprint|parser|receipt|basis_profile/i);
    expect(new Headers(calls[4][1]?.headers).get("x-rkb-csrf")).toBe("csrf-value");
  });

  it("serializes Agent handoff and result operations without a browser lease", async () => {
    const calls: Array<[RequestInfo | URL, RequestInit | undefined]> = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push([input, init]);
      if (String(input).endsWith("/api/session/csrf")) {
        return new Response(JSON.stringify({ csrf_token: "csrf-value" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(JSON.stringify({ status: "success", task: {} }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    await bootstrap("one-time-secret-value");

    const expected = {
      expected_state_id: "taskstate_1234",
      expected_state_digest: "a".repeat(64),
    };
    await inspectAgentHandoff("task_1234", { ...expected, executor_id: "codex_cli" });
    await prepareAgentHandoff("task_1234", { ...expected, executor_id: "codex_cli" });
    await submitAgentResult("task_1234", { ...expected, result: { contract_version: "synthetic" } });
    await approveAgentResult("task_1234", expected);

    expect(calls[2][0]).toBe("/api/agent/tasks/task_1234/inspect-handoff");
    expect(calls[3][0]).toBe("/api/agent/tasks/task_1234/handoff");
    expect(calls[4][0]).toBe("/api/agent/tasks/task_1234/submit");
    expect(calls[5][0]).toBe("/api/agent/tasks/task_1234/approve");
    for (const [, init] of calls.slice(2)) {
      expect(new Headers(init?.headers).get("x-rkb-csrf")).toBe("csrf-value");
      expect(String(init?.body)).not.toContain("lease");
    }
  });

  it("uses authenticated bounded egress routes without exposing a local path", async () => {
    const calls: Array<[RequestInfo | URL, RequestInit | undefined]> = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push([input, init]);
      if (String(input).endsWith("/api/session/csrf")) {
        return new Response(JSON.stringify({ csrf_token: "csrf-value" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(JSON.stringify({ status: "success" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    await bootstrap("one-time-secret-value");

    await getEgressPolicy();
    await copyToClipboard({
      action: "knowledge_query_answer",
      task_id: "task one/value",
      expected_state_id: "taskstate_1234",
      expected_state_digest: "a".repeat(64),
    });
    await exportAgentTaskPackage("task one/value", {
      expected_state_id: "taskstate_1234",
      expected_state_digest: "a".repeat(64),
      executor_id: "codex_cli",
      selection_lease_id: `selection_${"b".repeat(48)}`,
    });

    expect(calls[2][0]).toBe("/api/egress/policy");
    expect(calls[3][0]).toBe("/api/egress/clipboard");
    expect(calls[3][1]?.body).toBe(JSON.stringify({
      action: "knowledge_query_answer",
      task_id: "task one/value",
      expected_state_id: "taskstate_1234",
      expected_state_digest: "a".repeat(64),
    }));
    expect(calls[4][0]).toBe("/api/egress/agent-task-package/task%20one%2Fvalue");
    expect(calls[4][1]?.body).toBe(JSON.stringify({
      expected_state_id: "taskstate_1234",
      expected_state_digest: "a".repeat(64),
      executor_id: "codex_cli",
      selection_lease_id: `selection_${"b".repeat(48)}`,
    }));
    expect(new Headers(calls[3][1]?.headers).get("x-rkb-csrf")).toBe("csrf-value");
    expect(new Headers(calls[4][1]?.headers).get("x-rkb-csrf")).toBe("csrf-value");
    expect(JSON.stringify(calls)).not.toContain("C:\\\\");
    expect(JSON.stringify(calls)).not.toContain('"path"');
  });

  it("uses bounded organization reads and the dedicated approval endpoint", async () => {
    const calls: Array<[RequestInfo | URL, RequestInit | undefined]> = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push([input, init]);
      if (String(input).endsWith("/api/session/csrf")) {
        return new Response(JSON.stringify({ csrf_token: "csrf-value" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(JSON.stringify({ status: "success", task: {}, directions: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    await bootstrap("one-time-secret-value");

    await listOrganizationTargets("direction", 25);
    await getOrganizationTarget("field_map_entry", "field map/one");
    await getPaperOrganizationContext("paper one");
    await createOrganizationProposal({
      target_kind: "question",
      target_id: null,
      proposal_goal: "Create one bounded question.",
      paper_ids: ["paper_one"],
      include_review_background: false,
      executor_id: "codex_cli",
      approved_content_classes: ["paper_card_content", "research_routing_context", "operational_context"],
      idempotency_key: "organization-one",
    });
    await approveOrganizationProposal("task one", {
      expected_state_id: "taskstate_one",
      expected_state_digest: "a".repeat(64),
    });

    expect(calls[2][0]).toBe("/api/organization/directions?page_size=25");
    expect(calls[3][0]).toBe("/api/organization/field-map-entries/field%20map%2Fone");
    expect(calls[4][0]).toBe("/api/organization/papers/paper%20one/context");
    expect(calls[5][0]).toBe("/api/organization/proposals");
    expect(calls[6][0]).toBe("/api/organization/proposals/task%20one/approve");
    expect(new Headers(calls[6][1]?.headers).get("x-rkb-csrf")).toBe("csrf-value");
  });

  it("uses bounded Research Synthesis reads and the dedicated approval endpoint", async () => {
    const calls: Array<[RequestInfo | URL, RequestInit | undefined]> = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push([input, init]);
      if (String(input).endsWith("/api/session/csrf")) {
        return new Response(JSON.stringify({ csrf_token: "csrf-value" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(JSON.stringify({ status: "success", task: {}, candidates: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    await bootstrap("one-time-secret-value");

    await getResearchSynthesisLimits();
    await listResearchSynthesisCandidates({
      questionId: "question one",
      candidateType: "review_angle",
      freshness: "current",
      pageSize: 25,
      cursor: "candidate/one",
    });
    await getResearchSynthesisCandidate("candidate one/value");
    await getResearchSynthesisQuestionContext("question one/value");
    await createResearchSynthesisProposal({
      question_id: "question_one",
      candidate_type: "review_angle",
      maintenance_intent: "replace",
      target_candidate_id: "reviewangle_one",
      maintenance_goal: "Revise one bounded angle.",
      include_review_background: true,
      executor_id: "claude_code_cli",
      approved_content_classes: ["paper_card_content", "canonical_evidence", "research_routing_context", "research_synthesis", "operational_context", "review_background"],
      idempotency_key: "research-synthesis-one",
    });
    await approveResearchSynthesisProposal("task one", {
      expected_state_id: "taskstate_one",
      expected_state_digest: "b".repeat(64),
    });

    expect(calls[2][0]).toBe("/api/research-synthesis/limits");
    const listUrl = new URL(String(calls[3][0]), "http://127.0.0.1");
    expect(listUrl.pathname).toBe("/api/research-synthesis/candidates");
    expect(listUrl.searchParams.get("question_id")).toBe("question one");
    expect(listUrl.searchParams.get("candidate_type")).toBe("review_angle");
    expect(listUrl.searchParams.get("freshness")).toBe("current");
    expect(listUrl.searchParams.get("page_size")).toBe("25");
    expect(listUrl.searchParams.get("cursor")).toBe("candidate/one");
    expect(calls[4][0]).toBe("/api/research-synthesis/candidates/candidate%20one%2Fvalue");
    expect(calls[5][0]).toBe("/api/research-synthesis/questions/question%20one%2Fvalue/context");
    expect(calls[6][0]).toBe("/api/research-synthesis/proposals");
    expect(calls[7][0]).toBe("/api/research-synthesis/proposals/task%20one/approve");
    expect(new Headers(calls[6][1]?.headers).get("x-rkb-csrf")).toBe("csrf-value");
    expect(new Headers(calls[7][1]?.headers).get("x-rkb-csrf")).toBe("csrf-value");
  });

  it("uses only logical cursors, IDs, optional tables, opaque tokens, and closed continuations for Obsidian", async () => {
    const calls: Array<[RequestInfo | URL, RequestInit | undefined]> = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push([input, init]);
      if (String(input).endsWith("/api/session/csrf")) {
        return new Response(JSON.stringify({ csrf_token: "csrf-value" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(JSON.stringify({ status: "success", targets: [], entries: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    await bootstrap("one-time-secret-value");

    await getObsidianStatus(25, "Papers/_index.md");
    await getObsidianTargets();
    await previewObsidianRender(["library_summary", "question_coverage"]);
    await applyObsidianRender({
      preview_token: "opaque-render-token-0000000000000000",
      optional_tables: ["library_summary"],
      continuation: "render",
    });
    await previewObsidianSync("target-one");
    await applyObsidianSync({
      target_id: "target-one",
      preview_token: "opaque-sync-token-000000000000000000",
      continuation: "export_personal_copy_then_sync",
    });

    const statusUrl = new URL(String(calls[2][0]), "http://127.0.0.1");
    expect(statusUrl.pathname).toBe("/api/obsidian/status");
    expect(statusUrl.searchParams.get("page_size")).toBe("25");
    expect(statusUrl.searchParams.get("cursor")).toBe("Papers/_index.md");
    expect(calls[3][0]).toBe("/api/obsidian/targets");
    expect(JSON.parse(String(calls[4][1]?.body))).toEqual({ optional_tables: ["library_summary", "question_coverage"] });
    expect(JSON.parse(String(calls[5][1]?.body))).toEqual({
      preview_token: "opaque-render-token-0000000000000000",
      optional_tables: ["library_summary"],
      continuation: "render",
    });
    expect(JSON.parse(String(calls[6][1]?.body))).toEqual({ target_id: "target-one" });
    expect(JSON.parse(String(calls[7][1]?.body))).toEqual({
      target_id: "target-one",
      preview_token: "opaque-sync-token-000000000000000000",
      continuation: "export_personal_copy_then_sync",
    });
    const serializedBodies = calls.slice(4).map((item) => String(item[1]?.body)).join("\n");
    expect(serializedBodies).not.toMatch(/vault_root|managed_subtree|expected_state|manifest_digest|content_digest/i);
    expect(new Headers(calls[7][1]?.headers).get("x-rkb-csrf")).toBe("csrf-value");
  });
});
