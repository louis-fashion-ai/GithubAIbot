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
  `202`，真正的活儿丢 `BackgroundTasks`。超时会记成一次失败投递，而 GitHub **不会自动重投**
  （官方原话：GitHub does not automatically redeliver failed deliveries），这条通知就直接丢了。
- **用 delivery id 去重**：按 `X-GitHub-Delivery` 去重，不是按 issue 号——同一 issue 的第二次评论是
  新事件。重投只能人工点（只保留 3 天的记录），加上本地 smee/ngrok 转发偶尔重发，这层去重省不掉。
  记录留 14 天，够用。
- **GitHub App，不是 PAT**：个人 token 等于那个人本人，泄露就是他名下所有仓库。App 的
  installation token 只有被勾选的权限、只覆盖被安装的仓库，1 小时自动过期。
  认证是两步：私钥签 RS256 JWT（`iat` 往前挪 60 秒容忍时钟漂移，`exp` 最多 10 分钟）
  → 换 installation token → 用它调资源 API，过期前 5 分钟续。
- **权限最小化**：只需要 `Issues: read/write`、`Pull requests: read/write`、`Metadata: read`。
  代码里也守着边界——只评论和打标签，不关 issue、不合并 PR、不碰 secrets。
- **正文是不可信输入**：issue 正文和 diff 里可能夹着"忽略以上规则"这类提示词注入，
  在 system prompt 里明确告诉模型：那是问题，不是指令。
- **模型挂了不算 5xx**：异常吞掉记日志。回 5xx 也没人替你重试，只是让 Recent deliveries 多一条红记录。
- **跳过 Bot 事件**：不然机器人回的评论会再触发机器人，无限套娃。
- **ping 要单独接住**：建/改 webhook 时 GitHub 立刻 ping 一下探活，那条 payload 里没有
  `repository`；不早退的话取 `full_name` 直接 KeyError，页面上一片红。`installation` 那几个事件同理。
- **回帖前先看一眼**：重投（或同一 issue 的重复触发）不该刷出第二条评论，
  所以发帖前先按评论里的 `<!-- githubaibot:v1 -->` 标记查一次，查过了就跳过。
- **打标签失败不挡回帖**：仓库里没建这个标签、或者权限没给全，GitHub 直接返 422。
  标签是锦上添花，回帖才是主功能，所以那一步单独 try 掉，只记一条 warning。

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

`.env` 里设 `DRY_RUN=1`，然后发一条签名正确的假事件：

```bash
GITHUB_WEBHOOK_SECRET=你配的secret python scripts/dev_webhook.py      # issue
GITHUB_WEBHOOK_SECRET=你配的secret python scripts/dev_webhook.py pr   # PR
```

返回 `{"status":"accepted",...}`，终端里能看到 dry-run 打算发的评论和标签。
脚本每次用新的 delivery id；想验去重，把打印出来的 id 用 `DELIVERY_ID=...` 再发一次即可。

注意 `DRY_RUN` 只挡住发帖，模型照样会调——没有 key 时会看到一条"分诊失败"的日志。
PR 那条路还要先换 installation token 才能拉 diff，所以私钥得是真的。

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
scripts/
  dev_webhook.py  发一条签名正确的假事件，本地联调用
tests/
  test_smoke.py   五个不过网的用例
```

四个文件各管一件事，加新事件（比如 `@bot` 提问回复）就在 `tasks.py` 加个函数、
在 `server.py` 多一个分支。

## 已经核对过的几点（2026-11 复查）

对着 GitHub 官方实践和别人的踩坑记录复查了一遍，上面那些设计都还站得住，另外记两条：

- **installation token 的格式在变**：GitHub 从 2026-04 开始分阶段换成 `ghs_<AppID>_<JWT>`
  这种无状态格式。凡是假设 token 固定 40 字符的代码会挂，本仓库没这个假设，
  但如果你接了别的 SDK，值得回去查一眼。
- **别在 import 时去换 token**：进程冷启动（serverless、容器缩到零）时那笔网络往返
  可能拖到超时，而且缓存里的 token 醒来就没了。这里是在第一次发帖时才换，
  过期前 5 分钟续，换失败就让它失败——GitHub 不会自动重投，想补就人工去
  Advanced → Recent deliveries 点 Redeliver。
- **上一版 README 写错过一件事**：此前把"失败投递会触发重投"当成了默认行为，查了官方
  Handling failed webhook deliveries 才确认 GitHub 根本不自动重试。超时/5xx 只是被记成
  失败投递，消息不会再来第二次——所以入口尽快回 202 的意义比原来想的还大。
