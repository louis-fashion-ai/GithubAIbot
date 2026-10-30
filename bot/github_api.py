"""GitHub App 认证 + 用到的几个 API。

认证链路：私钥签一个 9 分钟有效的 JWT -> 换一个 1 小时有效的 installation token
-> 拿这个 token 去调资源 API。token 在过期前 5 分钟重新换，别永久缓存。
"""

import time
from datetime import datetime, timezone

import httpx
import jwt

API = "https://api.github.com"
HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


class GitHub:
    def __init__(self, cfg):
        self.cfg = cfg
        self._token = ""
        self._expires_at = 0.0

    def _app_jwt(self) -> str:
        now = int(time.time())
        return jwt.encode(
            # iat 往前挪 60 秒容忍时钟漂移，exp 最多 10 分钟
            {"iat": now - 60, "exp": now + 540, "iss": self.cfg.app_id},
            self.cfg.private_key,
            algorithm="RS256",
        )

    def token(self) -> str:
        if self._token and self._expires_at - time.time() > 300:
            return self._token
        r = httpx.post(
            f"{API}/app/installations/{self.cfg.installation_id}/access_tokens",
            headers={**HEADERS, "Authorization": f"Bearer {self._app_jwt()}"},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        self._token = data["token"]
        self._expires_at = (
            datetime.strptime(data["expires_at"], "%Y-%m-%dT%H:%M:%SZ")
            .replace(tzinfo=timezone.utc)
            .timestamp()
        )
        return self._token

    def _post(self, path: str, payload: dict) -> None:
        r = httpx.post(
            f"{API}{path}",
            headers={**HEADERS, "Authorization": f"Bearer {self.token()}"},
            json=payload,
            timeout=30,
        )
        r.raise_for_status()

    def comment(self, repo: str, number: int, body: str) -> None:
        if self.cfg.dry_run:
            print(f"[dry-run] comment {repo}#{number}\n{body}\n")
            return
        self._post(f"/repos/{repo}/issues/{number}/comments", {"body": body})

    def add_labels(self, repo: str, number: int, labels: list) -> None:
        if not labels:
            return
        if self.cfg.dry_run:
            print(f"[dry-run] labels {repo}#{number} -> {labels}")
            return
        self._post(f"/repos/{repo}/issues/{number}/labels", {"labels": labels})

    def already_commented(self, repo: str, number: int, marker: str) -> bool:
        """重投（或对同一 issue 的重复触发）不该刷出第二条评论。查一次比事后删容易。"""
        if self.cfg.dry_run:
            return False
        r = httpx.get(
            f"{API}/repos/{repo}/issues/{number}/comments",
            headers={**HEADERS, "Authorization": f"Bearer {self.token()}"},
            params={"per_page": 100},
            timeout=30,
        )
        r.raise_for_status()
        return any(marker in c.get("body", "") for c in r.json())

    def pr_diff(self, repo: str, number: int) -> str:
        r = httpx.get(
            f"{API}/repos/{repo}/pulls/{number}",
            headers={
                **HEADERS,
                "Authorization": f"Bearer {self.token()}",
                "Accept": "application/vnd.github.v3.diff",
            },
            timeout=30,
        )
        r.raise_for_status()
        return r.text[:20000]
