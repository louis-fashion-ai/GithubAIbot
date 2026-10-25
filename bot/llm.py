"""OpenAI 兼容接口的极简封装：丢 prompt 进去，拿文本出来。"""

import httpx


def chat(cfg, system: str, user: str, max_tokens: int = 600) -> str:
    r = httpx.post(
        f"{cfg.llm_base_url}/chat/completions",
        headers={"Authorization": f"Bearer {cfg.llm_api_key}"},
        json={
            "model": cfg.llm_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "max_tokens": max_tokens,
        },
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()
