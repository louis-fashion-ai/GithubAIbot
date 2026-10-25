"""Webhook 入口。

GitHub 只给我们 10 秒，调模型动辄几十秒，所以验签后立刻回 202，活儿丢后台任务。
"""

import hashlib
import hmac
import json
import logging
import time

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from . import config, github_api, tasks

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("githubaibot")

cfg = config.load()
gh = github_api.GitHub(cfg)
app = FastAPI(title="GithubAIbot")

_seen: dict = {}             # delivery id -> 时间戳，防 GitHub 重投
_TTL = 14 * 24 * 3600       # 覆盖 GitHub 的重试窗口再多留点余量


def verify(body: bytes, sig: str, secret: str) -> None:
    """必须用原始字节验签：先 body() 后 json()，顺序反了算出来的签名对不上。"""
    expected = "sha256=" + hmac.new(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise HTTPException(401, "bad signature")


def first_time(delivery_id: str) -> bool:
    now = time.time()
    for k, t in list(_seen.items()):
        if now - t > _TTL:
            _seen.pop(k, None)
    if delivery_id in _seen:
        return False
    _seen[delivery_id] = now
    return True


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/webhook")
async def webhook(request: Request, bg: BackgroundTasks):
    body = await request.body()
    verify(body, request.headers.get("X-Hub-Signature-256", ""), cfg.webhook_secret)

    event = request.headers.get("X-GitHub-Event", "")
    delivery = request.headers.get("X-GitHub-Delivery", "")
    if not first_time(delivery):
        return {"skipped": "duplicate delivery"}

    payload = json.loads(body)
    # 自己发的评论不再触发自己，否则无限套娃
    if payload.get("sender", {}).get("type") == "Bot":
        return {"skipped": "bot event"}

    repo = payload["repository"]["full_name"]
    opened = payload.get("action") == "opened"
    item = payload.get("issue") or payload.get("pull_request") or {}

    if event == "issues" and opened:
        bg.add_task(tasks.triage_issue, gh, cfg, repo, payload["issue"])
    elif event == "pull_request" and opened:
        bg.add_task(tasks.review_pr, gh, cfg, repo, payload["pull_request"])
    else:
        return {"skipped": f"{event}/{payload.get('action')}"}

    log.info("accepted %s %s#%s", event, repo, item.get("number"))
    return JSONResponse({"status": "accepted", "event": event}, status_code=202)
