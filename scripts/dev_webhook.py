"""往本地服务发一条签名正确的假事件，省得每次手搓 curl + openssl。

    python scripts/dev_webhook.py          # issue opened
    python scripts/dev_webhook.py pr       # pr opened
    python scripts/dev_webhook.py ping     # 建 webhook 时 GitHub 发的那条探活

密钥读 GITHUB_WEBHOOK_SECRET（默认 change-me），地址读 WEBHOOK_URL。
每次跑用的是新的 delivery id，所以连跑两次不会被去重挡掉；
要验去重就把 - 出来的 id 记下来，用 DELIVERY_ID 环境变量再发一次。
"""

import hashlib
import hmac
import json
import os
import sys
import uuid

import httpx

SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET", "change-me")
URL = os.environ.get("WEBHOOK_URL", "http://127.0.0.1:8000/webhook")
REPO = os.environ.get("REPO", "you/repo")

ISSUE = {
    "action": "opened",
    "repository": {"full_name": REPO},
    "issue": {
        "number": 1,
        "title": "启动就报 500",
        "body": "日志里只有一行 error，复现命令是 uvicorn bot.server:app。",
    },
    "sender": {"type": "User"},
}

PR = {
    "action": "opened",
    "repository": {"full_name": REPO},
    "pull_request": {"number": 2, "title": "把去重表从 dict 换成了 TTLCache"},
    "sender": {"type": "User"},
}


PING = {"zen": "Keep it local.", "hook_id": 7, "hook": {"id": 7}}

EVENTS = {
    "issue": ("issues", ISSUE),
    "pr": ("pull_request", PR),
    "ping": ("ping", PING),   # 没有 repository，正好验服务不会 500
}


def main(kind: str) -> None:
    event, payload = EVENTS.get(kind, ("issues", ISSUE))
    delivery = os.environ.get("DELIVERY_ID") or f"dev-{uuid.uuid4().hex[:8]}"
    # 签名必须对原始字节算，所以先 dumps 再发，中间不要再动它
    body = json.dumps(payload, ensure_ascii=False).encode()
    sig = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()

    r = httpx.post(
        URL,
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": delivery,
            "X-Hub-Signature-256": sig,
        },
        timeout=15,
    )
    print(f"{r.status_code} {r.text}")
    print(f"delivery={delivery}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "issue")
