#!/usr/bin/env python3
"""Write a fusion profile JSON (no MCP required)."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SAFE_PROFILE = re.compile(r"^[a-z0-9-]+$")


def fusion_home() -> Path:
    return Path(
        os.environ.get("MODEL_FUSION_HOME", Path.home() / ".config" / "model-fusion")
    )


def main() -> int:
    p = argparse.ArgumentParser(description="Save model-fusion profile")
    p.add_argument("profile", help="slug, e.g. default")
    p.add_argument(
        "--file",
        required=True,
        help="path to JSON config to copy (must match profiles/schema.json)",
    )
    args = p.parse_args()

    if not SAFE_PROFILE.match(args.profile):
        print(json.dumps({"ok": False, "error": "invalid profile slug"}))
        return 1

    src = Path(args.file)
    try:
        cfg = json.loads(src.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"bad json: {e}"}))
        return 1

    for key in ("version", "strategy", "judge", "workers", "temperature", "strategy_options"):
        if key not in cfg:
            print(json.dumps({"ok": False, "error": f"missing field: {key}"}))
            return 1
    if cfg.get("version") != 1:
        print(json.dumps({"ok": False, "error": "version must be 1"}))
        return 1
    if cfg.get("strategy") not in ("A", "B"):
        print(json.dumps({"ok": False, "error": "strategy must be A or B"}))
        return 1
    workers = cfg.get("workers") or []
    if not workers:
        print(json.dumps({"ok": False, "error": "workers empty"}))
        return 1
    for w in workers:
        samples = w.get("samples", 1)
        if not isinstance(samples, int) or not (1 <= samples <= 3):
            print(json.dumps({"ok": False, "error": f"bad samples: {samples}"}))
            return 1
        if not w.get("api_key_env") or not w.get("base_url") or not w.get("model"):
            print(
                json.dumps(
                    {"ok": False, "error": f"incomplete worker: {w.get('model_id')}"}
                )
            )
            return 1
        fmt = w.get("api_format", "openai")
        if fmt not in ("openai", "anthropic"):
            print(json.dumps({"ok": False, "error": f"bad api_format: {fmt}"}))
            return 1
    jfmt = (cfg.get("judge") or {}).get("api_format", "openai")
    if jfmt not in ("openai", "anthropic"):
        print(json.dumps({"ok": False, "error": f"bad judge api_format: {jfmt}"}))
        return 1

    now = datetime.now(timezone.utc).isoformat()
    cfg.setdefault("created_at", now)
    cfg["updated_at"] = now

    dest = fusion_home() / f"{args.profile}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "path": str(dest)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
