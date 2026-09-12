#!/usr/bin/env python3
"""Run one model-fusion pass from a profile. No strategy CLI rewrites."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_TEMPS = (0.4, 0.7, 1.0)

JUDGE_SYSTEM = (
    "你是融合仲裁者。任务：取舍、判伪、组织。\n"
    "- 从候选中吸收正确、完整、与用户任务匹配的内容。\n"
    "- 发现明显事实错误或互相冲突时，做出取舍并用一两句说明理由。\n"
    "- 输出一份可直接使用的最终答复，结构清晰。\n"
    "- 不要复述全部候选，不要给模型打分排名，不要引入候选之外的新事实声称。\n"
    "- 不要区分或猜测候选来自哪个模型。"
)

SAFE_PROFILE = re.compile(r"^[a-z0-9-]+$")


def fusion_home() -> Path:
    return Path(os.environ.get("MODEL_FUSION_HOME", Path.home() / ".config" / "model-fusion"))


def load_profile(name: str) -> dict[str, Any]:
    if not SAFE_PROFILE.match(name):
        raise ValueError(f"invalid profile name: {name!r}")
    path = fusion_home() / f"{name}.json"
    if not path.is_file():
        raise FileNotFoundError(f"profile not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def temp_sequence(mode_cfg: dict[str, Any], n: int) -> list[float]:
    mode = mode_cfg.get("mode", "default")
    if mode == "fixed":
        t = float(mode_cfg.get("fixed", 0.7))
        return [t] * n
    if mode == "custom":
        seq = [float(x) for x in (mode_cfg.get("sequence") or DEFAULT_TEMPS)]
        if not seq:
            seq = list(DEFAULT_TEMPS)
        return [seq[i % len(seq)] for i in range(n)]
    return [DEFAULT_TEMPS[i % len(DEFAULT_TEMPS)] for i in range(n)]


def resolve_worker_endpoint(w: dict[str, Any]) -> tuple[str, str, str, str]:
    key = os.environ.get(w.get("api_key_env") or "")
    if not key:
        raise RuntimeError(f"missing key env: {w.get('api_key_env')}")
    base = (w.get("base_url") or "").rstrip("/")
    model = w.get("model") or ""
    if not base or not model:
        raise RuntimeError(f"incomplete worker endpoint: {w}")
    fmt = w.get("api_format") or "openai"
    if fmt not in ("openai", "anthropic"):
        raise RuntimeError(f"unsupported api_format: {fmt}")
    return key, base, model, fmt


def _split_system(messages: list[dict[str, str]]) -> tuple[str | None, list[dict[str, str]]]:
    """Extract system text; remaining messages keep user/assistant roles only."""
    system_parts: list[str] = []
    rest: list[dict[str, str]] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content") or ""
        if role == "system":
            system_parts.append(content)
        elif role in ("user", "assistant"):
            rest.append({"role": role, "content": content})
    system = "\n\n".join(p for p in system_parts if p) or None
    return system, rest


def complete_once(
    key: str,
    base: str,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    timeout_s: float,
    max_tokens: int = 2048,
    api_format: str = "openai",
) -> dict[str, Any]:
    started = time.monotonic()

    if api_format == "anthropic":
        url = f"{base}/messages"
        system, rest = _split_system(messages)
        if not rest:
            rest = [{"role": "user", "content": ""}]
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": rest,
        }
        if system:
            payload["system"] = system
        headers = {
            "Content-Type": "application/json",
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        }
    elif api_format == "openai":
        url = f"{base}/chat/completions"
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        }
    else:
        return {
            "ok": False,
            "content": None,
            "error": f"unsupported api_format: {api_format}",
            "latency_ms": 0,
        }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        content = _extract_content(raw, api_format)
        return {
            "ok": bool(content),
            "content": content,
            "error": None if content else "empty content",
            "latency_ms": int((time.monotonic() - started) * 1000),
        }
    except urllib.error.HTTPError as e:
        detail = e.read()[:400].decode("utf-8", "replace") if e.fp else ""
        return {
            "ok": False,
            "content": None,
            "error": f"HTTP {e.code}: {detail}",
            "latency_ms": int((time.monotonic() - started) * 1000),
        }
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "content": None,
            "error": f"{type(e).__name__}: {e}",
            "latency_ms": int((time.monotonic() - started) * 1000),
        }


def _extract_content(raw: Any, api_format: str) -> str:
    if not isinstance(raw, dict):
        return ""
    if api_format == "anthropic":
        blocks = raw.get("content") or []
        parts = []
        for b in blocks:
            if isinstance(b, dict) and b.get("type") == "text" and b.get("text"):
                parts.append(b["text"])
        return "".join(parts)
    choices = raw.get("choices") or []
    if not choices:
        return ""
    msg = choices[0].get("message") or {}
    content = msg.get("content") or ""
    if not content and isinstance(choices[0].get("text"), str):
        content = choices[0]["text"]
    return content or ""


def job_messages(system: str | None, task: str) -> list[dict[str, str]]:
    msgs: list[dict[str, str]] = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": task})
    return msgs


def build_judge_task(
    strategy: str,
    task: str,
    candidates: list[dict[str, Any]],
    workers: list[dict[str, Any]],
) -> str:
    ok = [c for c in candidates if c.get("ok") and c.get("content")]
    by_role = {"primary": [], "supplement": []}
    role_map = {w["model_id"]: w.get("role", "primary") for w in workers}
    for c in ok:
        role = role_map.get(c["model_id"], "primary")
        by_role[role if role in by_role else "primary"].append(c)

    def block(items: list[dict[str, Any]]) -> str:
        parts = []
        for i, c in enumerate(items, 1):
            parts.append(f"### Sample #{i}\n{c['content'].strip()}\n")
        return "\n".join(parts)

    if strategy == "B":
        header = (
            f"## 用户任务\n{task}\n\n"
            "## 主方案候选\n"
            f"{block(by_role['primary']) or '(无)'}\n\n"
            "## 补充视角（旁证/反例，不决定主线结构）\n"
            f"{block(by_role['supplement']) or '(无)'}\n\n"
            "请按系统说明完成取舍、判伪与组织，输出唯一最终答复。"
        )
        return header

    # A — equal pool
    flat = by_role["primary"] + by_role["supplement"]
    return (
        f"## 用户任务\n{task}\n\n"
        f"## 全部候选\n{block(flat)}\n"
        "请按系统说明完成取舍、判伪与组织，输出唯一最终答复。"
    )


def write_run(
    profile_name: str,
    payload: dict[str, Any],
) -> Path:
    runs = fusion_home() / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = runs / f"{ts}-{profile_name}"
    json_path = base.with_suffix(".json")
    md_path = base.with_suffix(".md")
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    lines = [
        f"# fusion run {ts}",
        f"profile: {profile_name}",
        f"strategy: {payload.get('strategy')}",
        f"judge: {payload.get('judge_model')}",
        "",
        "## workers",
    ]
    for w in payload.get("workers_used") or []:
        lines.append(f"- {w['model_id']}×{w['n']} ok={w['ok']}")
    lines += ["", "## final", payload.get("final") or "(empty)", ""]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path


def run_fusion(
    profile_name: str,
    task: str,
    system: str | None,
    max_workers: int,
) -> dict[str, Any]:
    try:
        cfg = load_profile(profile_name)
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "error": str(e),
            "profile": profile_name,
            "strategy": "A",
            "judge_model": "",
            "workers_used": [],
            "candidates": [],
            "final": None,
            "degraded": False,
            "run_path": None,
        }

    strategy = cfg.get("strategy", "A")
    workers = cfg.get("workers") or []
    judge = cfg.get("judge") or {}
    opts = cfg.get("strategy_options") or {}
    timeout_s = float(opts.get("timeout_s", 90))
    min_success = int(opts.get("min_success", 2))
    on_fail = opts.get("on_partial_failure", "continue_with_available")
    temps_cfg = cfg.get("temperature") or {"mode": "default"}
    max_tokens = int(opts.get("max_tokens", 2048))

    if not workers:
        return {
            "ok": False,
            "error": "profile has no workers",
            "profile": profile_name,
            "strategy": strategy,
            "judge_model": judge.get("model_id") or "",
            "workers_used": [],
            "candidates": [],
            "final": None,
            "degraded": False,
            "run_path": None,
        }

    # expand samples
    jobs: list[dict[str, Any]] = []
    for w in workers:
        n = int(w.get("samples", 1))
        if n < 1:
            n = 1
        if n > 3:
            n = 3
        temps = temp_sequence(temps_cfg, n)
        for i in range(n):
            jobs.append({"worker": w, "sample_index": i, "temperature": temps[i]})

    candidates: list[dict[str, Any]] = []
    workers_used = []

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = []
        for j in jobs:
            w = j["worker"]
            futs.append(
                (
                    j,
                    pool.submit(
                        _run_one,
                        w,
                        job_messages(system, task),
                        j["temperature"],
                        j["sample_index"],
                        timeout_s,
                        max_tokens,
                    ),
                )
            )
        for j, fut in futs:
            result = fut.result()
            result["model_id"] = j["worker"]["model_id"]
            candidates.append(result)

    # per-worker counts
    for w in workers:
        mid = w["model_id"]
        n = sum(1 for c in candidates if c["model_id"] == mid)
        ok = sum(1 for c in candidates if c["model_id"] == mid and c.get("ok"))
        workers_used.append({"model_id": mid, "n": n, "ok": ok})

    success = [c for c in candidates if c.get("ok")]
    base = {
        "profile": profile_name,
        "strategy": strategy,
        "judge_model": judge.get("model_id") or judge.get("model") or "",
        "workers_used": workers_used,
        "candidates": candidates,
        "final": None,
        "degraded": False,
        "error": None,
        "run_path": None,
    }

    if not success:
        base["ok"] = False
        base["error"] = "no successful worker candidates"
        run_path = write_run(profile_name, base)
        base["run_path"] = str(run_path)
        return base

    if len(success) < min_success and on_fail == "abort":
        base["ok"] = False
        base["error"] = f"only {len(success)} ok < min_success {min_success} (abort)"
        base["degraded"] = True
        run_path = write_run(profile_name, base)
        base["run_path"] = str(run_path)
        return base

    # SPEC step 6: 单路成功 → 直接用该候选作 final，不调 judge
    if len(success) == 1:
        base["ok"] = True
        base["degraded"] = True
        base["final"] = success[0]["content"]
        base["note"] = "single_success_skipped_judge"
        run_path = write_run(profile_name, base)
        base["run_path"] = str(run_path)
        return base

    if len(success) < min_success:
        base["degraded"] = True

    # judge endpoint
    j_key_env = judge.get("api_key_env")
    j_base = judge.get("base_url")
    j_model = judge.get("model")
    j_fmt = judge.get("api_format") or "openai"
    if not (j_key_env and j_base and j_model):
        base["ok"] = False
        base["error"] = (
            "judge missing api_key_env/base_url/model in profile "
            f"(model_id={judge.get('model_id')})"
        )
        run_path = write_run(profile_name, {**base, "ok": False})
        base["run_path"] = str(run_path)
        return base

    j_key = os.environ.get(j_key_env)
    if not j_key:
        base["ok"] = False
        base["error"] = f"missing judge key env: {j_key_env}"
        run_path = write_run(profile_name, {**base, "ok": False})
        base["run_path"] = str(run_path)
        return base

    judge_task = build_judge_task(strategy, task, candidates, workers)
    judge_msgs = [
        {"role": "system", "content": JUDGE_SYSTEM},
        {"role": "user", "content": judge_task},
    ]
    jres = complete_once(
        j_key,
        j_base.rstrip("/"),
        j_model,
        judge_msgs,
        0.2,
        timeout_s,
        max_tokens=max_tokens,
        api_format=j_fmt,
    )
    base["judge_latency_ms"] = jres.get("latency_ms")
    if not jres.get("ok"):
        base["ok"] = False
        base["error"] = f"judge failed: {jres.get('error')}"
        run_path = write_run(profile_name, {**base, "ok": False})
        base["run_path"] = str(run_path)
        return base

    base["ok"] = True
    base["final"] = jres.get("content")
    run_path = write_run(profile_name, {**base, "ok": True})
    base["run_path"] = str(run_path)
    return base


def _run_one(
    worker: dict[str, Any],
    messages: list[dict[str, str]],
    temperature: float,
    sample_index: int,
    timeout_s: float,
    max_tokens: int = 2048,
) -> dict[str, Any]:
    entry = {
        "id": f"{worker.get('model_id')}#{sample_index}",
        "model_id": worker.get("model_id"),
        "sample_index": sample_index,
        "temperature": temperature,
        "api_format": worker.get("api_format") or "openai",
        "content": None,
        "error": None,
        "ok": False,
    }
    try:
        key, base, model, fmt = resolve_worker_endpoint(worker)
    except Exception as e:  # noqa: BLE001
        entry["error"] = str(e)
        return entry

    res = complete_once(
        key,
        base,
        model,
        messages,
        temperature,
        timeout_s,
        max_tokens=max_tokens,
        api_format=fmt,
    )
    entry["ok"] = res["ok"]
    entry["content"] = res.get("content")
    entry["error"] = res.get("error")
    entry["latency_ms"] = res.get("latency_ms")
    return entry


def main() -> int:
    parser = argparse.ArgumentParser(description="model-fusion fuse runner")
    parser.add_argument("--profile", default="default")
    parser.add_argument("--task", default="")
    parser.add_argument("--task-file", default="")
    parser.add_argument("--system", default="")
    parser.add_argument("--max-workers", type=int, default=4)
    args = parser.parse_args()

    task = args.task
    if args.task_file:
        task = Path(args.task_file).read_text(encoding="utf-8")
    if not task.strip():
        print(json.dumps({"ok": False, "error": "empty task"}, ensure_ascii=False))
        return 1

    system = args.system or None
    result = run_fusion(args.profile, task, system, max_workers=max(1, args.max_workers))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
