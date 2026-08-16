import { mkdir } from "node:fs/promises";
import path from "node:path";

import { expect, test, type Page } from "@playwright/test";


test("approve trusted Parse and resume an undecided route without parsing twice", async ({ page }) => {
  test.setTimeout(180_000);
  const url = process.env.RKB_E2E_URL;
  const token = process.env.RKB_E2E_TOKEN;
  const screenshotRoot = process.env.RKB_E2E_SCREENSHOT_DIR;
  if (!url || !token) throw new Error("E2E startup facts are unavailable");

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(url);
  await page.getByLabel("一次性 Token").fill(token);
  await page.getByRole("button", { name: "验证" }).click();
  await page.getByLabel("工作区", { exact: true }).selectOption("p2-small");
  await page.getByRole("button", { name: "打开" }).click();
  await page.getByRole("button", { name: "处理" }).click();

  await uploadSynthetic(page, "trusted-primary.pdf", "Trusted Parse E2E primary", "primary");
  await approveTrustedParse(page);
  await expect(page.getByTitle("primary_semantic_gate").first()).toHaveText("原始研究语义闸门", { timeout: 60_000 });

  await uploadSynthetic(page, "trusted-undecided.pdf", "Trusted Parse E2E undecided", "undecided");
  await approveTrustedParse(page);
  await expect(page.getByTitle("route_ambiguous").first()).toHaveText("文献路线待确认", { timeout: 60_000 });
  await page.getByRole("button", { name: "原始研究" }).click();
  await page.getByRole("button", { name: "继续处理" }).click();
  await expect(page.getByTitle("primary_semantic_gate").first()).toHaveText("原始研究语义闸门", { timeout: 60_000 });

  await uploadSynthetic(page, "trusted-review-uncertain.pdf", "Trusted Parse E2E uncertain review", "review", true);
  await approveTrustedParse(page);
  await expect(page.getByRole("heading", { name: "来源充分性处理" })).toBeVisible({ timeout: 60_000 });
  const acceptUncertainty = page.getByRole("button", { name: "接受当前限制" });
  await expect(acceptUncertainty).toBeDisabled();
  if (screenshotRoot) {
    await mkdir(screenshotRoot, { recursive: true });
    const desktop = await page.screenshot({
      path: path.join(screenshotRoot, "source-adequacy-resolution-desktop.png"),
      fullPage: true,
    });
    expect(desktop.length).toBeGreaterThan(10_000);
    await page.setViewportSize({ width: 390, height: 844 });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    const mobile = await page.screenshot({
      path: path.join(screenshotRoot, "source-adequacy-resolution-mobile.png"),
      fullPage: true,
    });
    expect(mobile.length).toBeGreaterThan(10_000);
    await page.setViewportSize({ width: 1440, height: 900 });
  }
  await page.getByRole("button", { name: "打开原文" }).click();
  await expect(page.getByText("原文已打开", { exact: true })).toBeVisible();
  await expect(acceptUncertainty).toBeEnabled();
  await acceptUncertainty.click();
  await expect(page.getByTitle("review_semantic_gate").first()).toHaveText("综述语义闸门", { timeout: 60_000 });

  await expect(page.locator(".job-table tbody tr")).toHaveCount(3);
  await expect(page.locator(".job-table .job-status-completed")).toHaveCount(3);
  await expect(page.locator("body")).not.toContainText("trusted_parse_");

  await page.getByRole("button", { name: "停止服务" }).click();
  await expect(page.getByRole("heading", { name: "服务已停止" })).toBeVisible();
});


async function uploadSynthetic(
  page: Page,
  name: string,
  title: string,
  route: "primary" | "review" | "undecided",
  uncertain = false,
): Promise<void> {
  await page.getByRole("button", { name: "上传 PDF" }).click();
  await page.getByLabel("PDF 文件").setInputFiles({
    name,
    mimeType: "application/pdf",
    buffer: uncertain ? syntheticUncertainPdfBytes(title) : syntheticPdfBytes(title),
  });
  await page.getByLabel("标题").fill(title);
  const routeLabel = route === "primary" ? "原始研究" : route === "review" ? "综述" : "暂不确定";
  await page.getByRole("button", { name: routeLabel }).click();
  await page.getByRole("button", { name: "开始处理" }).click();
}


async function approveTrustedParse(page: Page): Promise<void> {
  await expect(page.getByRole("heading", { name: "受监督解析批准" })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole("button", { name: "批准并解析" })).toBeDisabled();
  await page.getByRole("button", { name: "准备解析" }).click();
  await expect(page.getByText("解析准备已就绪，请确认后批准", { exact: true })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("pdfplumber-text-flow", { exact: true })).toBeVisible();
  await expect(page.getByText("trusted-local-pdf-standard@1.0", { exact: true })).toBeVisible();
  await expect(page.getByTitle("current").first()).toHaveText("当前有效");
  await page.getByRole("button", { name: "批准并解析" }).click();
  await expect(page.getByText("解析请求已接收", { exact: true })).toBeVisible({ timeout: 30_000 });
}


function syntheticPdfBytes(text: string): Buffer {
  return buildSyntheticPdf([text]);
}


function syntheticUncertainPdfBytes(text: string): Buffer {
  return buildSyntheticPdf([text, ""]);
}


function buildSyntheticPdf(pageTexts: string[]): Buffer {
  const pageCount = pageTexts.length;
  const fontRef = 3 + pageCount;
  const pageRefs = pageTexts.map((_, index) => `${index + 3} 0 R`).join(" ");
  const pages = pageTexts.map((_, index) => {
    const contentRef = 4 + pageCount + index;
    return Buffer.from(
      `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 ${fontRef} 0 R >> >> /Contents ${contentRef} 0 R >>`,
      "ascii",
    );
  });
  const streams = pageTexts.map((text) => {
    const escaped = text.replaceAll("\\", "\\\\").replaceAll("(", "\\(").replaceAll(")", "\\)");
    const stream = text ? Buffer.from(`BT /F1 12 Tf 72 720 Td (${escaped}) Tj ET`, "ascii") : Buffer.alloc(0);
    return Buffer.concat([Buffer.from(`<< /Length ${stream.length} >>\nstream\n`, "ascii"), stream, Buffer.from("\nendstream", "ascii")]);
  });
  const objects = [
    Buffer.from("<< /Type /Catalog /Pages 2 0 R >>", "ascii"),
    Buffer.from(`<< /Type /Pages /Kids [${pageRefs}] /Count ${pageCount} >>`, "ascii"),
    ...pages,
    Buffer.from("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>", "ascii"),
    ...streams,
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
