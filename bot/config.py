"""配置：全部从环境变量读。缺一个就启动失败，别等到半夜线上才炸。"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    app_id: str
    private_key: str          # PEM 文本，不落盘、不进日志
    installation_id: str
    webhook_secret: str
    llm_api_key: str
    llm_base_url: str
    llm_model: str
    labels: tuple            # 分诊时允许打的标签白名单
    dry_run: bool            # True 时只打印不发帖，本地调试用


def _req(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        raise SystemExit(f"缺少环境变量：{name}")
    return v


def load() -> Config:
    key = os.environ.get("GITHUB_PRIVATE_KEY", "").strip()
    if not key:
        with open(_req("GITHUB_PRIVATE_KEY_PATH"), encoding="utf-8") as f:
            key = f.read()

    return Config(
        app_id=_req("GITHUB_APP_ID"),
        private_key=key,
        installation_id=_req("GITHUB_INSTALLATION_ID"),
        webhook_secret=_req("GITHUB_WEBHOOK_SECRET"),
        llm_api_key=_req("LLM_API_KEY"),
        llm_base_url=os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1"),
        llm_model=os.environ.get("LLM_MODEL", "gpt-4o-mini"),
        labels=tuple(
            x.strip()
            for x in os.environ.get(
                "TRIAGE_LABELS", "bug,feature,question,docs,chore"
            ).split(",")
            if x.strip()
        ),
        dry_run=os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes"),
    )
