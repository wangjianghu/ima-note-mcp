# ima-note-mcp

面向 Trae、Cursor、CodeBuddy、Claude Desktop 等 IDE 的 IMA 笔记 MCP 服务。

- GitHub 仓库：https://github.com/wangjianghu/ima-note-mcp
- 适用环境：macOS + Python 3.13+

## 快速开始（GitHub 用户）

```bash
git clone https://github.com/wangjianghu/ima-note-mcp.git
cd ima-note-mcp
python3 -m venv .venv
./.venv/bin/pip install -e .
IMA_OPENAPI_CLIENTID="你的_client_id" IMA_OPENAPI_APIKEY="你的_api_key" ./.venv/bin/ima-note-mcp-init --ide "你的_ide"
```

其中 `"你的_ide"` 可选：`trae` / `cursor` / `codebuddy` / `claude`。必须与你实际使用的 IDE 一致。

生成对应 IDE 的 MCP 配置后，重启 IDE 即可使用。

## 按 IDE 执行（推荐）

### Trae

```bash
git clone https://github.com/wangjianghu/ima-note-mcp.git
cd ima-note-mcp
python3 -m venv .venv
./.venv/bin/pip install -e .
IMA_OPENAPI_CLIENTID="你的_client_id" IMA_OPENAPI_APIKEY="你的_api_key" ./.venv/bin/ima-note-mcp-init --ide trae
```

### Cursor

```bash
git clone https://github.com/wangjianghu/ima-note-mcp.git
cd ima-note-mcp
python3 -m venv .venv
./.venv/bin/pip install -e .
IMA_OPENAPI_CLIENTID="你的_client_id" IMA_OPENAPI_APIKEY="你的_api_key" ./.venv/bin/ima-note-mcp-init --ide cursor
```

### CodeBuddy

```bash
git clone https://github.com/wangjianghu/ima-note-mcp.git
cd ima-note-mcp
python3 -m venv .venv
./.venv/bin/pip install -e .
IMA_OPENAPI_CLIENTID="你的_client_id" IMA_OPENAPI_APIKEY="你的_api_key" ./.venv/bin/ima-note-mcp-init --ide codebuddy
```

### Claude Desktop（macOS）

```bash
git clone https://github.com/wangjianghu/ima-note-mcp.git
cd ima-note-mcp
python3 -m venv .venv
./.venv/bin/pip install -e .
IMA_OPENAPI_CLIENTID="你的_client_id" IMA_OPENAPI_APIKEY="你的_api_key" ./.venv/bin/ima-note-mcp-init --ide claude
```

## 一行安装（推荐）

```bash
curl -fsSL https://raw.githubusercontent.com/wangjianghu/ima-note-mcp/main/remote-install.sh | bash -s -- --repo-url https://github.com/wangjianghu/ima-note-mcp.git --ref main --ide "你的_ide" --client-id "你的_client_id" --api-key "你的_api_key"
```

## IDE 配置路径

```bash
./.venv/bin/ima-note-mcp-init --ide trae
./.venv/bin/ima-note-mcp-init --ide cursor
./.venv/bin/ima-note-mcp-init --ide codebuddy
./.venv/bin/ima-note-mcp-init --ide claude
```

默认配置文件路径：
- Trae: `.trae/mcp.json`
- Cursor: `.cursor/mcp.json`
- CodeBuddy: `.codebuddy/mcp.json`
- Claude Desktop(macOS): `~/Library/Application Support/Claude/claude_desktop_config.json`

自定义路径示例：

```bash
./.venv/bin/ima-note-mcp-init --ide cursor --config-path /your/path/mcp.json
```

## 凭证要求

```bash
export IMA_OPENAPI_CLIENTID="your_client_id"
export IMA_OPENAPI_APIKEY="your_api_key"
```

建议写入 `~/.zshrc` 或 `~/.bashrc`，避免终端重启后失效。

也支持配置文件方式：

```bash
mkdir -p ~/.config/ima
echo "your_client_id" > ~/.config/ima/client_id
echo "your_api_key" > ~/.config/ima/api_key
```

凭证预检：

```bash
./.venv/bin/ima-note-mcp --help >/dev/null
```

或在 MCP 客户端中先调用 `ima.credentials_check`（可选 `check_remote=true`）。

`ima.credentials_check` 现在会返回：
- `credential_source.client_id`：`env` / `file` / `missing`
- `credential_source.api_key`：`env` / `file` / `missing`
- `credential_source.effective`：`env` / `file` / `mixed` / `missing`
- `client_id_file_status` / `api_key_file_status`：`present` / `missing` / `empty` / `unreadable`

## 可用工具（MCP）

- `ima.credentials_check`
- `ima.folder.list`
- `ima.note.list`
- `ima.note.search`
- `ima.note.get_content`
- `ima.note.create`
- `ima.note.append`
- `ima.note.list_recent`
- `ima.knowledge.list`
- `ima.knowledge.list_addable`
- `ima.knowledge.get_info`
- `ima.knowledge.search`
- `ima.knowledge.list_content`
- `ima.knowledge.get_media_info`
- `ima.knowledge.create_media`
- `ima.knowledge.add`
- `ima.workflow.add_note_to_knowledge`
- `ima.workflow.note_to_knowledge`
- `ima.workflow.get_knowledge_source`
- `ima.update.check`

## 接口选择建议

- 搜索笔记：`ima.note.search`
- 查看笔记本列表：`ima.folder.list`
- 浏览某笔记本/全部笔记：`ima.note.list`（`folder_id=""` 即全部）
- 读取正文：`ima.note.get_content`（推荐 `target_content_format=0`）
- 新建笔记：`ima.note.create`（`content_format=1`）
- 追加笔记：`ima.note.append`（`content_format=1`）
- 最近更新：`ima.note.list_recent`
- 浏览知识库：`ima.knowledge.list`
- 获取可写入知识库：`ima.knowledge.list_addable`
- 获取知识库详情：`ima.knowledge.get_info`
- 搜索知识库内容：`ima.knowledge.search`
- 浏览知识库条目：`ima.knowledge.list_content`
- 获取知识库条目详情：`ima.knowledge.get_media_info`
- 上传文件前置：`ima.knowledge.create_media`（创建 media 并拿到上传参数）
- 添加知识库条目：`ima.knowledge.add`（可用于网页、笔记等类型）
- 将笔记加入知识库：`ima.workflow.add_note_to_knowledge`
- 将笔记内容直接写入知识库：`ima.workflow.note_to_knowledge`
- 从知识库条目回看原始来源：`ima.workflow.get_knowledge_source`

## 知识库工具用户手册

### 常见流程

- 浏览知识库：先调用 `ima.knowledge.list`，拿到 `knowledge_id`
- 查可写知识库：调用 `ima.knowledge.list_addable`
- 搜索知识库内容：调用 `ima.knowledge.search`
- 浏览知识库条目：调用 `ima.knowledge.list_content`
- 获取条目详情 / 判断是否支持查看原文：调用 `ima.knowledge.get_media_info`
- 关联网页到知识库：调用 `ima.knowledge.add`，并传入 `media_type` 与 `source_url`
- 上传文件到知识库：先 `ima.knowledge.create_media` 获取上传参数，完成文件上传后再 `ima.knowledge.add`
- 将笔记关联到知识库：调用 `ima.knowledge.add`，并使用 `media_type=11` 与笔记对应 `media_id`

### 条目详情与原文能力判断

- `ima.knowledge.get_media_info` 返回标准化条目字段：
  - `media_id` / `media_type`
  - `title` / `summary` / `description`
  - `source_url` / `download_url`
  - `file_name` / `file_size` / `content_type`
  - `knowledge_id` / `knowledge_name`
  - `note_doc_id`
- 其中几个判断字段可直接用于后续工作流：
  - `view_source_supported`：是否支持查看原文
  - `analyze_source_supported`：是否支持基于原始内容继续分析
  - `export_source_supported`：是否支持导出原文
  - `requires_note_module`：若为 `true`，说明当前知识库条目本质是笔记，需要再调用 `ima.note.get_content`
- 推荐判断逻辑：
  - 有 `download_url`：通常可导出原文
  - 有 `source_url`：通常可回看网页原文
  - `media_type=11` 且有 `note_doc_id`：应切换到笔记模块继续读取正文

### 跨模块工作流

#### 1) 把笔记加入知识库

```bash
# 工作流工具
ima.workflow.add_note_to_knowledge(
  knowledge_id="你的知识库ID",
  note_doc_id="你的笔记doc_id",
  title="可选标题"
)
```

返回的统一字段包括：
- `workflow=add_note_to_knowledge`
- `knowledge_id`
- `note_doc_id`
- `knowledge_item_id`
- `media_id`

#### 2) 将笔记内容直接写入知识库

```bash
# 工作流工具（自动完成文件创建和上传）
ima.workflow.note_to_knowledge(
  knowledge_id="你的知识库ID",
  content="笔记正文内容（Markdown）",
  title="可选标题"
)
```

工作流程：
1. 创建笔记（获取 note_id）
2. 创建临时文件并获取 COS 上传凭证
3. 上传文件到腾讯云 COS
4. 关联到知识库

返回的统一字段包括：
- `workflow=note_to_knowledge`
- `knowledge_id`
- `note_doc_id`
- `file_name`
- `file_size`
- `media_id`
- `knowledge_item_id`
- `title`
- `added=true`

#### 3) 从知识库条目回看原始内容

```bash
# 工作流工具
ima.workflow.get_knowledge_source(
  media_id="知识库条目media_id",
  privacy_mode="normal"
)
```

返回的统一字段包括：
- `workflow=get_knowledge_source`
- `source_kind`：`note` / `web` / `file` / `unknown`
- `next_action`：下一步建议动作
- `requires_note_module`：是否需要切换到 notes 模块
- `item`：知识库条目详情
- `source`：已取回的原始来源摘要或正文

常见行为：
- 若条目本质是笔记：自动继续读取 `ima.note.get_content`
- 若条目是网页：返回 `source_url`，提示打开原网页
- 若条目是文件：返回 `download_url`，提示下载原文件

### 参数边界与校验

- `limit`：必须为 `1~100` 的整数
- `start/end`：`start >= 0` 且 `end > start`
- `knowledge_id`：必须为非空字符串
- `media_type`：必须为正整数
- `source_url`：如传入，必须是 `http://` 或 `https://` 开头

参数非法时，统一返回：
- `success=false`
- `error.code=IMA_PARAM_INVALID`

### 支持 / 不支持 清单

- 支持：
  - 常规网页链接：`http://` / `https://`
  - 常见文档类文件上传：如 PDF、Word、Excel、Markdown、文本、图片
- 不支持：
  - `file://` 本地文件链接
  - Bilibili / YouTube 视频链接
  - 直接视频文件上传：如 `mp4`、`mov`、`mkv`、`avi`、`webm`
- 遇到不支持类型时，MCP 会直接返回 `IMA_UNSUPPORTED_MEDIA`，并提示改用 IMA 桌面客户端处理。

### 分页建议

- `ima.knowledge.list` 与 `ima.knowledge.list_addable`：首次 `cursor="0"`，后续使用 `next_cursor`
- `ima.knowledge.list_content`：首次 `cursor=""`，后续使用 `next_cursor`
- `ima.knowledge.search`：使用 `start/end` 偏移分页

### 错误处理建议

- `IMA_RATE_LIMITED`：可重试，建议指数退避
- `IMA_TIMEOUT`：可重试，建议缩小单次请求范围并重试
- `IMA_NOT_FOUND`：检查 `knowledge_id`、`media_id` 或账号权限
- `IMA_AUTH_MISSING` / `IMA_AUTH_INVALID`：检查凭证配置与有效性
- `IMA_UPSTREAM_BUSINESS_ERROR`：表示 HTTP 已成功，但上游业务层 `code != 0`

### 返回字段补充说明

- `ima.knowledge.list_content` 现在会尽量补齐以下字段：
  - `download_url`
  - `file_name`
  - `file_size`
  - `content_type`
  - `note_doc_id`
- `ima.knowledge.create_media` 现在额外返回：
  - `file_name`
  - `file_size`
  - `content_type`
  - `upload_url`
  - `upload_method`
- `ima.knowledge.add` 现在额外返回：
  - `title`
  - `source_url`

## 凭证读取优先级

- 首先读取环境变量：`IMA_OPENAPI_CLIENTID` / `IMA_OPENAPI_APIKEY`
- 如环境变量缺失，则回退读取：`~/.config/ima/client_id` / `~/.config/ima/api_key`
- 可先调用 `ima.credentials_check` 查看当前实际命中来源与文件状态

## 分页约定

- `ima.folder.list`：首次 `cursor="0"`，后续用 `next_cursor`
- `ima.note.list`：首次 `cursor=""`，后续用 `next_cursor`
- `ima.note.search`：使用 `start/end` 偏移分页

## 常见问题

### 1) 重启 IDE 后不能用
- 原因：环境变量只在当前终端生效。
- 处理：把 `export IMA_OPENAPI_CLIENTID=...` 和 `export IMA_OPENAPI_APIKEY=...` 写入 `~/.zshrc` 或 `~/.bashrc` 后重启 IDE。

### 2) 不想把真实密钥写进配置文件
- 默认就不会写入真实密钥，配置里使用 `${IMA_OPENAPI_CLIENTID}` 与 `${IMA_OPENAPI_APIKEY}` 占位符。
- 只在你明确传 `--embed-env-secrets` 时，才会把当前终端变量写入配置。

### 3) “最近/最新笔记”应该怎么取
- 直接用 `ima.note.list_recent`。
- 或 `ima.note.list` 且 `folder_id=""`，表示从全部笔记拉取。

### 4) 在 Cursor 看不到 MCP
- 原因 1：初始化时 `--ide` 参数与实际 IDE 不一致。
- 处理 1：如果使用 Cursor，必须执行 `./.venv/bin/ima-note-mcp-init --ide cursor`，配置会写入 `.cursor/mcp.json`。
- 原因 2：打开的不是项目根目录。
- 处理 2：确保 Cursor 打开的目录就是包含 `.cursor/mcp.json` 的目录，然后重启 Cursor。

## 写入幂等说明

- `ima.note.create` 与 `ima.note.append` 现在支持本地轻量幂等保护。
- 当前策略：
  - 若传入 `idempotency_key`，会基于 `操作名 + 请求体` 生成指纹。
  - 同一个 `idempotency_key` 且请求体完全一致：直接复用首次成功结果，不再重复调用上游。
  - 同一个 `idempotency_key` 但请求体不同：直接返回 `IMA_DUPLICATE_REQUEST`。
- 成功响应中会额外返回：
  - `idempotency_key`
  - `idempotent_hit`
- 说明：
  - `idempotent_hit=false`：本次真实调用了上游
  - `idempotent_hit=true`：本次命中了本地幂等缓存
- 当前实现为进程内轻量缓存：
  - 默认缓存 1 小时
  - 最多保留 512 条成功记录
  - 重启 MCP 进程后缓存会清空

## 更新检查说明

- 可用工具：`ima.update.check`
- 更新检查默认是可选能力，只有在配置了 `IMA_NOTE_MCP_UPDATE_CHECK_URL` 后才会联网检查。
- 自动触发时机：
  - 每天首次调用上游 API 前，最多自动检查一次
  - 若当天已检查过，则直接跳过
- 手动检查：

```bash
ima.update.check(force=true)
```

- 返回字段包括：
  - `current_version`
  - `latest_version`
  - `update_available`
  - `release_desc`
  - `instruction`
  - `download_url`
- 相关环境变量：
  - `IMA_NOTE_MCP_UPDATE_CHECK_URL`：更新清单 JSON 地址
  - `IMA_NOTE_MCP_DISABLE_UPDATE_CHECK=1`：关闭自动与手动更新检查
  - `IMA_NOTE_MCP_FORCE_UPDATE_CHECK=1`：忽略当天缓存，强制在 API 调用前检查
- 推荐的更新清单 JSON 结构：

```json
{
  "latest_version": "0.2.0",
  "release_desc": "修复若干问题并补充知识库能力",
  "instruction": "请升级到 0.2.0 后重试",
  "download_url": "https://example.com/releases/0.2.0"
}
```

## 隐私与安全建议

- 笔记正文属于用户隐私，不建议在群聊场景直接回传完整正文。
- 需要隐私保护时，使用 `ima.note.get_content` 的 `privacy_mode="safe_summary"` 仅返回摘要。
- `ima-note-mcp-init` 默认不将真实密钥写入配置文件。

## 业务错误码兼容说明

- MCP 现在兼容两类上游响应：
  - 直接业务对象：如 `{ "folders": [...] }`
  - 包裹结构：如 `{ "code": 0, "msg": "ok", "data": {...} }`
- 当上游返回 `code != 0` 时，MCP 会直接转成统一错误，而不会继续误判为成功。
- 常见映射关系：
  - 参数问题 -> `IMA_PARAM_INVALID`
  - 权限/凭证问题 -> `IMA_AUTH_INVALID`
  - 资源不存在 -> `IMA_NOT_FOUND`
  - 限流 -> `IMA_RATE_LIMITED`
  - 超时 -> `IMA_TIMEOUT`
  - 其他业务失败 -> `IMA_UPSTREAM_BUSINESS_ERROR`
- 如需排查，可查看错误详情中的：
  - `error.details.upstream_code`
  - `error.details.upstream_message`
  - `error.details.upstream_data`

## 写入前 UTF-8 校验说明

- `ima.note.create` 和 `ima.note.append` 在发送请求前，会先对 `content` 做本地 UTF-8 安全校验。
- 当前会自动做两类轻量清洗：
  - 去除开头的 UTF-8 BOM
  - 将 `\r\n` / `\r` 统一为 `\n`
- 如果正文里包含非法代理字符等无法安全编码为 UTF-8 的内容，会直接返回：
  - `error.code=IMA_ENCODING_INVALID`
  - `error.message=content 包含非法 UTF-8 字符，请先清理异常字符后重试`
- 如果清洗后正文为空或长度超限，仍会继续按原有规则返回：
  - `IMA_PARAM_INVALID`
  - `IMA_CONTENT_TOO_LARGE`
