#!/usr/bin/env bash
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo '请以 root 运行'; exit 1; }
command -v curl >/dev/null || { apt-get update; apt-get install -y curl ca-certificates; }
WORK_DIR=$(mktemp -d)
if command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then
    gh api repos/xmeng-cx/line-converter/tarball/main > "$WORK_DIR/source.tar.gz"
else
    curl --fail --location --retry 3 https://github.com/xmeng-cx/line-converter/archive/refs/heads/main.tar.gz -o "$WORK_DIR/source.tar.gz"
fi
mkdir "$WORK_DIR/source"
tar -xzf "$WORK_DIR/source.tar.gz" -C "$WORK_DIR/source" --strip-components=1
bash "$WORK_DIR/source/install.sh"
