#!/usr/bin/env bash
# Vercel 构建：从仓库里的 snapshot/ 还原数据库，生成在线只读版静态网站到 public/
# （vercel.json 里的 buildCommand 调用这个脚本；本地不用跑它，本地预览用 python -m skideals build-site）
set -euo pipefail

# 项目需要 Python 3.11+（tomllib）；构建镜像里有 3.12 / 3.13 / 3.14，挑一个
PY=""
for c in python3.13 python3.12 python3.14 python3; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
echo "using $($PY --version)"

# 用独立的虚拟环境装依赖，避免系统 Python 拒绝 pip 安装
"$PY" -m venv /tmp/skideals-venv
/tmp/skideals-venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt
/tmp/skideals-venv/bin/python -m skideals build-site --out public
