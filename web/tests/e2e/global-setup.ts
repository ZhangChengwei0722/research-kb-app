import { spawn, spawnSync, type ChildProcessWithoutNullStreams } from "node:child_process";
import { access, cp, mkdir, mkdtemp, readFile, utimes, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

let child: ChildProcessWithoutNullStreams | null = null;
let startupFacts: { url: string; token: string; logPath: string } | null = null;

export default async function globalSetup() {
  const repoRoot = fileURLToPath(new URL("../../../", import.meta.url));
  const fixture = process.env.RKB_P2_SMALL_FIXTURE
    ? path.resolve(process.env.RKB_P2_SMALL_FIXTURE)
    : path.join(repoRoot, "tests", "fixtures", "p2_small", "workspace");
  const python = process.env.RKB_APP_PYTHON
    ? path.resolve(process.env.RKB_APP_PYTHON)
    : path.join(repoRoot, ".venv", "Scripts", "python.exe");
  const fixtureVerification = spawnSync(
    python,
    [path.join(repoRoot, "scripts", "verify_public_fixture.py"), "--fixture", fixture],
    { cwd: repoRoot, encoding: "utf8" },
  );
  if (fixtureVerification.status !== 0) {
    const detail = fixtureVerification.error?.message || fixtureVerification.stderr || fixtureVerification.stdout;
    throw new Error(`Synthetic p2-small fixture verification failed${detail ? `: ${detail.trim()}` : ""}`);
  }
  const frontendRoot = await resolveFrontendRoot(python, repoRoot);
  const target = await mkdtemp(path.join(tmpdir(), "research-kb-app-p2d-e2e-"));
  const workspace = path.join(target, "workspace");
  const exchangeWorkspace = path.join(target, "exchange-workspace");
  const stateRoot = path.join(target, "app-state");
  const logRoot = path.join(stateRoot, "logs");
  const obsidianVault = path.join(target, "synthetic-obsidian-vault");
  const obsidianPersonal = path.join(obsidianVault, "Research KB", "Personal");
  const configPath = path.join(target, "app-config.json");
  await cp(fixture, workspace, { recursive: true, errorOnExist: true });
  await cp(fixture, exchangeWorkspace, { recursive: true, errorOnExist: true });
  const workspaceConfig = path.join(workspace, "workspace.yaml");
  const configureInbox = spawnSync(
    python,
    [
      "-c",
      "from pathlib import Path; import sys, yaml; p=Path(sys.argv[1]); " +
        "v=yaml.safe_load(p.read_text(encoding='utf-8')); " +
        "v['workspace']['local_inbox']='./sources/inbox'; " +
        "v['agent_policy']={" +
          "'registry_version':'p8-v1'," +
          "'allowed_content_classes':['metadata','operational_context','parsed_excerpt','review_background','canonical_evidence','paper_card_content','research_routing_context','research_synthesis']," +
          "'execution_scope':'cloud_allowed'," +
          "'max_prompt_bytes':2097152," +
          "'max_result_bytes':1048576}; " +
        "p.write_text(yaml.safe_dump(v, sort_keys=False), encoding='utf-8', newline='\\n')",
      workspaceConfig,
    ],
    { cwd: repoRoot, encoding: "utf8" },
  );
  if (configureInbox.status !== 0) throw new Error("Synthetic inbox configuration failed");
  const inbox = path.join(workspace, "sources", "inbox");
  await mkdir(inbox, { recursive: true });
  const inboxPdf = path.join(inbox, "e2e-inbox-review.pdf");
  await writeFile(inboxPdf, syntheticPdfBytes("Synthetic E2E inbox review."));
  const stableTime = new Date(Date.now() - 120_000);
  await utimes(inboxPdf, stableTime, stableTime);
  const bootstrap = spawnSync(
    python,
    [
      "-c",
      "from pathlib import Path; from research_kb.services import WorkspaceBootstrapService; " +
        "raise SystemExit(WorkspaceBootstrapService(Path(__import__('sys').argv[1])).run().exit_code)",
      path.join(workspace, "workspace.yaml"),
    ],
    { cwd: repoRoot, encoding: "utf8" },
  );
  if (bootstrap.status !== 0) throw new Error("Synthetic workspace bootstrap failed");
  const exchangeWorkspaceConfig = path.join(exchangeWorkspace, "workspace.yaml");
  const configureExchangeWorkspace = spawnSync(
    python,
    [
      "-c",
      "from pathlib import Path; import json, sys, yaml; p=Path(sys.argv[1]); " +
        "v=yaml.safe_load(p.read_text(encoding='utf-8')); " +
        "v['workspace']['id']='workspace_3b72f3aa-66a9-4c87-8cf5-5c70490398f2'; " +
        "v['workspace']['local_inbox']='./sources/inbox'; " +
        "p.write_text(yaml.safe_dump(v, sort_keys=False), encoding='utf-8', newline='\\n'); " +
        "g=p.parent/'knowledge'/'guardian'/'reports.jsonl'; " +
        "rows=[json.loads(line) for line in g.read_text(encoding='utf-8').splitlines() if line]; " +
        "[row.__setitem__('workspace_id', v['workspace']['id']) for row in rows]; " +
        "g.write_text(''.join(json.dumps(row, sort_keys=True, separators=(',', ':'))+'\\n' for row in rows), encoding='utf-8', newline='\\n')",
      exchangeWorkspaceConfig,
    ],
    { cwd: repoRoot, encoding: "utf8" },
  );
  if (configureExchangeWorkspace.status !== 0) throw new Error("Synthetic Exchange workspace configuration failed");
  await mkdir(path.join(exchangeWorkspace, "sources", "inbox"), { recursive: true });
  const bootstrapExchangeWorkspace = spawnSync(
    python,
    [
      "-c",
      "from pathlib import Path; from research_kb.services import WorkspaceBootstrapService; " +
        "raise SystemExit(WorkspaceBootstrapService(Path(__import__('sys').argv[1])).run().exit_code)",
      exchangeWorkspaceConfig,
    ],
    { cwd: repoRoot, encoding: "utf8" },
  );
  if (bootstrapExchangeWorkspace.status !== 0) throw new Error("Synthetic Exchange workspace bootstrap failed");
  const prepareP8 = spawnSync(
    python,
    [path.join(repoRoot, "tests", "prepare_p8_synthetic_workspace.py"), workspaceConfig],
    { cwd: repoRoot, encoding: "utf8" },
  );
  if (prepareP8.status !== 0) {
    throw new Error(`Synthetic P8 Question preparation failed: ${prepareP8.stderr}`);
  }
  await mkdir(logRoot, { recursive: true });
  await mkdir(obsidianPersonal, { recursive: true });
  await writeFile(path.join(obsidianPersonal, "personal-sentinel.md"), "personal sentinel\n", "utf8");
  await writeFile(
    configPath,
    JSON.stringify(
      {
        contract_version: "research-kb-app-config@1.1",
        workspaces: [
          {
            option_id: "p2-small",
            label: "P2 Small Synthetic",
            config_path: path.join(workspace, "workspace.yaml"),
          },
          {
            option_id: "p10-exchange-target",
            label: "P10 Exchange Target",
            config_path: exchangeWorkspaceConfig,
          },
        ],
        state_root: stateRoot,
        log_root: logRoot,
        frontend_root: frontendRoot,
        request_budgets: {
          max_body_bytes: 16384,
          max_query_bytes: 2048,
          max_page_size: 100,
          request_timeout_seconds: 30,
        },
        obsidian_targets: [
          {
            target_id: "synthetic-vault",
            label: "Synthetic Vault",
            workspace_option_id: "p2-small",
            vault_root: obsidianVault,
            managed_subtree: "Research KB/Generated",
            personal_notes_subtree: "Research KB/Personal",
          },
        ],
      },
      null,
      2,
    ),
    "utf8",
  );

  child = spawn(
    python,
    [path.join(repoRoot, "tests", "e2e_launcher.py"), "--config", configPath, "--no-browser"],
    { cwd: repoRoot, env: { ...process.env, PYTHONUNBUFFERED: "1" } },
  );
  const { url, token, logPath } = await readStartup(child);
  startupFacts = { url, token, logPath };
  process.env.RKB_E2E_URL = url;
  process.env.RKB_E2E_TOKEN = token;
  process.env.RKB_E2E_WORKSPACE = workspace;
  process.env.RKB_E2E_EXCHANGE_WORKSPACE = exchangeWorkspace;
  process.env.RKB_E2E_PYTHON = python;
  process.env.RKB_E2E_SCREENSHOT_DIR = path.join(target, "screenshots");
  process.env.RKB_E2E_OBSIDIAN_VAULT = obsidianVault;
  await waitForRuntime(url);

  return async () => {
    if (!child || !startupFacts) return;
    let forcedShutdown = false;
    if (child.exitCode === null) {
      try {
        await waitForExit(child, 10_000);
      } catch {
        forcedShutdown = true;
        child.kill();
        await waitForExit(child, 5_000).catch(() => undefined);
      }
    }
    if (!forcedShutdown && child.exitCode !== 0) throw new Error(`App exited with code ${child.exitCode ?? "unknown"}`);
    const log = await readFile(startupFacts.logPath, "utf8");
    if (startupFacts.url.includes(startupFacts.token) || log.includes(startupFacts.token)) {
      throw new Error("Startup token escaped its console-only boundary");
    }
  };
}

async function resolveFrontendRoot(python: string, repoRoot: string): Promise<string> {
  let frontendRoot: string;
  if (process.env.RKB_APP_FRONTEND_ROOT) {
    frontendRoot = path.resolve(process.env.RKB_APP_FRONTEND_ROOT);
  } else if (process.env.RKB_APP_PACKAGE_E2E === "1") {
    const resolved = spawnSync(
      python,
      [
        "-c",
        "from importlib import resources; print(resources.files('research_kb_app').joinpath('web_dist'))",
      ],
      { cwd: repoRoot, encoding: "utf8" },
    );
    if (resolved.status !== 0 || !resolved.stdout.trim()) {
      throw new Error("Installed App frontend resolution failed");
    }
    frontendRoot = path.resolve(resolved.stdout.trim());
  } else {
    frontendRoot = path.join(repoRoot, "web", "release");
  }
  await access(path.join(frontendRoot, "index.html"));
  await access(path.join(frontendRoot, "assets"));
  return frontendRoot;
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

function readStartup(
  processHandle: ChildProcessWithoutNullStreams,
): Promise<{ url: string; token: string; logPath: string }> {
  return new Promise((resolve, reject) => {
    let buffer = "";
    let url = "";
    let token = "";
    let logPath = "";
    const timeout = setTimeout(() => reject(new Error("App startup timed out")), 20_000);
    processHandle.stdout.on("data", (chunk: Buffer) => {
      buffer += chunk.toString("utf8");
      const lines = buffer.split(/\r?\n/);
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        if (line.startsWith("URL: ")) url = line.slice(5);
        if (line.startsWith("ONE-TIME TOKEN: ")) token = line.slice(16);
        if (line.startsWith("LOG: ")) logPath = line.slice(5);
      }
      if (url && token && logPath) {
        clearTimeout(timeout);
        resolve({ url, token, logPath });
      }
    });
    processHandle.once("exit", (code) => {
      if (!url || !token || !logPath) {
        clearTimeout(timeout);
        reject(new Error(`App exited before readiness with code ${code ?? "unknown"}`));
      }
    });
  });
}

function waitForExit(processHandle: ChildProcessWithoutNullStreams, timeoutMs: number): Promise<void> {
  if (processHandle.exitCode !== null) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error("App did not stop through the product action")), timeoutMs);
    processHandle.once("exit", () => {
      clearTimeout(timeout);
      resolve();
    });
  });
}

async function waitForRuntime(url: string): Promise<void> {
  const deadline = Date.now() + 20_000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(new URL("api/runtime", url));
      if (response.ok) return;
    } catch {
      // The already-bound listener may not yet be accepted by Uvicorn.
    }
    await new Promise((resolve) => setTimeout(resolve, 75));
  }
  throw new Error("App runtime endpoint did not become ready");
}
