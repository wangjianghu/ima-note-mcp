#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="python3"
IDE="cursor"
CONFIG_PATH=""
CLIENT_ID=""
API_KEY=""
EMBED_ENV_SECRETS="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
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
      echo "用法: ./install.sh [--python python3] [--ide cursor|trae|codebuddy|claude] [--config-path /path/mcp.json] [--client-id xxx --api-key xxx] [--embed-env-secrets]"
      exit 1
      ;;
  esac
done

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "未找到 Python 命令: $PYTHON_BIN"
  exit 1
fi

if [[ "$IDE" != "cursor" && "$IDE" != "trae" && "$IDE" != "codebuddy" && "$IDE" != "claude" ]]; then
  echo "不支持的 IDE: $IDE"
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$ROOT_DIR/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"
INIT_CMD="$VENV_DIR/bin/ima-note-mcp-init"

if [[ ! -d "$VENV_DIR" ]]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

"$VENV_PIP" install -U pip >/dev/null
"$VENV_PIP" install -e "$ROOT_DIR" >/dev/null

if [[ -n "$CLIENT_ID" ]]; then
  export IMA_OPENAPI_CLIENTID="$CLIENT_ID"
fi
if [[ -n "$API_KEY" ]]; then
  export IMA_OPENAPI_APIKEY="$API_KEY"
fi

INIT_ARGS=("--ide" "$IDE")
if [[ -n "$CONFIG_PATH" ]]; then
  INIT_ARGS+=("--config-path" "$CONFIG_PATH")
fi
if [[ "$EMBED_ENV_SECRETS" == "true" ]]; then
  INIT_ARGS+=("--embed-env-secrets")
fi

"$INIT_CMD" "${INIT_ARGS[@]}"

echo
echo "安装完成"
if [[ -n "$CONFIG_PATH" ]]; then
  echo "MCP 配置文件: $CONFIG_PATH"
else
  echo "MCP 配置文件: 已按 IDE($IDE) 默认路径写入"
fi
echo "请确保 IDE 运行环境已设置 IMA_OPENAPI_CLIENTID 与 IMA_OPENAPI_APIKEY"
echo "启动命令: $VENV_DIR/bin/ima-note-mcp"
