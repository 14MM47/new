"""The model under test, reached through `claude -p` with tools switched off."""
from __future__ import annotations

import json
import subprocess
import tempfile
import time


def ask(model: str, system: str, prompt: str, timeout: int = 600) -> dict:
    """One stateless completion. Returns text plus token and cost accounting."""
    cmd = ["claude", "-p", "--model", model, "--tools", "", "--strict-mcp-config", "--setting-sources", "",
           "--no-session-persistence", "--system-prompt", system, "--output-format", "json"]
    last = "no attempt made"
    for attempt in range(3):
        try:
            with tempfile.TemporaryDirectory() as cwd:      # an empty directory: nothing of this repository leaks in
                p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout, cwd=cwd)
            d = json.loads(p.stdout)
            if not d.get("is_error") and isinstance(d.get("result"), str):
                u = d.get("usage", {})
                return {"text": d["result"], "output_tokens": u.get("output_tokens", 0),
                        "thinking_tokens": (u.get("output_tokens_details") or {}).get("thinking_tokens", 0),
                        "input_tokens": u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
                        + u.get("cache_read_input_tokens", 0),
                        "cost_usd": d.get("total_cost_usd", 0.0), "served_by": sorted(d.get("modelUsage", {}))}
            last = str(d.get("result") or d)[:300]
        except Exception as e:      # timeout, bad JSON, CLI failure
            last = repr(e)[:300]
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(last)
