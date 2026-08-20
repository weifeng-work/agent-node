#!/usr/bin/env node
/**
 * agent-node — 去中心化局域网多智能体协作节点 / Decentralized LAN multi-agent node
 *
 * 用法 / Usage:
 *   agent-node          启动节点（已运行则打开面板）/ Start node (or open panel if running)
 *   agent-node start    同上 / Same as above
 *   agent-node stop     停止节点 / Stop node
 *   agent-node status   查看状态 / Show status
 *   agent-node setup    重新安装依赖 / Reinstall dependencies
 *   agent-node uninstall  完整卸载 / Full uninstall
 *   agent-node help     帮助 / Help
 */
"use strict";

const { spawn, execSync } = require("child_process");
const path = require("path");
const fs = require("fs");

// ---------- 常量 / Constants ----------
const INSTALL_DIR = path.join(process.env.LOCALAPPDATA || process.env.HOME, "agent-node");
const APP_DIR = path.join(INSTALL_DIR, "app");
const DATA_DIR = path.join(INSTALL_DIR, "data");
const VENV_DIR = path.join(INSTALL_DIR, "venv");
const PANEL_URL_FILE = path.join(DATA_DIR, "panel.url");
const LOCK_FILE = path.join(DATA_DIR, "node.lock");
const REPO_URL = "https://github.com/weifeng-work/agent-node";
const DEFAULT_PANEL_PORT = 5177;  // 默认面板端口（与 server/panel.py DEFAULT_PANEL_PORT 一致）
// npm 包版本（版本号统一来源；面板/命令行启动均显示）
const PKG_VERSION = require(path.join(__dirname, "..", "package.json")).version || "?";

// ---------- 双语文案 / Bilingual messages ----------
const T = {
  banner: [
    "",
    "╔══════════════════════════════════════════════════════════════╗",
    "║  agent-node — 去中心化局域网多智能体协作节点                  ║",
    "║  Decentralized LAN Multi-Agent Collaboration Node           ║",
    "╚══════════════════════════════════════════════════════════════╝",
  ],
  starting: ["  正在启动节点...", "  Starting node..."],
  alreadyRunning: ["  节点已在运行！正在打开面板...", "  Node is already running! Opening panel..."],
  started: ["  节点已启动，面板地址:", "  Node started, panel URL:"],
  stopped: ["  节点已停止。", "  Node stopped."],
  notRunning: ["  节点未运行。", "  Node is not running."],
  notInstalled: [
    "  ❌ 节点未安装。请先运行: agent-node setup",
    "  ❌ Node not installed. Run: agent-node setup",
  ],
  pythonMissing: [
    "  ❌ 未检测到 Python (≥3.10)。请安装 Python 后重试:",
    "  ❌ Python (≥3.10) not found. Install Python and retry:",
  ],
  pythonUrl: "     https://www.python.org/downloads/",
  error: [
    "  ❌ 启动失败。以下是排查提示词，发给任何 AI 智能体即可帮你排查:",
    "  ❌ Start failed. Send this troubleshooting prompt to any AI agent:",
  ],
  usage: [
    "",
    "  ─── 用法 / Usage ───────────────────────────────────────────",
    "",
    "  agent-node            启动（已运行则打开面板）/ Start (or open panel)",
    "  agent-node stop       停止 / Stop",
    "  agent-node status     状态 / Status",
    "  agent-node update     更新代码（拉取 npm 最新版，需重启生效）/ Update code (pull latest from npm, restart to apply)",
    "  agent-node restart    重启节点 / Restart node",
    "  agent-node setup      安装或修复 / Install or repair",
    "  agent-node uninstall  卸载 / Uninstall",
    "",
  ],
  mcpTitle: [
    "",
    "  ─── MCP 接入（让 AI 智能体使用本节点）/ MCP Access ──────────",
    "",
  ],
  mcpHint: [
    "  把下面的 JSON 配置添加到你的 AI 客户端（如 Claude Desktop）:",
    "  Add this JSON to your AI client (e.g. Claude Desktop):",
    "",
  ],
  skillHint: [
    "",
    "  ─── 让 AI 教你使用 / Let AI teach you ──────────────────────",
    "",
    "  把下面这句话发给任何智能体，它会读取节点内置说明书后指导你:",
    "  Send this message to any AI agent, it will read the built-in manual:",
    "",
  ],
};

function msg(key) {
  const lines = T[key];
  if (!lines) return;
  lines.forEach((l, i) => {
    if (typeof l === "string") console.log(l);
    else if (Array.isArray(l)) console.log(l[0] + "  " + l[1]);
  });
}

function msgPair(zh, en) {
  console.log(`  ${zh}`);
  console.log(`  ${en}`);
}

// ---------- 工具函数 / Helpers ----------
function isRunning() {
  try {
    if (!fs.existsSync(LOCK_FILE)) return false;
    const pid = parseInt(fs.readFileSync(LOCK_FILE, "utf-8").trim());
    if (!pid) return false;
    // Windows: 检查进程存活 / Check if process alive
    try {
      const out = execSync(`tasklist /fi "PID eq ${pid}" /nh`, { encoding: "utf-8", windowsHide: true });
      return out.includes(String(pid));
    } catch { return false; }
  } catch { return false; }
}

function getPanelUrl() {
  try {
    // 面板实际端口由节点启动时决定（5177 被占自动 +1）并写入 data/panel.url
    return fs.readFileSync(PANEL_URL_FILE, "utf-8").trim();
  } catch {
    // panel.url 缺失（节点未启动）→ 以默认端口兜底
    return `http://127.0.0.1:${DEFAULT_PANEL_PORT}`;
  }
}

function openUrl(url) {
  try { spawn("cmd", ["/c", "start", url], { detached: true, stdio: "ignore" }).unref(); } catch {}
}

function findPython() {
  // 依次查找 / Search in order
  const candidates = [
    path.join(VENV_DIR, "Scripts", "python.exe"),
    "python", "python3",
  ];
  for (const c of candidates) {
    try {
      const ver = execSync(`"${c}" --version`, { encoding: "utf-8", stdio: "pipe", windowsHide: true });
      if (ver.includes("Python 3.")) {
        const major = parseInt(ver.split(" ")[1].split(".")[0]);
        if (major >= 3) return c;
      }
    } catch {}
  }
  return null;
}

// ---------- 命令实现 / Commands ----------
function runInstaller() {
  const installer = require(path.join(__dirname, "..", "lib", "install.js"));
  return installer.main();
}

function cmdStart() {
  console.log(T.banner.join("\n"));

  // 未安装则自动安装（覆盖 npm allow-scripts 拦截 postinstall 的场景）
  // Auto-install if missing (covers npm allow-scripts blocking postinstall)
  if (!fs.existsSync(APP_DIR)) {
    msgPair("首次使用：正在自动安装（只需一次，约 1-3 分钟）...", "First run: auto-installing (one time only, ~1-3 min)...");
    console.log();
    runInstaller()
      .then(() => { console.log(); doStart(); })
      .catch(e => { console.error("  Install error:", e.message); process.exit(1); });
    return;
  }

  doStart();
}

function doStart() {
  const python = findPython();
  if (!python) {
    msg("pythonMissing");
    console.log(T.pythonUrl);
    process.exit(1);
  }

  if (isRunning()) {
    msg("alreadyRunning");
    const url = getPanelUrl();
    openUrl(url);
    showMcpGuide();
    showSkillGuide();
    showUsage();
    return;
  }

  msg("starting");

  // 清除旧 panel.url，确保只读到本次启动的实际地址
  // Remove stale panel.url so we only read the fresh URL written by this run
  try { fs.unlinkSync(PANEL_URL_FILE); } catch {}

  // 后台启动（pythonw 无窗口）/ Background start (pythonw no console)
  const pythonw = python.replace("python.exe", "pythonw.exe");
  const actual = fs.existsSync(pythonw) ? pythonw : python;
  const env = { ...process.env, AGENT_NODE_CONFIG_DIR: DATA_DIR };
  const proc = spawn(actual, ["-m", "node.main", "--data-dir", DATA_DIR], {
    cwd: APP_DIR,
    detached: true,
    stdio: "ignore",
    env,
    windowsHide: true,
  });
  proc.unref();

  // 等面板就绪（lock 存活 + panel.url 已写入）/ Wait for panel ready
  const startTime = Date.now();
  const checkInterval = setInterval(() => {
    if (isRunning() && fs.existsSync(PANEL_URL_FILE)) {
      clearInterval(checkInterval);
      const url = getPanelUrl();
      msg("started");
      console.log(`  ${url}`);
      console.log();
      openUrl(url);
      showMcpGuide();
      showSkillGuide();
      showUsage();
      process.exit(0);
    }
    if (Date.now() - startTime > 30000) {
      clearInterval(checkInterval);
      msg("error");
      showTroubleshoot("启动超时（30 秒内未就绪）/ Start timeout (not ready in 30s)");
      process.exit(1);
    }
  }, 1000);
}

function cmdStop() {
  if (!isRunning()) { msg("notRunning"); return; }
  try {
    const pid = parseInt(fs.readFileSync(LOCK_FILE, "utf-8").trim());
    execSync(`taskkill /PID ${pid} /T /F`, { stdio: "pipe", windowsHide: true });
    msg("stopped");
  } catch (e) {
    console.log("  Error:", e.message);
  }
}

function cmdStatus() {
  console.log(T.banner.join("\n"));
  if (isRunning()) {
    const url = getPanelUrl();
    console.log(`  ✓ 运行中 / Running`);
    console.log(`  PID: ${fs.readFileSync(LOCK_FILE, "utf-8").trim()}`);
    console.log(`  面板 / Panel: ${url}`);
  } else {
    msg("notRunning");
  }
  showUsage();
}

function cmdSetup() {
  console.log(T.banner.join("\n"));
  runInstaller().catch(e => {
    console.error("  Install error:", e.message);
    process.exit(1);
  });
}

function cmdUpdate() {
  console.log(T.banner.join("\n"));
  msgPair("正在检查 npm 最新版本...", "Checking npm for latest version...");
  console.log();
  const { execSync } = require("child_process");
  // 1) 查 npm 上最新可用版本（信息展示用，实际不传=latest）
  let latest = "?";
  try {
    const pkg = JSON.parse(execSync(
      "npm view @weifeng-work/agent-node version --json",
      { encoding: "utf-8", stdio: ["pipe","pipe","ignore"], windowsHide: true }));
    latest = String(pkg).trim() || "?";
  } catch (e) {
    console.log("  ⚠ 无法查询 npm 最新版本:" + e.message);
  }
  console.log(`  npm 最新版本 / npm latest: ${latest}`);
  console.log();
  // 2) 重新安装最新全局包（不传版本 = 拉 latest）
  try {
    msgPair("  正在拉取最新版本并安装...", "  Pulling latest and installing...");
    execSync(`npm install -g @weifeng-work/agent-node`,
             { encoding: "utf-8", stdio: "inherit", windowsHide: true });
    console.log();
  } catch (e) {
    console.log("  ❌ 更新失败:" + e.message);
    process.exit(1);
  }
  // 3) 重新部署 Python 源码（install.js 会重建 %LOCALAPPDATA%\agent-node\app）
  console.log();
  msgPair("正在重新部署节点源码...", "Re-deploying node source...");
  runInstaller().then(() => {
    console.log();
    msgPair("✅ 代码已更新到最新版。", "✅ Code updated to latest.");
    msgPair("  重启节点生效 / Restart node to apply:", "    agent-node restart");
    console.log();
  }).catch(e => {
    console.error("  Install error:", e.message);
    process.exit(1);
  });
}

function cmdRestart() {
  console.log(T.banner.join("\n"));
  const wasRunning = isRunning();
  if (wasRunning) cmdStop();
  cmdStart();
}

function cmdUninstall() {
  console.log(T.banner.join("\n"));
  msgPair("正在卸载...", "Uninstalling...");

  // 停止节点 / Stop node
  if (isRunning()) cmdStop();

  // 删除安装目录 / Remove install dir
  try {
    fs.rmSync(INSTALL_DIR, { recursive: true, force: true });
    msgPair("✓ 已删除安装目录", "✓ Install directory removed");
  } catch (e) {
    console.log("  ", e.message);
  }

  msgPair("卸载完成。", "Uninstall complete.");
  msgPair("npm 全局包请手动移除: npm uninstall -g @weifeng-work/agent-node", "Remove npm global package: npm uninstall -g @weifeng-work/agent-node");
}

// ---------- 引导展示 / Guides ----------
function showMcpGuide() {
  msg("mcpTitle");
  msg("mcpHint");
  const appDir = APP_DIR.replace(/\\/g, "/");
  console.log('  {');
  console.log('    "mcpServers": {');
  console.log('      "agent-node": {');
  console.log('        "command": "python",');
  console.log(`        "args": ["-m", "mcp.server"],`);
  console.log(`        "cwd": "${appDir}",`);
  console.log('        "env": {');
  console.log(`          "AGENT_NODE_PANEL": "${getPanelUrl()}"`);
  console.log('        }');
  console.log('      }');
  console.log('    }');
  console.log('  }');
}

function showSkillGuide() {
  msg("skillHint");
  console.log('  ┌─────────────────────────────────────────────────────────────┐');
  console.log('  │ 请加载 skill/SKILL.md 说明书，学会使用 agent-node 节点后，  │');
  console.log('  │ 告诉我：本机有哪些可用执行器？怎么调用远程智能体？          │');
  console.log('  │ Please load skill/SKILL.md manual, then tell me:           │');
  console.log('  │ what executors are available? How to call remote agents?   │');
  console.log('  └─────────────────────────────────────────────────────────────┘');
}

function showUsage() {
  T.usage.forEach(l => console.log(l));
  console.log(`  版本 / Version:  v${PKG_VERSION}`);
  console.log(`  面板 / Panel:  ${getPanelUrl()}`);
  console.log(`  文档 / Docs:   ${REPO_URL}`);
  console.log();
}

function showTroubleshoot(errDetail) {
  console.log("  ┌─────────────────────────────────────────────────────────────┐");
  console.log("  │ 排查提示词（复制发给任何 AI 智能体）/ Troubleshoot prompt   │");
  console.log("  ├─────────────────────────────────────────────────────────────┤");
  console.log("  │ 我的 agent-node 节点启动失败。                              │");
  console.log("  │ 错误信息: " + (errDetail || "unknown").substring(0, 45).padEnd(48) + "│");
  console.log("  │ 项目仓库: " + REPO_URL.padEnd(50) + "│");
  console.log("  │ 日志文件: " + path.join(DATA_DIR, "node.log").padEnd(46) + "│");
  console.log("  │ 请读取日志并告诉我问题原因及修复方法。                      │");
  console.log('  └─────────────────────────────────────────────────────────────┘');
}

// ---------- 主入口 / Main ----------
const cmd = process.argv[2] || "start";
switch (cmd) {
  case "start": case "": cmdStart(); break;
  case "stop": cmdStop(); break;
  case "status": cmdStatus(); break;
  case "setup": cmdSetup(); break;
  case "update": cmdUpdate(); break;
  case "restart": cmdRestart(); break;
  case "uninstall": cmdUninstall(); break;
  case "help": case "--help": case "-h":
    console.log(T.banner.join("\n"));
    showUsage(); break;
  default:
    console.log(`  Unknown command: ${cmd}`);
    showUsage();
    process.exit(1);
}
