import { mkdir } from "node:fs/promises";
import path from "node:path";
import { expect, test } from "@playwright/test";


const candidateId = "discovery_a1111111-1111-4111-8111-111111111111";
const resultKey = "doi:10.0000/discovery.e2e";

test("run explicit discovery and acquisition actions on desktop and mobile", async ({ page }) => {
  test.setTimeout(120_000);
  const url = process.env.RKB_E2E_URL;
  const token = process.env.RKB_E2E_TOKEN;
  const screenshotRoot = process.env.RKB_E2E_SCREENSHOT_DIR;
  if (!url || !token) throw new Error("E2E startup facts are unavailable");

  let selected = false;
  let acquired = false;
  const actions: string[] = [];
  const report = discoveryReport();

  await page.route("**/api/discovery/**", async (route) => {
    const request = route.request();
    const requestUrl = new URL(request.url());
    const pathname = requestUrl.pathname;
    if (pathname === "/api/discovery/search") {
      actions.push("search");
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(report) });
      return;
    }
    if (pathname === "/api/discovery/select") {
      actions.push("select");
      selected = true;
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "success" }) });
      return;
    }
    if (pathname.endsWith("/resolve")) {
      actions.push("resolve");
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
        status: "success",
        candidate_id: candidateId,
        resolution_status: "auto_acquisition_eligible",
        access_basis: "repository_open_access",
        license_observation: "provider_oa_policy_no_license_text",
        manual_reason: null,
        persistent_writes: 0,
      }) });
      return;
    }
    if (pathname.endsWith("/acquire")) {
      actions.push("acquire");
      acquired = true;
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "success" }) });
      return;
    }
    if (pathname.endsWith("/intake-handoff")) {
      actions.push("handoff");
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
        status: "success",
        candidate_id: candidateId,
        registration: { state: "unregistered", paper_ids: [] },
        persistent_writes: 0,
      }) });
      return;
    }
    if (pathname === "/api/discovery/candidates") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
        status: "success",
        candidate_count: selected ? 1 : 0,
        page_size: 100,
        candidates: selected ? [candidate(acquired)] : [],
        next_cursor: null,
        persistent_writes: 0,
      }) });
      return;
    }
    await route.fallback();
  });

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(url);
  await page.getByLabel("一次性 Token").fill(token);
  await page.getByRole("button", { name: "验证" }).click();
  await page.getByLabel("工作区", { exact: true }).selectOption("p2-small");
  await page.getByRole("button", { name: "打开" }).click();
  await page.getByRole("button", { name: "发现" }).click();

  await page.getByLabel("起始日期").fill("2026-07-27");
  await page.getByLabel("结束日期").fill("2026-08-03");
  await page.getByLabel("标题关键词").fill("targeted degradation");
  await page.getByRole("button", { name: "搜索" }).click();
  await expect(page.getByText("Synthetic discovery E2E paper")).toBeVisible();
  expect(actions).toEqual(["search"]);

  await page.getByRole("checkbox", { name: /Synthetic discovery E2E paper/ }).check();
  await page.getByRole("button", { name: /保存所选/ }).click();
  await expect(page.getByRole("button", { name: "检查 OA" })).toBeVisible();
  expect(actions).toEqual(["search", "select"]);

  await page.getByRole("button", { name: "检查 OA" }).click();
  await expect(page.getByRole("button", { name: "下载 OA PDF" })).toBeVisible();
  expect(actions).toEqual(["search", "select", "resolve"]);

  await page.getByRole("button", { name: "下载 OA PDF" }).click();
  await expect(page.getByText("来源已就绪，等待单独纳入知识库")).toBeVisible();
  expect(actions).toEqual(["search", "select", "resolve", "acquire", "handoff"]);

  if (screenshotRoot) {
    await mkdir(screenshotRoot, { recursive: true });
    const desktop = await page.screenshot({ path: path.join(screenshotRoot, "discovery-desktop.png"), fullPage: true });
    expect(desktop.length).toBeGreaterThan(10_000);
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("heading", { name: "论文发现" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  if (screenshotRoot) {
    const mobile = await page.screenshot({ path: path.join(screenshotRoot, "discovery-mobile.png"), fullPage: true });
    expect(mobile.length).toBeGreaterThan(10_000);
  }

  await page.getByRole("button", { name: "停止服务" }).click();
  await expect(page.getByRole("heading", { name: "服务已停止" })).toBeVisible();
});

function candidate(isAcquired: boolean) {
  return {
    candidate_id: candidateId,
    result_key: resultKey,
    title: "Synthetic discovery E2E paper",
    doi: "10.0000/discovery.e2e",
    first_publication_date: "2026-08-01",
    paper_type: "article",
    full_text_status: "open_access",
    acquisition_status: isAcquired ? "acquired" : "not_started",
    target_question_ids: [],
    selection_context_count: 1,
  };
}

function discoveryReport() {
  return {
    status: "success",
    interface_version: "1.0",
    provider: "europe-pmc",
    provider_api_version: "synthetic-6.9",
    query: {
      date_from: "2026-07-27",
      date_until: "2026-08-03",
      title_keywords: ["targeted degradation"],
      abstract_keywords: [],
      keyword_mode: "any",
      include_preprints: true,
      max_results: 15,
    },
    provider_hit_count: 1,
    scanned_result_count: 1,
    returned_result_count: 1,
    truncated: false,
    persistent_writes: 0,
    results: [{
      result_key: resultKey,
      title: "Synthetic discovery E2E paper",
      authors: ["Alpha Researcher"],
      first_publication_date: "2026-08-01",
      journal_or_server: "Synthetic Journal",
      doi: "10.0000/discovery.e2e",
      paper_type: "article",
      publication_types: ["Journal Article"],
      abstract: "Targeted degradation discovery.",
      matched_keywords: ["targeted degradation"],
      match_location: "title",
      discovery_sources: [{ provider: "europe-pmc", source: "MED", record_id: "E2E-1" }],
      full_text_status: "open_access",
      version_relationship: { status: "unresolved", related_doi: null },
      possible_duplicate_result_keys: [],
    }],
  };
}
