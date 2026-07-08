"""Minimal DeepSeek open advisor API probe without printing secrets."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from openai import OpenAI

import app.services.deepseek_position_advisor as advisor


def main() -> None:
    print(
        {
            "has_key": bool(advisor.DEEPSEEK_API_KEY),
            "key_len": len(advisor.DEEPSEEK_API_KEY or ""),
            "model": advisor.DEEPSEEK_MODEL,
            "base_url": advisor.DEEPSEEK_BASE_URL,
            "timeout": advisor.DEEPSEEK_TIMEOUT_S,
        }
    )
    client = OpenAI(
        api_key=advisor.DEEPSEEK_API_KEY,
        base_url=advisor.DEEPSEEK_BASE_URL,
    )
    resp = client.chat.completions.create(
        model=advisor.DEEPSEEK_MODEL,
        messages=[
            {"role": "system", "content": "Return JSON only."},
            {
                "role": "user",
                "content": 'Return {"decision":"approve","reason":"ok"} as JSON.',
            },
        ],
        temperature=0,
        max_tokens=80,
        timeout=30,
        response_format={"type": "json_object"},
    )
    print(resp.choices[0].message.content)


if __name__ == "__main__":
    main()
