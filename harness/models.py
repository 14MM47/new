"""Minimal model callers. Credentials are injected by the environment's proxy."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request


def _post(url: str, body: dict, headers: dict, timeout: int = 240) -> dict:
    data = json.dumps(body).encode()
    last = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **headers})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}: {e.read()[:300]!r}"
            if e.code not in (408, 409, 429, 500, 502, 503, 504):
                break
        except Exception as e:  # timeouts, resets
            last = repr(e)
        time.sleep(2 ** attempt * 2)
    raise RuntimeError(last)


def call_openai(model: str, system: str, messages: list) -> dict:
    body = {"model": model, "messages": [{"role": "system", "content": system}] + messages,
            "max_completion_tokens": 12000}
    d = _post("https://api.openai.com/v1/chat/completions", body, {"Authorization": "Bearer sk-proxy-injected"})
    usage = d.get("usage", {})
    hidden = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0) or 0
    out = usage.get("completion_tokens", 0)
    return {"text": d["choices"][0]["message"]["content"] or "", "prompt_tokens": usage.get("prompt_tokens", 0),
            "visible_tokens": out - hidden, "output_tokens": out}


def call_gemini(model: str, system: str, messages: list) -> dict:
    contents = [{"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
                for m in messages]
    body = {"systemInstruction": {"parts": [{"text": system}]}, "contents": contents,
            "generationConfig": {"maxOutputTokens": 12000}}
    d = _post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", body, {})
    parts = (d.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
    usage = d.get("usageMetadata", {})
    visible = usage.get("candidatesTokenCount", 0)
    return {"text": "".join(p.get("text", "") for p in parts), "prompt_tokens": usage.get("promptTokenCount", 0),
            "visible_tokens": visible, "output_tokens": visible + usage.get("thoughtsTokenCount", 0)}


def call(spec: str, system: str, messages: list) -> dict:
    provider, model = spec.split(":", 1)
    return {"openai": call_openai, "gemini": call_gemini}[provider](model, system, messages)
