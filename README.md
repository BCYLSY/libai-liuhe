# 李白六合

六合是运行在家中 Windows 宿主机上的只读 Agent Reach 多能力网关。业务容器通过内部 HTTP API 获取结构化数据，不在各自容器内重复安装平台 CLI，也不持有浏览器登录态或 X Cookie。

当前能力：

- Twitter/X：读取任意合法用户名的近期原创动态；显式 Cookie 可用时使用 `twitter-cli`，否则自动复用 Chrome 的 OpenCLI 会话。
- Web：通过 Jina Reader 读取公开网页正文。
- Bilibili：搜索、热门、视频详情使用 `bili-cli`；字幕使用 OpenCLI。
- 小红书：搜索、带 `xsec_token` 的笔记详情和评论使用 OpenCLI。

## 安全边界

- 服务只允许监听回环地址，默认 `127.0.0.1:9204`，不得暴露到公网或 Tailscale。
- 每个调用方必须提供独立的调用方 ID、Bearer Token 和平台 scope。
- 所有 CLI、适配器与子命令由六合固定，不接受任意命令、Shell 参数或可执行文件名。
- Web 只接受 HTTP/HTTPS 公网地址，拒绝用户信息、非标准端口、本机和私有 IP；可进一步配置域名白名单。
- X 接口每次只查询一个合法用户名，单次返回数量由 `LIUHE_TWITTER_MAX_POSTS_PER_REQUEST` 限制。
- `twitter-cli` 子进程只读取 Agent Reach 显式保存的 Cookie，并使用临时空用户目录；没有显式 Cookie 时才调用固定只读的 OpenCLI `twitter tweets` 命令复用 Chrome 会话。
- 小红书只调用 OpenCLI 的只读命令，不执行登录、发布、点赞、关注等写操作。
- 各平台调用串行执行并使用短期内存缓存；小红书调用之间至少间隔 3 秒。

## 首次配置

先检查宿主机 Agent Reach 后端：

```powershell
agent-reach doctor --json
```

Twitter/X 默认可复用已经登录的 Chrome OpenCLI 会话，无需导出 Cookie。如果希望脱离浏览器使用 `twitter-cli`，再通过 Cookie-Editor 手工导出并配置：

```powershell
agent-reach configure twitter-cookies
```

小红书使用现有 Chrome 登录态。请自行在 Chrome 登录 `xiaohongshu.com` 并保持 OpenCLI 桥接可用；六合不会替你登录或提取 Cookie。

Web 能力会自动复用 Agent Reach 配置中的 `proxy`，无需在六合 `.env` 中重复配置代理。

安装六合 Python 环境：

```powershell
.\scripts\setup.ps1
```

复制配置模板并生成至少 32 字符的随机 Token：

```powershell
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
```

把生成值写入 `.env` 的 `LIUHE_CLIENT_TOKENS`，并在 `LIUHE_CLIENT_SCOPES` 为该调用方列出实际需要的平台。两个 JSON 对象中的调用方名称必须完全一致。

示例：

```dotenv
LIUHE_CLIENT_TOKENS={"changfeng":"生成的随机值","another-service":"另一个随机值"}
LIUHE_CLIENT_SCOPES={"changfeng":["twitter"],"another-service":["web","bilibili","xiaohongshu"]}
```

真实 `.env` 不得提交。

## 启动

前台调试运行：

```powershell
.\scripts\start-api.ps1
```

宿主机生产发布（注册为当前 Windows 用户登录时自动启动的计划任务，并立即启动）：

```powershell
.\scripts\install-task.ps1
```

取消发布：

```powershell
.\scripts\uninstall-task.ps1
```

安装脚本优先注册名为 `Libai-Liuhe` 的计划任务；如果当前 Windows 会话没有任务计划权限，则自动改用当前用户的登录启动项。运行日志写入 `liuhe.service.log`。

宿主机地址为 `http://127.0.0.1:9204`。Docker Desktop 中的产品容器使用 `http://host.docker.internal:9204`。

未配置某个平台时服务仍可启动，`/health` 会显示各能力的后端状态；实际调用不可用能力时返回明确的 `5xx` 错误。

## 认证

除 `/health` 外，所有接口必须同时携带：

```http
X-Liuhe-Client: changfeng
Authorization: Bearer <该调用方的 Token>
```

`GET /v1/capabilities` 返回当前调用方可见的能力及授权状态。

## API

### Twitter/X

```http
GET /v1/twitter/users/btibor91/posts?limit=20
```

### Web

```http
POST /v1/web/read
Content-Type: application/json

{"url":"https://example.com/article"}
```

### Bilibili

```http
GET /v1/bilibili/search?q=关键词&limit=10
GET /v1/bilibili/hot?limit=10
GET /v1/bilibili/videos/BV1mDtL6hE4x
GET /v1/bilibili/videos/BV1mDtL6hE4x/subtitle
```

### 小红书

```http
GET /v1/xiaohongshu/search?q=关键词&limit=10
GET /v1/xiaohongshu/notes/笔记ID/comments
```

笔记详情必须使用搜索结果中带 `xsec_token` 的完整 URL，并放在请求体中，避免 Token 出现在访问日志：

```http
POST /v1/xiaohongshu/notes/read
Content-Type: application/json

{"url":"https://www.xiaohongshu.com/explore/笔记ID?xsec_token=..."}
```

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest
```
