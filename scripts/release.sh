#!/usr/bin/env bash
# agent-node npm 发布脚本 —— 由 GitHub Actions（OIDC trusted publishing）调用。
#
# 用法:
#   release.sh v0.1.1          # 从 git tag 派生版本号并发布
#
# 流程:
#   1) 校验 tag 格式 v<semver>
#   2) 把版本号写入 npm-dist/package.json 与 npm-dist/app/CHANGELOG 同步标记
#   3) 运行 scripts/build_npm.py 把最新源码/文档/二进制同步进 npm-dist/app/
#   4) 进入 npm-dist 执行 npm publish --access public --provenance
#
# 依赖环境:
#   - python3（build_npm.py 用标准库）
#   - npm >= 11.5.1 且 node >= 22.14（OIDC 需要）
#   - 已在 npm 配置该 repo+workflow 的 Trusted Publisher（OIDC，无需 NODE_AUTH_TOKEN）
set -euo pipefail

TAG="${1:?用法: release.sh v<semver> 例如 v0.1.1}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="$ROOT/npm-dist"

# ---- 1) 解析版本号 ----
case "$TAG" in
  v[0-9]*.[0-9]*.[0-9]*)
    VERSION="${TAG#v}"
    ;;
  *)
    echo "❌ tag 格式错误: $TAG （应为 vX.Y.Z）" >&2
    exit 1
    ;;
esac
echo "▶ 发布 agent-node@$VERSION（tag=$TAG）"

# ---- 2) 版本号写入 npm-dist/package.json ----
PKG_JSON="$DIST/package.json"
NEW_PKG="$(python3 - "$PKG_JSON" "$VERSION" <<'PY'
import json, sys
path, ver = sys.argv[1], sys.argv[2]
pkg = json.load(open(path, encoding="utf-8"))
pkg["version"] = ver
json.dump(pkg, open(path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
open(path, "a", encoding="utf-8").write("\n")
PY
)"
echo "✓ 版本号已写入 $PKG_JSON"

# ---- 2.5) 把版本号写入 node/version.py（唯一事实来源=tag；面板/命令行启动读取） ----
VERSION_PY="$ROOT/node/version.py"
python3 - "$VERSION_PY" "$VERSION" <<'PY'
import re, sys
p, v = sys.argv[1], sys.argv[2]
s = open(p, encoding="utf-8").read()
s = re.sub(r'VERSION\s*=\s*"[^"]*"', f'VERSION = "{v}"', s, count=1)
open(p, "w", encoding="utf-8").write(s)
PY
echo "✓ 版本号已写入 $VERSION_PY"

# ---- 3) 构建 npm-dist/app（同步最新源码+文档+二进制） ----
echo "▶ 运行 build_npm.py（同步源码→ npm-dist/app/）..."
python3 "$ROOT/scripts/build_npm.py"
echo "✓ 构建完成"

# ---- 4) 发布（OIDC trusted publishing，自动 provenance） ----
echo "▶ 发布到 npm..."
cd "$DIST"
npm publish --access public --provenance --ignore-scripts

echo "✅ 发布完成 agent-node@$VERSION"