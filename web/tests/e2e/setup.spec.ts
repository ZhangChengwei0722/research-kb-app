import { expect, test, type Route } from "@playwright/test";

type MockCall = {
  method: string;
  pathname: string;
  body: string;
};

const INTERFACE_VERSION = "research-kb-app-setup@1.0";
const leases = {
  workspace_parent: "selection_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  source_root: "selection_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  local_inbox: "selection_cccccccccccccccccccccccccccccccccccccccccccccccc",
} as const;

const selections = {
  workspace_parent: {
    lease_id: leases.workspace_parent,
    purpose: "workspace_parent",
    display_label: "研究工作区",
  },
  source_root: {
    lease_id: leases.source_root,
    purpose: "source_root",
    display_label: "TPD 综述来源",
  },
  local_inbox: {
    lease_id: leases.local_inbox,
    purpose: "local_inbox",
    display_label: "待导入 PDF",
  },
} as const;

test("completes the managed first-run workspace setup with opaque selections", async ({ page }) => {
  test.setTimeout(120_000);
  const url = process.env.RKB_E2E_URL;
  const token = process.env.RKB_E2E_TOKEN;
  if (!url || !token) throw new Error("E2E startup facts are unavailable");

  const calls: MockCall[] = [];
  const setupHandler = async (route: Route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    const body = request.postData() ?? "";
    calls.push({ method: request.method(), pathname, body });
    assertNoPrivatePathOrSecurityDescriptor(body);

    if (pathname === "/api/setup/status" && request.method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          status: "success",
          interface_version: INTERFACE_VERSION,
          mode: "first_run",
          profile_id: "default",
          current_revision_id: null,
          recovery_available: false,
        }),
      });
      return;
    }

    if (pathname === "/api/setup/select-folder" && request.method() === "POST") {
      const payload = JSON.parse(body) as { purpose?: string };
      const selection = payload.purpose && payload.purpose in selections
        ? selections[payload.purpose as keyof typeof selections]
        : null;
      if (!selection) {
        await route.fulfill({
          status: 400,
          contentType: "application/json",
          body: JSON.stringify({ detail: "unexpected folder purpose" }),
        });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          status: "success",
          interface_version: INTERFACE_VERSION,
          selection: {
            ...selection,
            capability_facts: {
              filesystem: "NTFS",
              local: true,
              reparse_free: true,
              acl_secure: true,
              accepted: true,
            },
            expires_in_seconds: 600,
          },
        }),
      });
      return;
    }

    if (pathname === "/api/setup/prepare-workspace" && request.method() === "POST") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          status: "success",
          interface_version: INTERFACE_VERSION,
          proposal_token: `setup_${"d".repeat(48)}`,
          preview_digest: "e".repeat(64),
          preview: {
            workspace_label: "TPD 知识库",
            workspace_name: "tpd-main-beta",
            source_root_ids: ["source-1"],
            external_source_root_count: 1,
            local_inbox: "existing_external_reference",
            expires_at: "2026-08-08T12:15:00Z",
          },
        }),
      });
      return;
    }

    if (pathname === "/api/setup/commit-workspace" && request.method() === "POST") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          status: "success",
          interface_version: INTERFACE_VERSION,
          workspace_id: "workspace_opaque",
          profile_revision_id: `profile-rev-${"f".repeat(32)}`,
          restart_required: true,
          result: "profile_committed",
        }),
      });
      return;
    }

    await route.fulfill({
      status: 500,
      contentType: "application/json",
      body: JSON.stringify({ detail: `unexpected setup request: ${pathname}` }),
    });
  };

  const workspacesHandler = async (route: Route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    const body = request.postData() ?? "";
    calls.push({ method: request.method(), pathname, body });
    assertNoPrivatePathOrSecurityDescriptor(body);
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ workspaces: [] }),
    });
  };

  await page.route("**/api/setup/**", setupHandler);
  await page.route("**/api/workspaces", workspacesHandler);
  try {
    await page.goto(url);
    await expect(page.getByRole("heading", { name: "Research KB" })).toBeVisible();
    await page.getByLabel("一次性 Token").fill(token);
    await page.getByRole("button", { name: "验证" }).click();

    await expect(page.getByRole("heading", { name: "工作区设置" })).toBeVisible();
    await expect(page.getByRole("navigation", { name: "主要视图" })).not.toBeVisible();
    await page.getByLabel("工作区名称").fill("TPD 知识库");
    await page.getByLabel("文件夹名称").fill("tpd-main-beta");

    await page.getByRole("button", { name: "选择" }).first().click();
    await expect(page.getByText(selections.workspace_parent.display_label, { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "添加文献来源" }).click();
    await expect(page.getByText(selections.source_root.display_label, { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "选择" }).last().click();
    await expect(page.getByText(selections.local_inbox.display_label, { exact: true })).toBeVisible();

    await page.getByRole("button", { name: "检查并预览" }).click();
    const preview = page.locator(".setup-review");
    await expect(preview).toContainText("TPD 知识库");
    await expect(preview).toContainText("tpd-main-beta");
    await expect(preview).toContainText("确认创建");
    const previewText = await preview.innerText();
    expect(previewText).toContain("文献来源");
    expect(previewText).toContain("导入目录");
    assertNoPrivatePathOrSecurityDescriptor(previewText);
    expect(previewText).not.toMatch(/selection_[0-9a-f]{48}|setup_[0-9a-f]{48}/);
    expect(calls.filter((call) => call.pathname === "/api/setup/commit-workspace")).toHaveLength(0);

    await page.getByRole("button", { name: "确认创建" }).click();
    await expect(page.getByRole("heading", { name: "重新打开应用" })).toBeVisible();

    const setupStatusCalls = calls.filter((call) => call.pathname === "/api/setup/status");
    const workspaceCalls = calls.filter((call) => call.pathname === "/api/workspaces");
    const folderCalls = calls.filter((call) => call.pathname === "/api/setup/select-folder");
    const prepareCalls = calls.filter((call) => call.pathname === "/api/setup/prepare-workspace");
    const commitCalls = calls.filter((call) => call.pathname === "/api/setup/commit-workspace");
    expect(setupStatusCalls).toHaveLength(1);
    expect(workspaceCalls).toHaveLength(1);
    expect(folderCalls).toHaveLength(3);
    expect(prepareCalls).toHaveLength(1);
    expect(commitCalls).toHaveLength(1);
    expect(folderCalls.map((call) => (JSON.parse(call.body) as { purpose: string }).purpose)).toEqual([
      "workspace_parent",
      "source_root",
      "local_inbox",
    ]);

    const preparePayload = JSON.parse(prepareCalls[0].body) as Record<string, unknown>;
    expect(preparePayload).toMatchObject({
      workspace_parent_lease_id: leases.workspace_parent,
      source_roots: [{ root_id: "source-1", selection_lease_id: leases.source_root }],
      local_inbox_lease_id: leases.local_inbox,
      workspace_name: "tpd-main-beta",
      workspace_label: "TPD 知识库",
    });
    expect(preparePayload).not.toHaveProperty("expires_at");
    const commitPayload = JSON.parse(commitCalls[0].body) as Record<string, unknown>;
    expect(commitPayload).toEqual({
      proposal_token: `setup_${"d".repeat(48)}`,
      preview_digest: "e".repeat(64),
    });
    for (const call of calls) assertNoPrivatePathOrSecurityDescriptor(call.body);

    const visibleText = await page.locator("body").innerText();
    assertNoPrivatePathOrSecurityDescriptor(visibleText);
    expect(visibleText).not.toMatch(/selection_[0-9a-f]{48}|setup_[0-9a-f]{48}/);
  } finally {
    await page.unroute("**/api/setup/**", setupHandler);
    await page.unroute("**/api/workspaces", workspacesHandler);
  }
});

function assertNoPrivatePathOrSecurityDescriptor(value: string): void {
  expect(value).not.toMatch(/[A-Za-z]:[\\/]/);
  expect(value).not.toMatch(/\\\\[A-Za-z0-9]/);
  expect(value).not.toMatch(/(?:^|[\s"'=])\/(?:Users|home|private|tmp|var|mnt|workspace|documents)\b/i);
  expect(value).not.toMatch(/(?:security_descriptor|sddl|raw_acl|acl_descriptor)/i);
  expect(value).not.toMatch(/\b[OGDS]:[A-Z](?:[^\s,}]*)/);
}
