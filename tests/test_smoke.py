"""最小验证：验签、去重、派单。不联网、不调模型。

server 在 import 时就会读环境变量，所以先把环境摆好。
"""

import hashlib
import hmac
import json
import os

os.environ.update(
    GITHUB_APP_ID="1",
    GITHUB_PRIVATE_KEY="unused",
    GITHUB_INSTALLATION_ID="2",
    GITHUB_WEBHOOK_SECRET="secret",
    LLM_API_KEY="sk-test",
    DRY_RUN="1",
)

from fastapi.testclient import TestClient  # noqa: E402

from bot import tasks  # noqa: E402
from bot.server import _seen, app  # noqa: E402

SECRET = "secret"
ISSUE = {
    "action": "opened",
    "repository": {"full_name": "you/repo"},
    "issue": {"number": 1, "title": "启动就报 500", "body": "日志里只有一行 error"},
    "sender": {"type": "User"},
}


def post(raw: bytes, delivery: str = "d1", event: str = "issues", secret: str = SECRET):
    sig = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return TestClient(app).post(
        "/webhook",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": delivery,
            "X-Hub-Signature-256": sig,
        },
    )


def setup_function():
    _seen.clear()


def test_bad_signature_is_401():
    r = post(json.dumps(ISSUE).encode(), secret="wrong")
    assert r.status_code == 401


def test_duplicate_delivery_is_skipped():
    raw = json.dumps(ISSUE).encode()
    assert post(raw).status_code == 202
    assert post(raw).json() == {"skipped": "duplicate delivery"}


def test_label_failure_still_posts_comment(monkeypatch):
    """标签没建好不该连回帖一起吞掉——这条以前是整段 try 一起挂。"""
    posted = []

    class FakeGH:
        def add_labels(self, *a):
            raise RuntimeError("422: label does not exist")

        def already_commented(self, *a):
            return False

        def comment(self, repo, number, body):
            posted.append(body)

    monkeypatch.setattr(
        tasks.llm, "chat", lambda *a, **k: '{"labels": ["bug"], "reply": "收到"}'
    )
    cfg = type("Cfg", (), {"labels": ("bug",)})()
    tasks.triage_issue(FakeGH(), cfg, "you/repo", {"number": 2, "title": "t", "body": "b"})

    assert len(posted) == 1 and tasks.MARKER in posted[0]


def test_issue_opened_dispatches_triage(monkeypatch):
    calls = []
    monkeypatch.setattr(tasks, "triage_issue", lambda *a: calls.append(a))
    r = post(json.dumps(ISSUE).encode())
    assert r.json() == {"status": "accepted", "event": "issues"}
    assert len(calls) == 1
