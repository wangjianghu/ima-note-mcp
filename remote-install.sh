#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/wangjianghu/ima-note-mcp.git"
REF="main"
INSTALL_DIR="${HOME}/.ima-note-mcp"
PYTHON_BIN="python3"
IDE="cursor"
CONFIG_PATH=""
CLIENT_ID=""
API_KEY=""
EMBED_ENV_SECRETS="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-url)
      REPO_URL="${2:-}"
      shift 2
      ;;
    --ref)
      REF="${2:-}"
      shift 2
      ;;
    --install-dir)
      INSTALL_DIR="${2:-}"
      shift 2
      ;;
    --python)
      PYTHON_BIN="${2:-}"
      shift 2
      ;;
    --config-path)
      CONFIG_PATH="${2:-}"
      shift 2
      ;;
    --ide)
      IDE="${2:-}"
      shift 2
      ;;
    --client-id)
      CLIENT_ID="${2:-}"
      shift 2
      ;;
    --api-key)
      API_KEY="${2:-}"
      shift 2
      ;;
    --embed-env-secrets)
      EMBED_ENV_SECRETS="true"
      shift 1
      ;;
    *)
      echo "不支持的参数: $1"
      echo "用法: remote-install.sh [--repo-url URL] [--ref main] [--install-dir ~/.ima-note-mcp] [--python python3] [--ide cursor|trae|codebuddy|claude] [--config-path /path/mcp.json] [--client-id xxx --api-key xxx] [--embed-env-secrets]"
      exit 1
      ;;
  esac
done

if ! command -v git >/dev/null 2>&1; then
  echo "未找到 git，请先安装 git"
  exit 1
fi

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "未找到 Python 命令: $PYTHON_BIN"
  exit 1
fi

mkdir -p "$(dirname "$INSTALL_DIR")"

if [[ -d "$INSTALL_DIR/.git" ]]; then
  git -C "$INSTALL_DIR" fetch --depth 1 origin "$REF"
  git -C "$INSTALL_DIR" checkout -f FETCH_HEAD
else
  rm -rf "$INSTALL_DIR"
  git clone --depth 1 --branch "$REF" "$REPO_URL" "$INSTALL_DIR"
fi

INSTALL_CMD=("$INSTALL_DIR/install.sh" "--python" "$PYTHON_BIN" "--ide" "$IDE")
if [[ -n "$CONFIG_PATH" ]]; then
  INSTALL_CMD+=("--config-path" "$CONFIG_PATH")
fi
if [[ -n "$CLIENT_ID" ]]; then
  INSTALL_CMD+=("--client-id" "$CLIENT_ID")
fi
if [[ -n "$API_KEY" ]]; then
  INSTALL_CMD+=("--api-key" "$API_KEY")
fi
if [[ "$EMBED_ENV_SECRETS" == "true" ]]; then
  INSTALL_CMD+=("--embed-env-secrets")
fi

bash "${INSTALL_CMD[@]}"
