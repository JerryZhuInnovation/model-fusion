#!/usr/bin/env python3
"""Probe available OpenAI-compatible APIs for model-fusion."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

KNOWN: list[dict[str, Any]] = [
    {
        "id": "openai",
        "display": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "api_key_env": "OPENAI_API_KEY",
    },
    {
        "id": "anthropic",
        "display": "Anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "model": "claude-sonnet-4-5",
        "api_key_env": "ANTHROPIC_API_KEY",
        "api_format": "anthropic",
    },
    {
        "id": "openrouter",
        "display": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "openai/gpt-4o-mini",
        "api_key_env": "OPENROUTER_API_KEY",
        "api_format": "openai",
    },
    {
        "id": "deepseek",
        "display": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "api_key_env": "DEEPSEEK_API_KEY",
        "api_format": "openai",
    },
    {
        "id": "moonshot",
        "display": "Moonshot",
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-8k",
        "api_key_env": "MOONSHOT_API_KEY",
        "api_format": "openai",
    },
    {
        "id": "gemini",
        "display": "Google Gemini",
        # Gemini OpenAI 兼容层
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model": "gemini-2.0-flash",
        "api_key_env": "GEMINI_API_KEY",
        "alt_envs": ["GOOGLE_API_KEY"],
        "api_format": "openai",
    },
]

HINT_PATHS = [
    os.path.expanduser("~/.claude/.credentials.json"),
    os.path.expanduser("~/.claude/credentials.json"),
    os.path.expanduser("~/.cursor/mcp.json"),
]


def _get_key(entry: dict[str, Any]) -> str | None:
    envs = [entry["api_key_env"]]
    envs.extend(entry.get("alt_envs") or [])
    for name in envs:
        val = os.environ.get(name)
        if val:
            return val
    return None


def ping_one(entry: dict[str, Any], timeout_s: float) -> dict[str, Any]:
    key = _get_key(entry)
    fmt = entry.get("api_format", "openai")
    out = {
        "id": entry["id"],
        "display": entry["display"],
        "base_url": entry["base_url"],
        "model": entry["model"],
        "api_key_env": entry["api_key_env"],
        "api_format": fmt,
        "ping_ok": False,
        "ping_ms": None,
        "roles": [],
        "note": None,
    }

    if fmt not in ("openai", "anthropic"):
        out["note"] = f"unsupported api_format: {fmt}"
        return out

    if not key:
        out["note"] = f"missing key env: {entry['api_key_env']}"
        return out

    url, headers, body = _ping_request(entry, key, fmt)
    body_bytes = json.dumps(body).encode("utf-8")
    started = time.monotonic()
    req = urllib.request.Request(url, data=body_bytes, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            _ = resp.read(256)
            out["ping_ok"] = True
            out["roles"] = ["worker", "judge"]
    except urllib.error.HTTPError as e:
        body_txt = e.read()[:200].decode("utf-8", "replace") if e.fp else ""
        if e.code in (401, 403):
            out["note"] = f"auth failed: HTTP {e.code}"
        else:
            out["ping_ok"] = True
            out["roles"] = ["worker", "judge"]
            out["note"] = f"reachable with HTTP {e.code}: {body_txt}"
    except Exception as e:  # noqa: BLE001
        out["note"] = f"ping error: {type(e).__name__}: {e}"
    out["ping_ms"] = int((time.monotonic() - started) * 1000)
    return out


def _ping_request(
    entry: dict[str, Any], key: str, fmt: str
) -> tuple[str, dict[str, str], dict[str, Any]]:
    base = entry["base_url"].rstrip("/")
    model = entry["model"]
    if fmt == "anthropic":
        url = f"{base}/messages"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        }
        body = {
            "model": model,
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "ping"}],
        }
        return url, headers, body

    url = f"{base}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
    }
    if entry["id"] == "gemini":
        headers["x-goog-api-key"] = key
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
    }
    return url, headers, body


def scan_hints() -> list[dict[str, Any]]:
    hints = []
    for path in HINT_PATHS:
        if os.path.exists(path):
            hints.append(
                {
                    "id": f"hint:{os.path.basename(path)}",
                    "display": os.path.basename(path),
                    "base_url": "",
                    "model": "",
                    "api_key_env": "",
                    "ping_ok": False,
                    "ping_ms": None,
                    "roles": [],
                    "note": "仅提示：检测到本地登录痕迹，无 HTTP API，不可作 worker",
                }
            )
    return hints


def host_model_hint() -> str | None:
    for name in ("MIMO_MODEL", "MODEL_NAME", "ANTHROPIC_MODEL", "OPENAI_MODEL"):
        if os.environ.get(name):
            return os.environ[name]
    return None


def probe(do_ping: bool, timeout_ms: int, skip_ping: bool) -> dict[str, Any]:
    timeout_s = max(timeout_ms, 1000) / 1000.0
    candidates: list[dict[str, Any]] = []

    if skip_ping or not do_ping:
        for entry in KNOWN:
            key = _get_key(entry)
            fmt = entry.get("api_format", "openai")
            # --no-ping: 仅有 key 即视为候选；ping_ok 在此语义下表示「有凭证」
            item = {
                "id": entry["id"],
                "display": entry["display"],
                "base_url": entry["base_url"],
                "model": entry["model"],
                "api_key_env": entry["api_key_env"],
                "api_format": fmt,
                "ping_ok": bool(key),
                "ping_ms": None,
                "roles": ["worker", "judge"] if key else [],
                "note": None if key else f"missing key env: {entry['api_key_env']}",
            }
            candidates.append(item)
    else:
        with ThreadPoolExecutor(max_workers=6) as pool:
            futs = [pool.submit(ping_one, e, timeout_s) for e in KNOWN]
            for fut in as_completed(futs):
                candidates.append(fut.result())
        # stable order matching KNOWN
        order = {e["id"]: i for i, e in enumerate(KNOWN)}
        candidates.sort(key=lambda c: order.get(c["id"], 99))

    candidates.extend(scan_hints())
    return {"candidates": candidates, "host_model_hint": host_model_hint()}


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe model-fusion APIs")
    parser.add_argument("--json", action="store_true", help="JSON to stdout")
    parser.add_argument("--no-ping", action="store_true", help="Skip network ping")
    parser.add_argument("--ping", dest="do_ping", action="store_true", default=True)
    parser.add_argument("--ping-timeout-ms", type=int, default=4000)
    args = parser.parse_args()

    result = probe(do_ping=args.do_ping, timeout_ms=args.ping_timeout_ms, skip_ping=args.no_ping)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("model-fusion probe\n" + "=" * 40)
        for c in result["candidates"]:
            ping = "✓" if c["ping_ok"] else "✗"
            roles = ",".join(c["roles"]) or "-"
            note = f" | {c['note']}" if c.get("note") else ""
            print(f"- {c['id']} ({c['display']}) ping:{ping} roles:{roles}{note}")
        if result.get("host_model_hint"):
            print(f"\nhost_model_hint: {result['host_model_hint']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
