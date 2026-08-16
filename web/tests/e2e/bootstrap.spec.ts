import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { access, mkdir, readdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { expect, test, type Locator, type Page } from "@playwright/test";

test("run deterministic intake and browse product surfaces on desktop and mobile", async ({ page }) => {
  test.setTimeout(900_000);
  const url = process.env.RKB_E2E_URL;
  const token = process.env.RKB_E2E_TOKEN;
  const workspace = process.env.RKB_E2E_WORKSPACE;
  const exchangeWorkspace = process.env.RKB_E2E_EXCHANGE_WORKSPACE;
  const python = process.env.RKB_E2E_PYTHON;
  const obsidianVault = process.env.RKB_E2E_OBSIDIAN_VAULT;
  if (!url || !token || !workspace || !exchangeWorkspace || !python || !obsidianVault) throw new Error("E2E startup facts are unavailable");
  const scientificBefore = await scientificDigests(path.join(workspace, "knowledge"));
  const screenshotRoot = process.env.RKB_E2E_SCREENSHOT_DIR;

  await page.context().grantPermissions(["clipboard-read", "clipboard-write"], {
    origin: new URL(url).origin,
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(url);
  await expect(page.getByRole("heading", { name: "Research KB" })).toBeVisible();
  await page.getByLabel("一次性 Token").fill(token);
  await page.getByRole("button", { name: "验证" }).click();
  await expect(page.getByRole("option", { name: "P2 Small Synthetic" })).toBeAttached();

  await page.getByLabel("工作区", { exact: true }).selectOption("p2-small");
  await page.getByRole("button", { name: "打开" }).click();
  await expect(page.getByRole("heading", { name: "P2 Small Synthetic" })).toBeVisible();
  await expect(page.locator(".header-status")).toHaveText("missing");
  await page.getByRole("button", { name: "重建索引" }).click();
  await expect(page.locator(".header-status")).toHaveText("current", { timeout: 15_000 });
  const catalogCount = Number(await page.locator(".metric-item").filter({ hasText: "Catalog items" }).locator("strong").innerText());
  expect(catalogCount).toBeGreaterThan(0);

  await page.getByRole("button", { name: "处理" }).click();
  await expect(page.getByRole("heading", { name: "文献处理" })).toBeVisible();
  await page.getByLabel("PDF 文件").setInputFiles({
    name: "e2e-upload-primary.pdf",
    mimeType: "application/pdf",
    buffer: syntheticPdfBytes("Synthetic E2E uploaded primary paper."),
  });
  await page.getByLabel("标题").fill("E2E uploaded primary");
  await page.getByRole("button", { name: "开始处理" }).click();
  await approveTrustedParse(page);
  await expect(page.getByTitle("primary_semantic_gate").first()).toHaveText("原始研究语义闸门", { timeout: 60_000 });
  await expect(page.getByText("用途能力", { exact: true })).toBeVisible();
  await expect(page.locator(".header-status")).toHaveText("current", { timeout: 20_000 });

  await page.getByRole("button", { name: "收件箱" }).click();
  await expect(page.getByText("e2e-inbox-review.pdf", { exact: true })).toBeVisible();
  await page.getByRole("radio", { name: /e2e-inbox-review\.pdf/ }).check();
  await page.getByRole("button", { name: "综述" }).click();
  await page.getByLabel("标题").fill("E2E inbox review");
  await page.getByRole("button", { name: "开始处理" }).click();
  await approveTrustedParse(page);
  await expect(page.getByTitle("review_semantic_gate").first()).toHaveText("综述语义闸门", { timeout: 60_000 });
  await expect(page.locator(".job-table tbody tr")).toHaveCount(2);
  await expect(page.locator(".operation-chip")).toHaveText("就绪", { timeout: 20_000 });
  await expect(page.locator(".job-table .job-status-completed")).toHaveCount(2);
  await expect(page.getByText("用途能力", { exact: true })).toBeVisible();
  await expect(page.getByTitle("watched_inbox")).toHaveText("收件箱");
  await expect(page.getByText("用途 基础综述阅读记忆", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Agent" }).click();
  await expect(page.getByRole("heading", { name: "Agent 工作台" })).toBeVisible();
  const primaryJobOption = page.locator("#agent-job option").filter({ hasText: /primary_semantic_gate$/ }).first();
  const primaryJobId = await primaryJobOption.getAttribute("value");
  expect(primaryJobId).toBeTruthy();
  await page.getByLabel("Pipeline Job").selectOption(primaryJobId!);
  await expect(page.getByLabel("Task kind")).toHaveValue("primary_semantic_processing");
  await page.getByRole("button", { name: "创建 Task" }).click();
  await expect(page.getByRole("status")).toHaveText("Task 已创建", { timeout: 20_000 });

  const adequacyDecision = spawnSync(
    python,
    [
      path.join(process.cwd(), "tests", "accept_synthetic_source_adequacy.py"),
      path.join(workspace, "workspace.yaml"),
      "E2E uploaded primary",
    ],
    { cwd: process.cwd(), encoding: "utf8" },
  );
  expect(adequacyDecision.status, adequacyDecision.stderr).toBe(0);
  expect(JSON.parse(adequacyDecision.stdout).capability_status).toBe("yes");

  await page.getByRole("button", { name: "预览 Payload" }).click();
  await expect(page.getByText("Payload 已就绪", { exact: true })).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: "生成 Prompt" }).click();
  const primaryPrompt = page.locator(".prompt-block pre");
  await expect(primaryPrompt).toContainText("PAYLOAD_JSON", { timeout: 30_000 });
  const primaryPromptText = await primaryPrompt.innerText();
  const primaryTaskId = textField(primaryPromptText, "task_id");
  const primaryInputBasisDigest = textField(primaryPromptText, "input_basis_digest");
  await page.getByLabel("Agent JSON").fill(JSON.stringify(
    primaryCandidate(
      primaryTaskId,
      primaryInputBasisDigest,
      "Synthetic E2E uploaded primary paper.",
    ),
    null,
    2,
  ));
  await page.getByRole("button", { name: "导入结果" }).click();
  await expect(page.getByText("候选已暂存", { exact: true })).toBeVisible({ timeout: 60_000 });
  await page.getByRole("button", { name: "批准写入" }).click();
  await expect(page.getByText("候选已批准", { exact: true })).toBeVisible({ timeout: 60_000 });
  await expect(page.locator(".agent-handoff-pane .status-badge")).toHaveText("approved");

  const reviewJobOption = page.locator("#agent-job option").filter({ hasText: /review_semantic_gate$/ }).first();
  const reviewJobId = await reviewJobOption.getAttribute("value");
  expect(reviewJobId).toBeTruthy();
  await page.getByLabel("Pipeline Job").selectOption(reviewJobId!);
  await expect(page.getByLabel("Task kind")).toHaveValue("review_semantic_processing");
  await page.getByRole("button", { name: "创建 Task" }).click();
  await expect(page.getByRole("status")).toHaveText("Task 已创建", { timeout: 20_000 });

  await page.getByRole("button", { name: "预览 Payload" }).click();
  await expect(page.getByText("Payload 已就绪", { exact: true })).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: "生成 Prompt" }).click();
  const prompt = page.locator(".prompt-block pre");
  await expect(prompt).toContainText("PAYLOAD_JSON", { timeout: 30_000 });
  await expect(prompt).toContainText('"result_contract_schema"');
  const promptText = await prompt.innerText();
  const taskId = textField(promptText, "task_id");
  const inputBasisDigest = textField(promptText, "input_basis_digest");
  await page.getByLabel("Agent JSON").fill(JSON.stringify(zeroUnitReviewCandidate(taskId, inputBasisDigest), null, 2));
  await page.getByRole("button", { name: "导入结果" }).click();
  await expect(page.getByText("候选已暂存", { exact: true })).toBeVisible({ timeout: 60_000 });
  await expect(page.locator(".candidate-block pre")).toContainText("<script>untrusted review text</script>");
  expect(await page.locator(".candidate-block script").count()).toBe(0);
  await page.getByRole("button", { name: "批准写入" }).click();
  await expect(page.getByText("候选已批准", { exact: true })).toBeVisible({ timeout: 60_000 });
  await expect(page.locator(".agent-handoff-pane .status-badge")).toHaveText("approved");

  if (screenshotRoot) {
    const agentDesktop = await page.screenshot({ path: path.join(screenshotRoot, "agent-desktop.png"), fullPage: true });
    expect(agentDesktop.length).toBeGreaterThan(10_000);
  }

  await page.getByRole("button", { name: "处理" }).click();
  await page.getByRole("button", { name: "上传 PDF" }).click();
  await page.getByLabel("PDF 文件").setInputFiles({
    name: "e2e-upload-mixed.pdf",
    mimeType: "application/pdf",
    buffer: syntheticPdfBytes("Synthetic E2E uploaded mixed document."),
  });
  await page.getByRole("button", { name: "混合型" }).click();
  await page.getByLabel("标题").fill("E2E uploaded mixed document");
  await page.getByRole("button", { name: "开始处理" }).click();
  await approveTrustedParse(page);
  const mixedNode = page.getByTitle("review_semantic_gate_mixed_document").first();
  const pollingPaused = page.getByRole("alert").filter({ hasText: "自动刷新已暂停" });
  await expect(page.locator(".processing-view")).toContainText(
    /混合文献语义闸门|自动刷新已暂停/,
    { timeout: 60_000 },
  );
  if (await pollingPaused.isVisible()) {
    await page.getByRole("button", { name: "刷新任务" }).click();
  }
  await expect(mixedNode).toBeVisible({ timeout: 60_000 });
  await expect(page.locator(".job-table tbody tr")).toHaveCount(3);
  await expect(page.locator(".operation-chip")).toHaveText("就绪", { timeout: 20_000 });
  await expect(page.locator(".job-table .job-status-completed")).toHaveCount(3);
  const mixedJobRow = page.locator(".job-table tbody tr").filter({
    has: page.locator('[title="review_semantic_gate_mixed_document"]'),
  }).first();
  await mixedJobRow.getByRole("button").click();
  await expect(page.getByText("用途 基础综述阅读记忆", { exact: true })).toBeVisible();

  if (screenshotRoot) {
    await mkdir(screenshotRoot, { recursive: true });
    await page.locator(".product-main").evaluate((element) => element.scrollTo(0, 0));
    const processingDesktop = await page.screenshot({ path: path.join(screenshotRoot, "processing-desktop.png"), fullPage: true });
    expect(processingDesktop.length).toBeGreaterThan(10_000);
  }

  await page.getByRole("button", { name: "文献" }).click();
  await expect(page.getByRole("heading", { name: "论文与阅读产物" })).toBeVisible();
  await page.getByLabel("类型").selectOption("paper");
  await page.getByPlaceholder("搜索标题、摘要、ID 或状态").fill("Synthetic Primary Catalog Record 00000002");
  await page.getByRole("button", { name: "搜索" }).click();
  const paperRow = page.locator(".catalog-row-main").filter({ hasText: "Synthetic Primary Catalog Record 00000002" });
  await expect(paperRow).toBeVisible();
  await paperRow.click();
  await expect(page.getByText("record:current")).toBeVisible();
  await expect(page.getByText("paper_08c0dd81-5b44-4d2f-9d32-662fb3e15ae5", { exact: true }).first()).toBeVisible();

  const readingBefore = await scientificDigests(path.join(workspace, "knowledge"));
  await page.getByRole("button", { name: "打开阅读 Synthetic Primary Catalog Record 00000002" }).click();
  await expect(page.getByRole("heading", { name: "Synthetic Primary Catalog Record 00000002" })).toBeVisible();
  await expect(page.getByText("1. 研究背景与研究意义", { exact: true })).toBeVisible();
  await expect(page.getByText("7. 对于未来研究的展望", { exact: true })).toBeVisible();
  await expect(page.getByText("Synthetic Card Unit 1 for item 00000002.", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "查看 Evidence evidence_20cbe39d-3cba-4ba8-980f-bc6399026bf6" }).first().click();
  await expect(page.getByText("The fabricated intervention produced response token 00000002.", { exact: true })).toBeVisible();
  await expect(page.getByText("page:1:block:1", { exact: true })).toBeVisible();
  if (screenshotRoot) {
    await mkdir(screenshotRoot, { recursive: true });
    const primaryReading = await page.screenshot({ path: path.join(screenshotRoot, "reading-primary-desktop.png"), fullPage: true });
    expect(primaryReading.length).toBeGreaterThan(10_000);
  }
  await page.getByRole("button", { name: "关闭 Evidence" }).click();

  await page.getByRole("button", { name: "文献" }).click();
  await page.getByLabel("类型").selectOption("paper");
  await page.getByPlaceholder("搜索标题、摘要、ID 或状态").fill("E2E uploaded primary");
  await page.getByRole("button", { name: "搜索" }).click();
  const uploadedPrimaryRow = page.locator(".catalog-row-main").filter({ hasText: "E2E uploaded primary" }).first();
  await expect(uploadedPrimaryRow).toBeVisible();
  await uploadedPrimaryRow.click();
  await page.getByRole("button", { name: "打开阅读 E2E uploaded primary" }).click();
  await expect(page.getByRole("heading", { name: "E2E uploaded primary" })).toBeVisible();
  await page.locator(".evidence-link").first().click();
  await expect(page.getByText("Synthetic E2E uploaded primary paper.", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "打开 Evidence PDF" }).click();
  const pdfCanvas = page.getByLabel("Evidence PDF 第 1 页");
  await expect(pdfCanvas).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("已定位原文摘录", { exact: true })).toBeVisible({ timeout: 30_000 });
  await expect(page.locator('[data-testid="evidence-highlight"]')).toHaveCount(1);
  await expect.poll(() => nonWhiteCanvasPixels(pdfCanvas), { timeout: 30_000 }).toBeGreaterThan(20);
  const pdfDesktopOverflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(pdfDesktopOverflow).toBeLessThanOrEqual(0);
  if (screenshotRoot) {
    const pdfDesktop = await page.screenshot({ path: path.join(screenshotRoot, "evidence-pdf-desktop.png"), fullPage: true });
    expect(pdfDesktop.length).toBeGreaterThan(10_000);
  }

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(pdfCanvas).toBeVisible();
  const pdfMobileOverflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(pdfMobileOverflow).toBeLessThanOrEqual(0);
  if (screenshotRoot) {
    const pdfMobile = await page.screenshot({ path: path.join(screenshotRoot, "evidence-pdf-mobile.png"), fullPage: true });
    expect(pdfMobile.length).toBeGreaterThan(10_000);
  }
  await page.getByRole("button", { name: "关闭 Evidence" }).click();
  await page.setViewportSize({ width: 1440, height: 900 });

  await page.getByRole("button", { name: "文献" }).click();
  await page.getByLabel("类型").selectOption("paper");
  await page.getByPlaceholder("搜索标题、摘要、ID 或状态").fill("Synthetic Primary Catalog Record 00000002");
  await page.getByRole("button", { name: "搜索" }).click();
  await page.getByRole("button", { name: "加入比较 Synthetic Primary Catalog Record 00000002" }).click();
  const queryScientificBefore = await scientificDigests(path.join(workspace, "knowledge"));
  await page.getByRole("button", { name: "问答" }).click();
  await expect(page.getByRole("heading", { name: "知识库问答" })).toBeVisible();
  await expect(page.getByText("2", { exact: true }).first()).toBeVisible();
  await page.getByLabel("问题类型").selectOption("selected_paper_comparison");
  await page.getByRole("textbox", { name: "研究问题", exact: true }).fill("What bounded pattern do these two synthetic records share?");
  await page.getByRole("button", { name: "创建问答 Task" }).click();
  await expect(page.getByRole("status")).toHaveText("问答 Task 已创建", { timeout: 20_000 });
  await page.getByRole("button", { name: "预览 Payload" }).click();
  await expect(page.getByText("Payload 已就绪", { exact: true })).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: "生成 Prompt" }).click();
  const queryPrompt = page.locator(".query-handoff-pane .prompt-block pre");
  await expect(queryPrompt).toContainText("PAYLOAD_JSON", { timeout: 30_000 });
  const queryHandoff = JSON.parse(await queryPrompt.innerText()) as Record<string, unknown>;
  await page.getByLabel("Agent JSON").fill(JSON.stringify(knowledgeQueryCandidate(queryHandoff), null, 2));
  await page.getByRole("button", { name: "导入结果" }).click();
  await expect(page.getByText("报告候选已暂存", { exact: true })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("<script>Both synthetic records retain one bounded pattern.</script>", { exact: true })).toBeVisible();
  await expect(page.locator(".query-report script")).toHaveCount(0);
  await page.getByRole("button", { name: "接受报告" }).click();
  await expect(page.getByText("报告已接受；未写入 canonical scientific knowledge", { exact: true })).toBeVisible({ timeout: 60_000 });
  await expect(page.locator(".query-handoff-pane .status-badge")).toHaveText("approved");
  expect(await scientificDigests(path.join(workspace, "knowledge"))).toEqual(queryScientificBefore);
  await page.locator(".product-main").evaluate((element) => element.scrollTo(0, 0));
  const queryDesktopOverflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(queryDesktopOverflow).toBeLessThanOrEqual(0);
  if (screenshotRoot) {
    const queryDesktop = await page.screenshot({ path: path.join(screenshotRoot, "knowledge-query-desktop.png"), fullPage: true });
    expect(queryDesktop.length).toBeGreaterThan(10_000);
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => window.scrollTo(0, 0));
  const queryMobileOverflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(queryMobileOverflow).toBeLessThanOrEqual(0);
  if (screenshotRoot) {
    const queryMobile = await page.screenshot({ path: path.join(screenshotRoot, "knowledge-query-mobile.png"), fullPage: true });
    expect(queryMobile.length).toBeGreaterThan(10_000);
  }
  await page.setViewportSize({ width: 1440, height: 900 });

  const organizationBefore = await treeDigest(path.join(workspace, "knowledge", "organization", "directions"));
  await page.getByRole("button", { name: "研究组织" }).click();
  await expect(page.getByRole("heading", { name: "研究组织" })).toBeVisible();
  await expect(page.getByLabel("Paper IDs（每行一个，1-25）")).not.toHaveValue("");
  await page.getByLabel("Proposal goal").fill("Create one bounded synthetic E2E direction.");
  await page.getByRole("button", { name: "创建组织 Task" }).click();
  await expect(page.getByRole("status")).toHaveText("研究组织 Task 已创建", { timeout: 20_000 });
  await page.getByRole("button", { name: "预览 Payload" }).click();
  await expect(page.getByText("Payload 已就绪", { exact: true })).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: "生成 Prompt" }).click();
  const organizationPrompt = page.locator(".organization-view .query-handoff-pane pre").last();
  await expect(organizationPrompt).toContainText("p7b-organization-proposal@1.0", { timeout: 30_000 });
  const organizationHandoff = JSON.parse(await organizationPrompt.innerText()) as Record<string, unknown>;
  await page.getByLabel("Agent JSON").fill(JSON.stringify(organizationCandidate(organizationHandoff), null, 2));
  await page.getByRole("button", { name: "导入结果" }).click();
  await expect(page.getByText("组织候选已暂存", { exact: true })).toBeVisible({ timeout: 60_000 });
  await expect(page.locator(".organization-preview pre")).toContainText("Synthetic E2E direction");
  await page.getByRole("button", { name: "批准 revision" }).click();
  await expect(page.getByText("组织 revision 已批准并提交", { exact: true })).toBeVisible({ timeout: 60_000 });
  await expect(page.locator(".organization-view .status-badge")).toHaveText("approved");
  expect(await treeDigest(path.join(workspace, "knowledge", "organization", "directions"))).not.toBe(organizationBefore);
  const directionOption = page.locator("#organization-target option").filter({ hasText: "Synthetic E2E direction" });
  await expect(directionOption).toBeAttached({ timeout: 20_000 });
  const directionId = await directionOption.getAttribute("value");
  expect(directionId).toBeTruthy();

  await page.getByRole("tab", { name: "Field Map" }).click();
  await page.getByLabel("Proposal goal").fill("Create one bounded synthetic E2E field entry.");
  await page.getByRole("button", { name: "创建组织 Task" }).click();
  await expect(page.getByRole("status")).toHaveText("研究组织 Task 已创建", { timeout: 20_000 });
  await page.getByRole("button", { name: "预览 Payload" }).click();
  await expect(page.getByText("Payload 已就绪", { exact: true })).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: "生成 Prompt" }).click();
  const fieldMapPrompt = page.locator(".organization-view .query-handoff-pane pre").last();
  await expect(fieldMapPrompt).toContainText("p7b-organization-proposal@1.0", { timeout: 30_000 });
  const fieldMapHandoff = JSON.parse(await fieldMapPrompt.innerText()) as Record<string, unknown>;
  await page.getByLabel("Agent JSON").fill(JSON.stringify(fieldMapCandidate(fieldMapHandoff, directionId!), null, 2));
  await page.getByRole("button", { name: "导入结果" }).click();
  await expect(page.getByText("组织候选已暂存", { exact: true })).toBeVisible({ timeout: 60_000 });
  await page.getByRole("button", { name: "批准 revision" }).click();
  await expect(page.getByText("组织 revision 已批准并提交", { exact: true })).toBeVisible({ timeout: 60_000 });
  const fieldMapOption = page.locator("#organization-target option").filter({ hasText: "Synthetic E2E field entry" });
  await expect(fieldMapOption).toBeAttached({ timeout: 20_000 });
  const fieldMapId = await fieldMapOption.getAttribute("value");
  expect(fieldMapId).toBeTruthy();

  const paperId = "paper_08c0dd81-5b44-4d2f-9d32-662fb3e15ae5";
  const questionId = "question_272dfde3-ef0f-4205-b9a1-65623487637d";
  const screeningBefore = await treeDigest(path.join(workspace, "knowledge", "organization"));
  await page.getByRole("button", { name: "问题筛选" }).click();
  await expect(page.getByRole("heading", { name: "问题筛选" })).toBeVisible();
  await page.getByLabel("Question ID").fill(questionId);
  await page.getByLabel("标题").fill("Synthetic E2E criteria");
  await page.getByLabel("范围").fill("Generated fixture records only.");
  await page.getByLabel("纳入标准（每行一条）").fill("Include the selected synthetic Paper.");
  const saveCriteriaButton = page.getByRole("button", { name: "保存标准" });
  await expect(saveCriteriaButton).toBeEnabled({ timeout: 20_000 });
  await saveCriteriaButton.click();
  await expect(page.locator(".screening-view .notice-banner")).toHaveText("筛选标准已保存", { timeout: 20_000 });
  await expect(page.locator(".header-status")).toHaveText("current", { timeout: 20_000 });
  await page.getByRole("button", { name: "论文决策" }).click();
  await page.getByLabel("Paper ID").fill(paperId);
  await page.getByLabel("Disposition 1").selectOption("met");
  await page.getByLabel("Rationale 1").fill("The synthetic Paper meets the synthetic criterion.");
  await page.getByLabel("总体理由").fill("Question-specific synthetic inclusion.");
  await page.getByRole("button", { name: "保存决策" }).click();
  await expect(page.locator(".screening-view .notice-banner")).toHaveText("Question-specific decision 已保存", { timeout: 20_000 });
  await expect(page.locator(".header-status")).toHaveText("current", { timeout: 20_000 });
  expect(await treeDigest(path.join(workspace, "knowledge", "organization"))).not.toBe(screeningBefore);
  const screeningDesktopOverflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(screeningDesktopOverflow).toBeLessThanOrEqual(0);
  if (screenshotRoot) {
    const screeningDesktop = await page.screenshot({ path: path.join(screenshotRoot, "screening-desktop.png"), fullPage: true });
    expect(screeningDesktop.length).toBeGreaterThan(10_000);
  }
  await page.setViewportSize({ width: 390, height: 844 });
  const screeningMobileOverflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(screeningMobileOverflow).toBeLessThanOrEqual(0);
  if (screenshotRoot) {
    const screeningMobile = await page.screenshot({ path: path.join(screenshotRoot, "screening-mobile.png"), fullPage: true });
    expect(screeningMobile.length).toBeGreaterThan(10_000);
  }
  await page.setViewportSize({ width: 1440, height: 900 });

  await page.getByRole("button", { name: "标签", exact: true }).click();
  await expect(page.getByRole("heading", { name: "标签", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "新建标签" }).click();
  await page.getByLabel("标签名称").fill("Synthetic E2E Tag");
  await page.getByLabel("描述").fill("A deterministic four-target Tag.");
  await page.getByRole("button", { name: "创建标签" }).click();
  await expect(page.locator(".tags-view .notice-banner")).toHaveText("标签已创建", { timeout: 20_000 });

  for (const [index, [kind, target]] of [
    ["paper", paperId],
    ["direction", directionId!],
    ["field_map_entry", fieldMapId!],
    ["question", questionId],
  ].entries()) {
    await page.getByLabel("目标类型").selectOption(kind);
    await page.getByLabel("目标 ID").fill(target);
    await page.getByRole("button", { name: "建立关联" }).click();
    await expect(page.locator(".tags-view .notice-banner")).toHaveText("关联已建立", { timeout: 20_000 });
    await expect(page.locator(".tag-assignment-row")).toHaveCount(index + 1, { timeout: 20_000 });
    await expect(page.locator(".header-status")).toHaveText("current", { timeout: 20_000 });
  }
  await expect(page.locator(".tag-assignment-row")).toHaveCount(4);
  const tagList = await page.evaluate(async () => (await fetch("/api/tags?page_size=40")).json()) as { tags: Array<{ tag_id: string; name: string }> };
  const tagId = tagList.tags.find((tag) => tag.name === "Synthetic E2E Tag")?.tag_id;
  expect(tagId).toBeTruthy();
  const taggedPaperItemId = await catalogItemId(page, "paper", tagId!, "Synthetic Primary Catalog Record 00000002");
  expect(taggedPaperItemId).toBeTruthy();

  const tagsDesktopOverflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(tagsDesktopOverflow).toBeLessThanOrEqual(0);
  if (screenshotRoot) {
    const tagsDesktop = await page.screenshot({ path: path.join(screenshotRoot, "tags-desktop.png"), fullPage: true });
    expect(tagsDesktop.length).toBeGreaterThan(10_000);
  }
  await page.setViewportSize({ width: 390, height: 844 });
  const tagsMobileOverflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(tagsMobileOverflow).toBeLessThanOrEqual(0);
  if (screenshotRoot) {
    const tagsMobile = await page.screenshot({ path: path.join(screenshotRoot, "tags-mobile.png"), fullPage: true });
    expect(tagsMobile.length).toBeGreaterThan(10_000);
  }
  await page.setViewportSize({ width: 1440, height: 900 });

  await page.getByRole("button", { name: "文献" }).click();
  await page.getByLabel("标签", { exact: true }).selectOption({ label: "Synthetic E2E Tag" });
  await page.getByPlaceholder("搜索标题、摘要、ID 或状态").fill("Synthetic Primary Catalog Record 00000002");
  await page.getByRole("button", { name: "搜索" }).click();
  await expect(page.locator(".catalog-row-main").filter({ hasText: "Synthetic Primary Catalog Record 00000002" })).toBeVisible();

  await page.getByRole("button", { name: "研究组织" }).click();
  await page.getByLabel("标签筛选").selectOption({ label: "Synthetic E2E Tag" });
  await expect(page.locator("#organization-target option").filter({ hasText: "Synthetic E2E direction" })).toBeAttached();
  await page.getByRole("tab", { name: "Field Map" }).click();
  await expect(page.locator("#organization-target option").filter({ hasText: "Synthetic E2E field entry" })).toBeAttached();
  await page.getByRole("tab", { name: "Question" }).click();
  await expect(page.locator("#organization-target option").filter({ hasText: "Synthetic catalog Question 1" })).toBeAttached();

  await page.getByRole("button", { name: "标签", exact: true }).click();
  await page.getByRole("button", { name: /Synthetic E2E Tag/ }).click();
  await page.getByLabel("目标类型").selectOption("paper");
  await page.getByLabel("目标 ID").fill(paperId);
  await page.getByRole("button", { name: "移除关联" }).click();
  await expect(page.locator(".tags-view .notice-banner")).toHaveText("关联已移除", { timeout: 20_000 });
  await expect(page.locator(".tag-assignment-row")).toHaveCount(3, { timeout: 20_000 });
  await expect(page.locator(".header-status")).toHaveText("current", { timeout: 20_000 });
  const unfilteredPaperItemId = await catalogItemId(page, "paper", undefined, "Synthetic Primary Catalog Record 00000002");
  expect(unfilteredPaperItemId).toBe(taggedPaperItemId);

  await page.getByRole("button", { name: "文献" }).click();
  await page.getByLabel("标签", { exact: true }).selectOption({ label: "Synthetic E2E Tag" });
  await page.getByPlaceholder("搜索标题、摘要、ID 或状态").fill("Synthetic Primary Catalog Record 00000002");
  await page.getByRole("button", { name: "搜索" }).click();
  await expect(page.getByText("当前筛选没有文献记录", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "标签", exact: true }).click();
  await page.getByRole("button", { name: /Synthetic E2E Tag/ }).click();
  await page.getByRole("button", { name: "归档标签" }).click();
  await expect(page.locator(".tags-view .notice-banner")).toHaveText("标签已归档", { timeout: 20_000 });
  await expect(page.locator(".header-status")).toHaveText("current", { timeout: 20_000 });
  await page.getByLabel("包含已归档").check();
  await expect(page.getByRole("button", { name: /Synthetic E2E Tag/ })).toBeVisible();
  await expect(page.locator(".tag-status-archived")).toHaveText("archived");
  await page.getByRole("button", { name: "研究组织" }).click();
  const organizationDesktopOverflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(organizationDesktopOverflow).toBeLessThanOrEqual(0);
  if (screenshotRoot) {
    const organizationDesktop = await page.screenshot({ path: path.join(screenshotRoot, "organization-desktop.png"), fullPage: true });
    expect(organizationDesktop.length).toBeGreaterThan(10_000);
  }
  await page.setViewportSize({ width: 390, height: 844 });
  const organizationMobileOverflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(organizationMobileOverflow).toBeLessThanOrEqual(0);
  if (screenshotRoot) {
    const organizationMobile = await page.screenshot({ path: path.join(screenshotRoot, "organization-mobile.png"), fullPage: true });
    expect(organizationMobile.length).toBeGreaterThan(10_000);
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.getByRole("button", { name: "问答" }).click();
  await page.getByRole("button", { name: "移出 Synthetic Primary Catalog Record 00000002" }).click();

  await page.getByRole("button", { name: "文献" }).click();
  await page.getByLabel("类型").selectOption("paper");
  await page.getByPlaceholder("搜索标题、摘要、ID 或状态").fill("Synthetic Review Catalog Record 00000004");
  await page.getByRole("button", { name: "搜索" }).click();
  await expect(page.getByRole("button", { name: "加入比较 Synthetic Review Catalog Record 00000004" })).toBeVisible();
  await page.getByRole("button", { name: "加入比较 Synthetic Review Catalog Record 00000004" }).click();
  await page.getByRole("button", { name: "阅读", exact: true }).click();
  const comparisonColumns = page.locator('[data-testid="reading-paper-column"]');
  await expect(comparisonColumns).toHaveCount(2);
  await expect(comparisonColumns.nth(0).getByRole("heading", { name: "E2E uploaded primary" })).toBeVisible();
  await expect(comparisonColumns.nth(1).getByRole("heading", { name: "Synthetic Review Catalog Record 00000004" })).toBeVisible();
  await expect(comparisonColumns.nth(1).getByText("仅作背景").first()).toBeVisible();
  await expect(comparisonColumns.nth(1).getByText("Synthetic Review Unit 1 for item 00000004.", { exact: true })).toBeVisible();
  if (screenshotRoot) {
    const comparisonDesktop = await page.screenshot({ path: path.join(screenshotRoot, "reading-comparison-desktop.png"), fullPage: true });
    expect(comparisonDesktop.length).toBeGreaterThan(10_000);
  }

  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => window.scrollTo(0, 0));
  const readingOverflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(readingOverflow).toBeLessThanOrEqual(0);
  if (screenshotRoot) {
    const comparisonMobile = await page.screenshot({ path: path.join(screenshotRoot, "reading-comparison-mobile.png"), fullPage: true });
    expect(comparisonMobile.length).toBeGreaterThan(10_000);
  }
  const readingAfter = await scientificDigests(path.join(workspace, "knowledge"));
  expect(readingAfter).toEqual(readingBefore);
  await page.setViewportSize({ width: 1440, height: 900 });

  await page.getByRole("button", { name: "文献" }).click();
  await page.getByLabel("类型").selectOption("all");
  await page.getByPlaceholder("搜索标题、摘要、ID 或状态").fill("");
  await page.getByRole("button", { name: "搜索" }).click();
  await expect(page.getByRole("button", { name: "下一页", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "下一页", exact: true }).click();
  await expect(page.getByText("第 2 页", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "上一页", exact: true }).click();
  await expect(page.getByText("第 1 页", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "问题", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Question Mapping" })).toBeVisible();
  const questionRow = page.getByRole("button", { name: /Synthetic catalog Question 1/ });
  await expect(questionRow).toBeVisible();
  await questionRow.click();
  await expect(page.getByText("Question text")).toBeVisible();

  const p8ScientificBefore = await scientificDigests(path.join(workspace, "knowledge"));
  expect(p8ScientificBefore.step7).toBe(scientificBefore.step7);
  await page.getByRole("button", { name: "科研综合" }).click();
  await expect(page.getByRole("heading", { name: "科研综合与启发" })).toBeVisible();
  await page.getByLabel("Research Question").selectOption(questionId);
  await expect(page.getByText("not_fact: true", { exact: true })).toBeVisible();
  await expect(page.getByText("review_status: ai_draft", { exact: true })).toBeVisible();
  await expect(page.getByText("automation_status: pending", { exact: true })).toBeVisible();

  for (const [index, [label, candidateType]] of ([
    ["Synthesis", "synthesis"],
    ["Review Angle", "review_angle"],
    ["Insight", "insight"],
    ["Cross-View", "cross_view"],
  ] as const).entries()) {
    await page.getByRole("tab", { name: label }).click();
    await page.getByLabel("Maintenance goal").fill(`Create one bounded synthetic ${label}.`);
    await page.getByLabel("External Agent").selectOption(index % 2 === 0 ? "codex_cli" : "claude_code_cli");
    await page.getByRole("button", { name: "创建 Research Synthesis Task" }).click();
    await expect(page.locator(".synthesis-workspace .notice-banner")).toHaveText(
      "Research Synthesis Task 已创建",
      { timeout: 30_000 },
    );

    await page.getByRole("button", { name: "预览 Payload" }).click();
    await expect(page.locator(".synthesis-workspace .notice-banner")).toHaveText(
      "Payload 已就绪",
      { timeout: 30_000 },
    );
    await expect(page.getByRole("heading", { name: "Primary Card Units" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Canonical Evidence" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Review queue boundaries" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Review Memory background" })).toBeVisible();
    await expect(page.getByText("仅作背景，不进入事实支持", { exact: true })).toBeVisible();

    await page.getByRole("button", { name: "生成 Prompt" }).click();
    const manifestBlock = page.getByLabel("Agent handoff manifest");
    await expect(manifestBlock).toContainText("p8-agent-handoff@1.0", { timeout: 30_000 });
    const displayedManifest = JSON.parse(await manifestBlock.innerText()) as Record<string, unknown>;
    const egressPolicy = await page.evaluate(async () => {
      const response = await fetch("/api/egress/policy", { credentials: "same-origin" });
      if (!response.ok) throw new Error(`Egress policy request failed: ${response.status}`);
      return await response.json() as {
        status: string;
        clipboard: { history: string; cloud_sync: string };
      };
    });
    expect(egressPolicy.status).toBe("success");
    expect(["enabled", "disabled", "unknown"]).toContain(egressPolicy.clipboard.history);
    expect(["enabled", "disabled", "unknown"]).toContain(egressPolicy.clipboard.cloud_sync);
    await page.getByRole("button", { name: "复制 Prompt" }).click();
    const clipboardAllowed = egressPolicy.clipboard.history === "disabled"
      && egressPolicy.clipboard.cloud_sync === "disabled";
    if (clipboardAllowed) {
      await expect(page.locator(".synthesis-workspace .notice-banner")).toHaveText("Prompt 已复制");
      const copiedManifest = await page.evaluate(() => navigator.clipboard.readText());
      expect(JSON.parse(copiedManifest)).toEqual(displayedManifest);
    } else {
      await expect(page.locator(".synthesis-workspace .error-banner")).toContainText(
        "Restricted content cannot be copied",
      );
    }
    const handoff = displayedManifest;
    expect((handoff.payload as { maintenance_request: { candidate_type: string } }).maintenance_request.candidate_type).toBe(candidateType);

    await page.getByLabel("Agent JSON").fill(JSON.stringify(researchSynthesisCandidate(handoff), null, 2));
    await page.getByRole("button", { name: "导入结果" }).click();
    await expect(page.locator(".synthesis-workspace .notice-banner")).toHaveText(
      "Research Synthesis 候选已暂存",
      { timeout: 60_000 },
    );
    await expect(page.locator(".synthesis-preview-pane .synthesis-json")).toContainText("Synthetic E2E");
    await page.getByRole("button", { name: "批准候选" }).click();
    await expect(page.locator(".synthesis-workspace .notice-banner")).toHaveText(
      "Research Synthesis 候选已批准",
      { timeout: 60_000 },
    );
    await expect(page.locator(".header-status")).toHaveText("current", { timeout: 20_000 });
  }

  const p8ScientificAfter = await scientificDigests(path.join(workspace, "knowledge"));
  expect(p8ScientificAfter.step7).not.toBe(p8ScientificBefore.step7);
  for (const name of ["primary_bundles", "paper_cards", "evidence", "review_memories", "review_bundles", "review_queue", "questions"]) {
    expect(p8ScientificAfter[name]).toBe(p8ScientificBefore[name]);
  }
  await page.locator(".product-main").evaluate((element) => element.scrollTo(0, 0));
  const synthesisDesktopOverflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(synthesisDesktopOverflow).toBeLessThanOrEqual(0);
  if (screenshotRoot) {
    const synthesisDesktop = await page.screenshot({ path: path.join(screenshotRoot, "research-synthesis-desktop.png"), fullPage: true });
    expect(synthesisDesktop.length).toBeGreaterThan(10_000);
  }
  await page.setViewportSize({ width: 390, height: 844 });
  const synthesisMobileOverflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(synthesisMobileOverflow).toBeLessThanOrEqual(0);
  if (screenshotRoot) {
    const synthesisMobile = await page.screenshot({ path: path.join(screenshotRoot, "research-synthesis-mobile.png"), fullPage: true });
    expect(synthesisMobile.length).toBeGreaterThan(10_000);
  }
  await page.setViewportSize({ width: 1440, height: 900 });

  const personalSentinel = path.join(obsidianVault, "Research KB", "Personal", "personal-sentinel.md");
  const personalSentinelBefore = await readFile(personalSentinel);
  await page.getByRole("button", { name: "Obsidian" }).click();
  await expect(page.getByRole("heading", { name: "Obsidian 视图" })).toBeVisible();
  await expect(page.getByText("尚未生成视图")).toBeVisible();
  await page.getByLabel("文献库摘要表").check();
  await page.getByLabel("问题覆盖表").check();
  await page.getByRole("button", { name: "预览生成" }).click();
  await expect(page.getByText(/待更新 [1-9]/)).toBeVisible();
  await page.getByRole("button", { name: "生成视图" }).click();
  await expect(page.getByText("视图已生成", { exact: true })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("Home.md", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "预览同步" }).click();
  await expect(page.getByText(/新建 [1-9]/)).toBeVisible();
  await page.getByRole("button", { name: "同步到 Obsidian" }).click();
  await expect(page.getByText("Obsidian 同步完成", { exact: true })).toBeVisible({ timeout: 30_000 });
  const managedHome = path.join(obsidianVault, "Research KB", "Generated", "Home.md");
  const managedUnknown = path.join(obsidianVault, "Research KB", "Generated", "Personal.md");
  await access(managedHome);
  await access(path.join(obsidianVault, "Research KB", "Generated", ".research-kb-generated-view.json"));
  expect(await readFile(personalSentinel)).toEqual(personalSentinelBefore);

  const editedBytes = Buffer.from("user edited generated note\n", "utf8");
  const unknownBytes = Buffer.from("user unknown note\n", "utf8");
  await writeFile(managedHome, editedBytes);
  await writeFile(managedUnknown, unknownBytes);
  await page.getByRole("button", { name: "预览同步" }).click();
  await expect(page.getByRole("button", { name: "导出个人副本后同步" })).toBeVisible();
  await page.getByRole("button", { name: "导出个人副本后同步" }).click();
  await expect(page.getByText(/个人副本已导出，2 files/)).toBeVisible({ timeout: 30_000 });
  const personalFiles = await listFiles(path.join(obsidianVault, "Research KB", "Personal"));
  const exportedHome = personalFiles.find((file) => file.endsWith(path.join("Home.md")) && file !== personalSentinel);
  const exportedUnknown = personalFiles.find((file) => file.endsWith(path.join("Personal.md")));
  expect(exportedHome).toBeTruthy();
  expect(exportedUnknown).toBeTruthy();
  expect(await readFile(exportedHome!)).toEqual(editedBytes);
  expect(await readFile(exportedUnknown!)).toEqual(unknownBytes);
  expect(await readFile(personalSentinel)).toEqual(personalSentinelBefore);

  await writeFile(managedHome, Buffer.from("second managed edit\n", "utf8"));
  await page.getByRole("button", { name: "预览同步" }).click();
  await expect(page.getByRole("button", { name: "放弃受管修改并同步" })).toBeVisible();
  await page.getByRole("button", { name: "放弃受管修改并同步" }).click();
  await expect(page.getByText("Obsidian 同步完成", { exact: true })).toBeVisible({ timeout: 30_000 });
  expect((await readFile(managedHome)).includes(Buffer.from("second managed edit"))).toBe(false);
  expect(await readFile(personalSentinel)).toEqual(personalSentinelBefore);

  await page.locator(".product-main").evaluate((element) => element.scrollTo(0, 0));
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
  if (screenshotRoot) {
    const obsidianDesktop = await page.screenshot({ path: path.join(screenshotRoot, "obsidian-desktop.png"), fullPage: true });
    expect(obsidianDesktop.length).toBeGreaterThan(10_000);
  }
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
  if (screenshotRoot) {
    const obsidianMobile = await page.screenshot({ path: path.join(screenshotRoot, "obsidian-mobile.png"), fullPage: true });
    expect(obsidianMobile.length).toBeGreaterThan(10_000);
  }
  await page.setViewportSize({ width: 1440, height: 900 });

  const exchangeTargetBefore = await scientificDigests(path.join(exchangeWorkspace, "knowledge"));
  const exchangeArchiveRoot = path.join(path.dirname(exchangeWorkspace), "exchange-archives");
  await mkdir(exchangeArchiveRoot, { recursive: true });
  const sourceFreeArchive = path.join(exchangeArchiveRoot, "source-free.rkb-exchange.zip");
  const sourceInclusiveArchive = path.join(exchangeArchiveRoot, "source-inclusive.rkb-exchange.zip");

  await page.getByRole("button", { name: "交换" }).click();
  await expect(page.getByRole("heading", { name: "知识库交换" })).toBeVisible();
  await page.getByRole("button", { name: "预览导出" }).click();
  const sourceFreePreview = page.getByLabel("导出预览");
  await expect(sourceFreePreview).toBeVisible();
  await expect(sourceFreePreview).toContainText("缺失源");
  await page.getByRole("button", { name: "生成 Archive" }).click();
  const [sourceFreeDownload] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("link", { name: "下载" }).click(),
  ]);
  await sourceFreeDownload.saveAs(sourceFreeArchive);
  await expect(page.getByRole("button", { name: "已下载" })).toBeDisabled();

  await page.getByRole("button", { name: "论文", exact: true }).click();
  await page.getByRole("textbox", { name: "目标" }).fill("E2E uploaded primary");
  await page.getByRole("button", { name: "搜索" }).click();
  await expect(page.getByLabel("导出目标")).not.toHaveValue("");
  await page.getByText("包含 PDF", { exact: true }).click();
  await page.getByText("已确认本批次可再分发", { exact: true }).click();
  await page.getByRole("button", { name: "预览导出" }).click();
  const sourceInclusivePreview = page.getByLabel("导出预览");
  await expect(sourceInclusivePreview).toContainText("PDF");
  await expect(page.getByRole("button", { name: "生成 Archive" })).toBeEnabled();
  await page.getByRole("button", { name: "生成 Archive" }).click();
  const [sourceInclusiveDownload] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("link", { name: "下载" }).click(),
  ]);
  await sourceInclusiveDownload.saveAs(sourceInclusiveArchive);

  await page.getByLabel("工作区", { exact: true }).selectOption("p10-exchange-target");
  await page.getByRole("button", { name: "打开" }).click();
  await expect(page.getByRole("heading", { name: "P10 Exchange Target" })).toBeVisible();
  await page.getByRole("button", { name: "交换" }).click();
  for (const archivePath of [sourceFreeArchive, sourceInclusiveArchive]) {
    await page.getByLabel("选择 .rkb-exchange.zip").setInputFiles(archivePath);
    await page.getByRole("button", { name: "上传并预检" }).click();
    const importPreview = page.getByLabel("导入预览");
    await expect(importPreview).toContainText("supported", { timeout: 30_000 });
    await expect(importPreview).toContainText("external · local review pending");
    await page.getByRole("button", { name: "批准导入" }).click();
    await expect(page.getByRole("status")).toContainText(/外部知识包已导入|Archive 已存在/, { timeout: 30_000 });
  }
  await expect(page.getByRole("listitem")).toHaveCount(2);
  await page.getByRole("listitem").first().click();
  await expect(page.getByLabel("外部知识包详情")).toContainText("external · local review pending");
  await expect(page.getByLabel("外部知识包详情")).not.toContainText(/use as local fact/i);
  expect(await scientificDigests(path.join(exchangeWorkspace, "knowledge"))).toEqual(exchangeTargetBefore);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
  if (screenshotRoot) {
    const exchangeDesktop = await page.screenshot({ path: path.join(screenshotRoot, "exchange-desktop.png"), fullPage: true });
    expect(exchangeDesktop.length).toBeGreaterThan(10_000);
  }
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
  if (screenshotRoot) {
    const exchangeMobile = await page.screenshot({ path: path.join(screenshotRoot, "exchange-mobile.png"), fullPage: true });
    expect(exchangeMobile.length).toBeGreaterThan(10_000);
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.getByLabel("工作区", { exact: true }).selectOption("p2-small");
  await page.getByRole("button", { name: "打开" }).click();
  await expect(page.getByRole("heading", { name: "P2 Small Synthetic" })).toBeVisible();

  await page.getByRole("button", { name: "健康" }).click();
  await expect(page.getByRole("heading", { name: "运行记录与 Guardian" })).toBeVisible();
  await expect(page.getByText("process:true", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "总览" }).click();
  if (screenshotRoot) {
    await mkdir(screenshotRoot, { recursive: true });
    await page.screenshot({ path: path.join(screenshotRoot, "desktop.png"), fullPage: true });
  }

  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "Agent" }).click();
  await expect(page.getByRole("heading", { name: "Agent 工作台" })).toBeVisible();
  await expect(page.locator(".agent-task-list")).toContainText("approved", { timeout: 30_000 });
  await expect(page.locator(".agent-handoff-pane .status-badge")).toHaveText("approved", { timeout: 30_000 });
  await page.evaluate(() => window.scrollTo(0, 0));
  const agentOverflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(agentOverflow).toBeLessThanOrEqual(0);
  if (screenshotRoot) {
    const agentMobile = await page.screenshot({ path: path.join(screenshotRoot, "agent-mobile.png"), fullPage: true });
    expect(agentMobile.length).toBeGreaterThan(10_000);
  }

  await page.getByRole("button", { name: "处理" }).click();
  await expect(page.getByRole("heading", { name: "文献处理" })).toBeVisible();
  await expect(page.locator(".job-table tbody tr")).toHaveCount(3, { timeout: 30_000 });
  const mixedMobileJobRow = page.locator(".job-table tbody tr").filter({
    has: page.locator('[title="review_semantic_gate_mixed_document"]'),
  }).first();
  await mixedMobileJobRow.getByRole("button").click();
  await expect(page.getByText("用途能力", { exact: true })).toBeVisible({ timeout: 30_000 });
  await page.evaluate(() => window.scrollTo(0, 0));
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  if (screenshotRoot) {
    const processingMobile = await page.screenshot({ path: path.join(screenshotRoot, "processing-mobile.png"), fullPage: true });
    expect(processingMobile.length).toBeGreaterThan(10_000);
  }

  const scientificAfter = await scientificDigests(path.join(workspace, "knowledge"));
  expect(scientificAfter.primary_bundles).not.toEqual(scientificBefore.primary_bundles);
  expect(scientificAfter.review_bundles).not.toEqual(scientificBefore.review_bundles);
  for (const name of ["paper_cards", "evidence", "review_memories", "review_queue", "questions"]) {
    expect(scientificAfter[name]).toEqual(scientificBefore[name]);
  }
  expect(scientificAfter.step7).not.toEqual(scientificBefore.step7);
  await page.getByRole("button", { name: "停止服务" }).click();
  await expect(page.getByRole("heading", { name: "服务已停止" })).toBeVisible();
});

async function treeDigest(root: string): Promise<string> {
  const files = await listFiles(root);
  const hash = createHash("sha256");
  for (const file of files) {
    const relative = path.relative(root, file).split(path.sep).join("/");
    hash.update(relative);
    hash.update("\0");
    hash.update(await readFile(file));
    hash.update("\0");
  }
  return hash.digest("hex");
}

async function scientificDigests(knowledgeRoot: string): Promise<Record<string, string>> {
  const result: Record<string, string> = {};
  for (const name of ["primary_bundles", "paper_cards", "evidence", "review_memories", "review_bundles", "review_queue", "questions", "step7"]) {
    result[name] = await treeDigest(path.join(knowledgeRoot, name));
  }
  return result;
}

async function listFiles(root: string): Promise<string[]> {
  let entries;
  try {
    entries = await readdir(root, { withFileTypes: true });
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return [];
    throw error;
  }
  const files: string[] = [];
  for (const entry of entries) {
    const target = path.join(root, entry.name);
    if (entry.isDirectory()) files.push(...await listFiles(target));
    if (entry.isFile()) files.push(target);
  }
  return files.toSorted();
}

function syntheticPdfBytes(text: string): Buffer {
  const escaped = text.replaceAll("\\", "\\\\").replaceAll("(", "\\(").replaceAll(")", "\\)");
  const stream = Buffer.from(`BT /F1 12 Tf 72 720 Td (${escaped}) Tj ET`, "ascii");
  const objects = [
    Buffer.from("<< /Type /Catalog /Pages 2 0 R >>", "ascii"),
    Buffer.from("<< /Type /Pages /Kids [3 0 R] /Count 1 >>", "ascii"),
    Buffer.from("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>", "ascii"),
    Buffer.from("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>", "ascii"),
    Buffer.concat([Buffer.from(`<< /Length ${stream.length} >>\nstream\n`, "ascii"), stream, Buffer.from("\nendstream", "ascii")]),
  ];
  const parts = [Buffer.from("%PDF-1.4\n%synthetic\n", "ascii")];
  const offsets = [0];
  let length = parts[0].length;
  objects.forEach((object, index) => {
    offsets.push(length);
    const entry = Buffer.concat([Buffer.from(`${index + 1} 0 obj\n`, "ascii"), object, Buffer.from("\nendobj\n", "ascii")]);
    parts.push(entry);
    length += entry.length;
  });
  const xref = length;
  const table = [Buffer.from(`xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`, "ascii")];
  for (const offset of offsets.slice(1)) table.push(Buffer.from(`${String(offset).padStart(10, "0")} 00000 n \n`, "ascii"));
  table.push(Buffer.from(`trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`, "ascii"));
  return Buffer.concat([...parts, ...table]);
}

function textField(text: string, field: string): string {
  const match = text.match(new RegExp(`"${field}"\\s*:\\s*"([^"]+)"`));
  if (!match) throw new Error(`${field} is absent from the generated handoff prompt`);
  return match[1];
}

function zeroUnitReviewCandidate(taskId: string, inputBasisDigest: string) {
  return {
    contract_version: "p4c-review-semantic-candidate@1.0",
    task_id: taskId,
    input_basis_digest: inputBasisDigest,
    review_subtype: "narrative_review",
    review_subtype_source: "agent_high_confidence",
    review_subtype_reason: "The synthetic document is a secondary synthesis.",
    read_status: "targeted_read",
    scope_tags: ["synthetic_review"],
    one_sentence_reuse_value: "No reusable unit remains after provenance review.",
    memory_value: { status: "low_value", reason: "The synthetic source is redundant." },
    coverage_limits: {
      unread_sections: ["Synthetic appendix"],
      weakly_read_sections: [],
      reason: "The appendix was outside the targeted read.",
    },
    sections: [
      "review_objective_scope",
      "review_question_search_boundaries",
      "taxonomy_field_structure",
      "major_synthesis",
      "methods_metrics_guardrails",
      "gaps_frontiers",
      "primary_leads_reuse",
    ].map((section_id) => ({ section_id, units: [] })),
    non_reusable_notes: [{ content: "<script>untrusted review text</script>", reason: "promotional" }],
  };
}

function primaryCandidate(taskId: string, inputBasisDigest: string, quote: string) {
  const sectionIds = [
    "research_background_significance",
    "research_problem",
    "method_principle_advantages",
    "conclusions_applications",
    "innovation",
    "limitations",
    "future_outlook",
  ];
  const sourcePage = {
    pdf_page: 1,
    printed_page: null,
    section: "Synthetic results",
    figure_or_table: null,
  };
  return {
    contract_version: "p4b-primary-semantic-candidate@1.0",
    task_id: taskId,
    input_basis_digest: inputBasisDigest,
    evidence: [{
      alias: "ev_uploaded_result",
      claim: "The uploaded synthetic PDF contains the declared result sentence.",
      evidence_type: "reported_result",
      quote,
      source_page: sourcePage,
      locator: "page:1:block:1",
      support_scope: "The generated PDF sentence only.",
      what_it_does_not_support: ["Any real scientific conclusion"],
      requested_operation: "continuous_text_evidence",
    }],
    review_boundaries: [{
      alias: "bd_real_world",
      issue_type: "overclaim",
      claim_candidate: "The synthetic sentence is a real scientific result.",
      reason: "The source was generated only for deterministic E2E validation.",
      source_page: sourcePage,
      locator: "page:1:block:1",
      resolution_status: "needs_resolution",
    }],
    sections: sectionIds.map((sectionId) => ({
      section_id: sectionId,
      units: sectionId === "conclusions_applications"
        ? [{
            statement: "The uploaded synthetic PDF contains the declared result sentence.",
            statement_type: "reported_result",
            grounding_status: "grounded",
            evidence_aliases: ["ev_uploaded_result"],
            boundary_aliases: [],
            source_page: sourcePage,
            confidence: "high",
          }]
        : sectionId === "limitations"
          ? [{
              statement: "The generated sentence cannot support a real scientific conclusion.",
              statement_type: "limitation",
              grounding_status: "needs_resolution",
              evidence_aliases: [],
              boundary_aliases: ["bd_real_world"],
              source_page: sourcePage,
              confidence: "high",
            }]
          : [],
    })),
  };
}

function knowledgeQueryCandidate(handoff: Record<string, unknown>) {
  const payload = handoff.payload as {
    primary_papers: Array<{
      paper_id: string;
      card_units: Array<{ unit_id: string; evidence_ids: string[] }>;
    }>;
  };
  return {
    contract_version: "p5c-knowledge-query-report@1.0",
    task_id: String(handoff.task_id),
    input_basis_digest: String(handoff.input_basis_digest),
    query_type: "selected_paper_comparison",
    answer_blocks: [{
      block_role: "cross_paper_synthesis",
      text: "<script>Both synthetic records retain one bounded pattern.</script>",
      support_refs: payload.primary_papers.map((paper) => ({
        paper_id: paper.paper_id,
        card_unit_id: paper.card_units[0].unit_id,
        evidence_ids: [paper.card_units[0].evidence_ids[0]],
      })),
      background_refs: [],
      background_only: false,
    }],
    unresolved_items: ["No real scientific inference is made from synthetic fixtures."],
    persistence_status: "report_only",
    canonical_scientific_write: false,
  };
}

function researchSynthesisCandidate(handoff: Record<string, unknown>) {
  const payload = handoff.payload as {
    maintenance_request: {
      question_id: string;
      candidate_type: "synthesis" | "review_angle" | "insight" | "cross_view";
      maintenance_intent: "append" | "replace";
      target_candidate_id: string | null;
    };
    primary_support: Array<{
      paper_id: string;
      card_units: Array<{ unit_id: string }>;
    }>;
    existing_candidates: Array<{
      candidate: { candidate_id: string; type: string; candidate_status: string };
      freshness: { state: string };
    }>;
  };
  const candidateType = payload.maintenance_request.candidate_type;
  const paperCardBase = payload.primary_support.map((paper) => ({
    paper_id: paper.paper_id,
    card_unit_ids: paper.card_units.map((unit) => unit.unit_id),
  }));
  const common = {
    question_id: payload.maintenance_request.question_id,
    title: candidateType === "cross_view"
      ? `Synthetic E2E cross view ${"LONGIDENTIFIER".repeat(24)}`
      : `Synthetic E2E ${candidateType.replaceAll("_", " ")}`,
    candidate_status: "keep",
    rejection_rationale: null,
    analysis_operator: {
      synthesis: "aggregate",
      review_angle: "compare",
      insight: "hypothesis_generation",
      cross_view: "contrast",
    }[candidateType],
    trace_status: "traceable",
    paper_card_base: paperCardBase,
    missing_evidence: ["No real scientific evidence is represented by the synthetic fixture."],
    assumptions: ["The generated records are comparable only for deterministic validation."],
    risk: ["The synthetic output has no external scientific meaning."],
    testability: "Inspect the deterministic synthetic records and their provenance closure.",
    next_action: "Retain only as a P8 browser validation artifact.",
  };

  let typeSpecific: Record<string, unknown>;
  if (candidateType === "synthesis") {
    typeSpecific = {
      claim: "Both generated records retain one bounded synthetic response pattern.",
      scope: "The generated fixture records only.",
      agreement_pattern: "Direction agrees within the fabricated examples.",
      conflict_pattern: "No real scientific conflict is represented.",
      boundary_statement: "This Research Synthesis candidate is not a scientific fact.",
    };
  } else if (candidateType === "review_angle") {
    typeSpecific = {
      thesis: "Organize the generated records by response comparability and control completeness.",
      organizing_axes: ["response comparability", "control completeness"],
      included_clusters: ["bounded synthetic response"],
      excluded_scope: ["all real scientific settings"],
      why_this_angle_adds_value: "It separates fixture agreement from unsupported generalization.",
    };
  } else if (candidateType === "insight") {
    typeSpecific = {
      insight_type: "experimental_idea",
      hypothesis_or_idea: "One additional synthetic control could distinguish two fabricated explanations.",
      rationale: "The fixture records a bounded control gap.",
      falsification_condition: "The fabricated explanations remain indistinguishable.",
      minimum_test: "Add one deterministic synthetic control arm.",
    };
  } else {
    const sourceViews = payload.existing_candidates
      .filter((item) => item.freshness.state === "current" && ["keep", "revise"].includes(item.candidate.candidate_status))
      .map((item) => item.candidate.candidate_id)
      .slice(0, 2);
    if (!sourceViews.length) throw new Error("Cross-View handoff has no current source candidate");
    typeSpecific = {
      source_views: sourceViews,
      relation_type: "complements",
      why_interesting: "The current synthetic candidates expose complementary fixture views.",
      shared_dimension: "bounded response interpretation",
      non_equivalence_warning: "The generated views are not interchangeable factual claims.",
    };
  }

  return {
    contract_version: "p8-research-synthesis-proposal@1.0",
    task_id: String(handoff.task_id),
    input_basis_digest: String(handoff.input_basis_digest),
    candidate_type: candidateType,
    maintenance_intent: payload.maintenance_request.maintenance_intent,
    target_candidate_id: payload.maintenance_request.target_candidate_id,
    duplicate_disposition: payload.maintenance_request.maintenance_intent === "append" ? "distinct" : "updates_target",
    payload: { ...common, ...typeSpecific },
  };
}

function organizationCandidate(handoff: Record<string, unknown>) {
  const payload = handoff.payload as {
    proposal_request: { target_kind: string; target_id: string | null };
    primary_papers: Array<{ paper_id: string; card_units: Array<{ unit_id: string }> }>;
  };
  const source = payload.primary_papers.find((paper) => paper.card_units.length > 0);
  if (!source) throw new Error("Organization handoff has no admissible Card Unit");
  return {
    contract_version: "p7b-organization-proposal@1.0",
    task_id: String(handoff.task_id),
    input_basis_digest: String(handoff.input_basis_digest),
    target_kind: payload.proposal_request.target_kind,
    target_id: payload.proposal_request.target_id,
    proposal: {
      name: "Synthetic E2E direction",
      scope: "Generated records only.",
      status: "active",
      unit_links: [{
        source_kind: "primary",
        paper_id: source.paper_id,
        unit_id: source.card_units[0].unit_id,
        role: "factual_example",
        rationale: "One current grounded synthetic Unit is a bounded example.",
      }],
      gap_notes: ["No real scientific inference is made."],
    },
    duplicate_notes: [],
    unresolved_conflicts: [],
  };
}

function fieldMapCandidate(handoff: Record<string, unknown>, directionId: string) {
  const payload = handoff.payload as {
    proposal_request: { target_kind: string; target_id: string | null };
    primary_papers: Array<{ paper_id: string; card_units: Array<{ unit_id: string }> }>;
  };
  const source = payload.primary_papers.find((paper) => paper.card_units.length > 0);
  if (!source) throw new Error("Field Map handoff has no admissible Card Unit");
  return {
    contract_version: "p7b-organization-proposal@1.0",
    task_id: String(handoff.task_id),
    input_basis_digest: String(handoff.input_basis_digest),
    target_kind: "field_map_entry",
    target_id: payload.proposal_request.target_id,
    proposal: {
      title: "Synthetic E2E field entry",
      entry_type: "mechanism",
      definition: "Generated records only.",
      status: "active",
      consensus_level: "review_plus_primary_examples",
      direction_refs: [directionId],
      unit_links: [{
        source_kind: "primary",
        paper_id: source.paper_id,
        unit_id: source.card_units[0].unit_id,
        role: "factual_example",
        rationale: "One current grounded synthetic Unit is a bounded example.",
      }],
      aspect_notes: [],
    },
    duplicate_notes: [],
    unresolved_conflicts: [],
  };
}

async function catalogItemId(
  page: Page,
  itemKind: string,
  tagId: string | undefined,
  query: string,
): Promise<string | null> {
  return page.evaluate(async (request) => {
    const search = new URLSearchParams({
      query: request.query,
      item_kinds: request.itemKind,
      page_size: "20",
    });
    if (request.tagId) search.set("tag_id", request.tagId);
    const response = await fetch(`/api/catalog/items?${search.toString()}`);
    if (!response.ok) throw new Error(`Catalog lookup failed: ${response.status}`);
    const body = await response.json() as { items: Array<{ item_id: string }> };
    return body.items[0]?.item_id ?? null;
  }, { itemKind, tagId, query });
}

async function nonWhiteCanvasPixels(canvas: Locator): Promise<number> {
  return canvas.evaluate((element) => {
    const target = element as HTMLCanvasElement;
    const context = target.getContext("2d");
    if (!context || target.width === 0 || target.height === 0) return 0;
    const pixels = context.getImageData(0, 0, target.width, target.height).data;
    let count = 0;
    for (let index = 0; index < pixels.length; index += 4) {
      if (pixels[index + 3] > 0 && pixels[index] + pixels[index + 1] + pixels[index + 2] < 735) count += 1;
    }
    return count;
  });
}

async function approveTrustedParse(page: Page): Promise<void> {
  await expect(page.getByRole("heading", { name: "受监督解析批准" })).toBeVisible({ timeout: 60_000 });
  await page.getByRole("button", { name: "准备解析" }).click();
  await expect(page.getByText("解析准备已就绪，请确认后批准", { exact: true })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("pdfplumber-text-flow", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "批准并解析" }).click();
  await expect(page.getByText("解析请求已接收", { exact: true })).toBeVisible({ timeout: 30_000 });
}
