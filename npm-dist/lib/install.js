/**
 * agent-node 安装器 / Installer
 * 部署 Python 项目到 %LOCALAPPDATA%\agent-node 并安装依赖。
 * Deploys Python project to %LOCALAPPDATA%\agent-node and installs dependencies.
 */
"use strict";

const { execSync } = require("child_process");
const path = require("path");
const fs = require("fs");

const INSTALL_DIR = path.join(process.env.LOCALAPPDATA || process.env.HOME, "agent-node");
const APP_DIR = path.join(INSTALL_DIR, "app");
const DATA_DIR = path.join(INSTALL_DIR, "data");
const VENV_DIR = path.join(INSTALL_DIR, "venv");

// npm 包内 app/ 目录 / app/ directory inside npm package
const PKG_APP_DIR = path.join(__dirname, "..", "app");

function log(zh, en) {
  console.log(`  ${zh}`);
  if (en) console.log(`  ${en}`);
  console.log();
}

function step(n, total, zh, en) {
  console.log(`  [${n}/${total}] ${zh}`);
  if (en) console.log(`        ${en}`);
}

// ---------- 1. Python 检测 ----------
function findSystemPython() {
  for (const name of ["python", "python3", "py"]) {
    try {
      const out = execSync(`"${name}" --version`, { encoding: "utf-8", stdio: "pipe", windowsHide: true });
      const m = out.match(/Python (\d+)\.(\d+)/);
      if (m && (parseInt(m[1]) > 3 || (parseInt(m[1]) === 3 && parseInt(m[2]) >= 10))) {
        return name;
      }
    } catch {}
  }
  return null;
}

// ---------- 主流程 ----------
async function main() {
  console.log();
  console.log("  ═══ agent-node 安装 / Install ═══");
  console.log();

  const TOTAL = 5;

  // Step 1: 检测 Python
  step(1, TOTAL, "检测 Python...", "Detecting Python...");
  const sysPython = findSystemPython();
  if (!sysPython) {
    log("❌ 未检测到 Python ≥ 3.10。请先安装:", "Python ≥ 3.10 not found. Install from:");
    console.log("     https://www.python.org/downloads/");
    console.log("     安装时勾选 'Add Python to PATH' / Check 'Add Python to PATH'");
    console.log("     装好后重新运行: agent-node setup");
    console.log();
    process.exit(1);
  }
  log(`✓ 找到 Python: ${sysPython}`, `Found Python: ${sysPython}`);

  // Step 2: 创建目录结构
  step(2, TOTAL, "创建目录...", "Creating directories...");
  [INSTALL_DIR, APP_DIR, DATA_DIR, path.join(DATA_DIR, "inbox"),
   path.join(DATA_DIR, "sync"), path.join(DATA_DIR, "syncthing")].forEach(d => {
    fs.mkdirSync(d, { recursive: true });
  });
  log(`✓ 安装目录: ${INSTALL_DIR}`, `Install dir: ${INSTALL_DIR}`);

  // Step 3: 复制项目文件
  step(3, TOTAL, "部署项目文件...", "Deploying project files...");
  try {
    // 排除 data_synth 模板（syncthing 二进制单独释放到 data/，避免 app/ 里留重复文件）
    // Exclude data_synth template (syncthing binary released separately to data/, avoid duplicate in app/)
    copyDir(PKG_APP_DIR, APP_DIR, ["data_synth"]);
    log("✓ 文件部署完成", "Files deployed");
  } catch (e) {
    log(`❌ 文件部署失败: ${e.message}`, `Deploy failed: ${e.message}`);
    process.exit(1);
  }

  // 释放 syncthing 二进制: 包内随包分发于 data_synth/syncthing/（npm 包内位置），运行时需 data/syncthing/
  // Release syncthing binary: bundled under data_synth/syncthing/ (inside npm package), runtime expects data/syncthing/
  try {
    const bundled = path.join(PKG_APP_DIR, "data_synth", "syncthing", "syncthing.exe");
    const dest = path.join(DATA_DIR, "syncthing", "syncthing.exe");
    if (fs.existsSync(bundled) && !fs.existsSync(dest)) {
      fs.mkdirSync(path.dirname(dest), { recursive: true });
      fs.copyFileSync(bundled, dest);
      log("✓ syncthing 二进制已就位", "Syncthing binary in place");
    } else if (!fs.existsSync(bundled)) {
      log("⚠ 包内未找到 syncthing 二进制（同步功能不可用）",
          "⚠ syncthing binary not found in package (sync unavailable)");
    } else {
      log("✓ syncthing 二进制已存在", "Syncthing binary already present");
    }
    // 清理旧版本部署到 app/ 的 data_synth 残留（v0.1.5 起只保留包内一份）
    // Remove leftover app/data_synth from older versions (since v0.1.5 kept only inside package)
    const staleTpl = path.join(APP_DIR, "data_synth");
    if (fs.existsSync(staleTpl)) fs.rmSync(staleTpl, { recursive: true, force: true });
  } catch (e) {
    log(`⚠ syncthing 二进制释放失败: ${e.message}`,
        `⚠ Failed to deploy syncthing binary: ${e.message}`);
  }

  // Step 4: 创建 venv 并安装依赖
  step(4, TOTAL, "创建虚拟环境并安装依赖（可能需要几分钟）...", "Creating venv and installing dependencies (may take a few minutes)...");
  const venvPython = path.join(VENV_DIR, "Scripts", "python.exe");
  if (!fs.existsSync(venvPython)) {
    try {
      execSync(`"${sysPython}" -m venv "${VENV_DIR}"`, { stdio: "pipe", windowsHide: true });
      log("✓ 虚拟环境已创建", "Virtual environment created");
    } catch (e) {
      log(`❌ venv 创建失败: ${e.message}`, `venv creation failed: ${e.message}`);
      process.exit(1);
    }
  }
  // 安装依赖 / Install deps
  const reqFile = path.join(APP_DIR, "requirements.txt");
  if (fs.existsSync(reqFile)) {
    try {
      execSync(`"${venvPython}" -m pip install -r "${reqFile}" --quiet`, {
        stdio: "pipe", windowsHide: true, timeout: 300000,
      });
      log("✓ 依赖安装完成", "Dependencies installed");
    } catch (e) {
      log(`❌ 依赖安装失败: ${e.message}`, `Dependency install failed: ${e.message}`);
      process.exit(1);
    }
  }

  // Step 5: 防火墙规则（尽力而为，不阻断）
  step(5, TOTAL, "配置防火墙规则...", "Configuring firewall rules...");
  try {
    const pythonwExe = venvPython.replace("python.exe", "pythonw.exe");
    const rules = [
      { name: "agent-node python", prog: venvPython },
      { name: "agent-node pythonw", prog: pythonwExe },
      { name: "agent-node syncthing", prog: path.join(DATA_DIR, "syncthing", "syncthing.exe") },
    ];
    for (const r of rules) {
      if (fs.existsSync(r.prog)) {
        try {
          execSync(`netsh advfirewall firewall add rule name="${r.name}" dir=in action=allow program="${r.prog}" enable=yes`,
            { stdio: "pipe", windowsHide: true });
        } catch {}
      }
    }
    log("✓ 防火墙规则已添加（或已存在）", "Firewall rules added (or already exist)");
  } catch {
    log("⚠ 防火墙规则添加需要管理员权限（不影响基础功能）", "Firewall rules need admin (doesn't affect basic features)");
  }

  // 完成 / Done
  console.log();
  console.log("  ═══ 安装完成 / Install Complete ═══");
  console.log();
  console.log("  启动节点 / Start node:");
  console.log("    agent-node");
  console.log("  （启动后显示实际面板地址 / Actual panel URL is shown on start）");
  console.log();
}

function copyDir(src, dest, exclude = []) {
  const entries = fs.readdirSync(src, { withFileTypes: true });
  fs.mkdirSync(dest, { recursive: true });
  for (const entry of entries) {
    const srcPath = path.join(src, entry.name);
    const destPath = path.join(dest, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "__pycache__" || entry.name === ".git" || exclude.includes(entry.name)) continue;
      copyDir(srcPath, destPath, exclude);
    } else {
      fs.copyFileSync(srcPath, destPath);
    }
  }
}

// postinstall 钩子调用时静默部署（不装依赖，首跑时装）
if (require.main === module) {
  main().catch(e => { console.error(e); process.exit(1); });
}

module.exports = { main, copyDir };
