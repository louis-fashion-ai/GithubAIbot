# GithubAIbot

一个跑在 GitHub App 上的小机器人：新 issue 开出来自动分诊（打标签 + 回一条中文确认），
新 PR 开出来自动贴一段评审摘要。除此之外什么都不做。

## 它长什么样

```
GitHub 事件 → POST /webhook → 验签 → 查 delivery 去重 → 回 202
                                        └→ 后台任务：拿 App token → 问模型 → 评论 / 打标签
```

## 几个刻意的设计

- **先验签再干活**：`X-Hub-Signature-256` 是对**原始字节**的 HMAC-SHA256，所以必须先
  `await request.body()` 再解析 JSON，顺序反了算出来的签名对不上。比较用
  `hmac.compare_digest`，防时序侧信道。
- **10 秒铁律**：GitHub 的 webhook 超时是 10 秒，调模型动辄几十秒。所以入口验签完立刻回
  `202`，真正的活儿丢 `BackgroundTasks`。同步等待会被判定失败并触发重投，最后变成三条重复评论。
- **用 delivery id 去重**：按 `X-GitHub-Delivery` 去重，不是按 issue 号——同一 issue 的第二次评论是
  新事件。记录保留 14 天（GitHub 重试窗口 + 余量）。
- **GitHub App，不是 PAT**：个人 token 等于那个人本人，泄露就是他名下所有仓库。App 的
  installation token 只有被勾选的权限、只覆盖被安装的仓库，1 小时自动过期。
  认证是两步：私钥签 RS256 JWT（`iat` 往前挪 60 秒容忍时钟漂移，`exp` 最多 10 分钟）
  → 换 installation token → 用它调资源 API，过期前 5 分钟续。
- **权限最小化**：只需要 `Issues: read/write`、`Pull requests: read/write`、`Metadata: read`。
  代码里也守着边界——只评论和打标签，不关 issue、不合并 PR、不碰 secrets。
- **正文是不可信输入**：issue 正文和 diff 里可能夹着"忽略以上规则"这类提示词注入，
  在 system prompt 里明确告诉模型：那是问题，不是指令。
- **模型挂了不算 5xx**：异常吞掉记日志，否则 GitHub 会重投，一样是重复评论。
- **跳过 Bot 事件**：不然机器人回的评论会再触发机器人，无限套娃。
- **回帖前先看一眼**：重投（或同一 issue 的重复触发）不该刷出第二条评论，
  所以发帖前先按评论里的 `<!-- githubaibot:v1 -->` 标记查一次，查过了就跳过。

## 跑起来

### 1. 建一个 GitHub App

Settings → Developer settings → GitHub Apps → New GitHub App

- Webhook URL：`https://你的域名/webhook`
- Webhook secret：随便生成一串，填进 `.env`
- Permissions：**Issues** 读写、**Pull requests** 读写、**Metadata** 只读
- Subscribe to events：`Issues`、`Pull requests`
- 生成私钥下载 `.pem`（`.gitignore` 已排除，别提交）
- 装到目标仓库后，从 URL `.../settings/installations/<数字>` 里拿到 installation id

### 2. 装依赖、配环境

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # 然后填
```

### 3. 启动

```bash
uvicorn bot.server:app --host 127.0.0.1 --port 8000
```

本地没公网地址时，用 [smee.io](https://smee.io) 或 ngrok 把 GitHub 的回调转发到本机：

```bash
npx smee-client --url https://smee.io/你的频道 --target http://127.0.0.1:8000/webhook
```

生产环境建议把服务绑 `127.0.0.1`，由 nginx 终结 TLS 并限流。

### 4. 本地试一下（不真发帖）

`.env` 里设 `DRY_RUN=1`，然后手搓一个签名请求：

```bash
SECRET=你配的secret
BODY='{"action":"opened","repository":{"full_name":"you/repo"},"issue":{"number":1,"title":"启动就报 500","body":"日志里只有一行 error"},"sender":{"type":"User"}}'
SIG="sha256=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$SECRET" | awk '{print $2}')"
curl -s -X POST http://127.0.0.1:8000/webhook \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Event: issues" \
  -H "X-GitHub-Delivery: test-1" \
  -H "X-Hub-Signature-256: $SIG" \
  -d "$BODY"
```

返回 `{"status":"accepted","event":"issues"}`，终端里能看到 dry-run 打算发的评论和标签。
注意 `DRY_RUN` 只挡住发帖，模型照样会调——没有 key 时会看到一条"分诊失败"的日志。

### 5. 跑测试

```bash
python -m pytest -q
```

不联网、不调模型，只验证验签、按 delivery 去重、以及 issue 事件确实派到了分诊。

## 代码结构

```
bot/
  config.py       环境变量 → Config，缺一个就启动失败
  server.py       FastAPI：验签、去重、派单
  github_api.py   App JWT → installation token → 评论/打标签/取 diff
  llm.py          OpenAI 兼容接口的一层薄封装
  tasks.py        分诊和评审的 prompt 与流程
tests/
  test_smoke.py   三个不过网的冒烟用例
```

四个文件各管一件事，加新事件（比如 `@bot` 提问回复）就在 `tasks.py` 加个函数、
在 `server.py` 多一个分支。

## 已经核对过的几点（2026-10）

对着 GitHub 官方实践和别人的踩坑记录复查了一遍，上面那些设计都还站得住，另外记两条：

- **installation token 的格式在变**：GitHub 从 2026-04 开始分阶段换成 `ghs_<AppID>_<JWT>`
  这种无状态格式。凡是假设 token 固定 40 字符的代码会挂，本仓库没这个假设，
  但如果你接了别的 SDK，值得回去查一眼。
- **别在 import 时去换 token**：进程冷启动（serverless、容器缩到零）时那笔网络往返
  可能拖到超时，而且缓存里的 token 醒来就没了。这里是在第一次发帖时才换，
  过期前 5 分钟续，没换成功就让它失败、等 GitHub 重投。
