#!/usr/bin/env bash
# Prepare runtime dirs and optionally copy the skill into an agent skill root.
# Does not touch ~/.claude/skills unless you pass --skill-root.
set -euo pipefail

PKG="$(cd "$(dirname "$0")/.." && pwd)"
CFG="${MODEL_FUSION_HOME:-$HOME/.config/model-fusion}"
SKILL_ROOT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skill-root)
      SKILL_ROOT="${2:-}"
      shift 2 || true
      ;;
    *)
      echo "usage: $0 [--skill-root /path/to/agent/skills/model-fusion]" >&2
      exit 2
      ;;
  esac
done

mkdir -p "$CFG/runs"

echo "Package:     $PKG"
echo "Runtime dir: $CFG"

if [[ -n "$SKILL_ROOT" ]]; then
  mkdir -p "$SKILL_ROOT"
  cp "$PKG/SKILL.md" "$SKILL_ROOT/SKILL.md"
  mkdir -p "$SKILL_ROOT/scripts" "$SKILL_ROOT/profiles"
  cp "$PKG/scripts/"*.py "$PKG/scripts/"*.md "$SKILL_ROOT/scripts/"
  cp "$PKG/profiles/schema.json" "$SKILL_ROOT/profiles/schema.json"
  if [[ -d "$PKG/examples" ]]; then
    mkdir -p "$SKILL_ROOT/examples"
    cp "$PKG/examples/"*.json "$SKILL_ROOT/examples/" 2>/dev/null || true
  fi
  echo "Skill copied to: $SKILL_ROOT"
else
  echo "Skill not copied. Use e.g.:"
  echo "  $0 --skill-root \$HOME/.claude/skills/model-fusion"
fi

echo "Done."
