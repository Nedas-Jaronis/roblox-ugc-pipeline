#!/usr/bin/env bash
# Full local marketplace-prep loop: autorig -> inspect -> validate.
#
#   scripts/full_loop.sh <mesh.(glb|fbx|obj|blend)> [out_dir]
#
# Runs from WSL against the Windows Blender install (override with
# ROBLOX_UGC_BLENDER). Writes <out_dir>/{rigged.fbx, rigged.log.json,
# report.json} and prints the validator findings.
set -euo pipefail

SRC=${1:?usage: full_loop.sh <mesh> [out_dir]}
OUT_DIR=${2:-runs/loop/$(basename "${SRC%.*}")}
REPO_DIR=$(cd "$(dirname "$0")/.." && pwd)
BL=${ROBLOX_UGC_BLENDER:-"/mnt/c/Program Files/Blender Foundation/Blender 5.0/blender.exe"}

mkdir -p "$OUT_DIR"

to_win() { wslpath -w "$1" 2>/dev/null || echo "$1"; }

RUNNER=$(to_win "$REPO_DIR/src/roblox_ugc_pipeline/blender/autorig_runner.py")
INSPECT=$(to_win "$REPO_DIR/src/roblox_ugc_pipeline/blender/inspect.py")
FBX_WSL="$OUT_DIR/rigged.fbx"
REPORT_WSL="$OUT_DIR/report.json"

echo "== autorig: $SRC"
"$BL" --background --python "$RUNNER" -- \
    --in "$(to_win "$SRC")" --out "$(to_win "$OUT_DIR")\\rigged.fbx" \
    2>&1 | grep -E "re-bucketed|\[autorig" || true
test -f "$FBX_WSL" || { echo "autorig failed: no $FBX_WSL"; exit 1; }

echo "== inspect"
"$BL" --background --python "$INSPECT" -- \
    --in "$(to_win "$FBX_WSL")" --out "$(to_win "$OUT_DIR")\\report.json" \
    2>&1 | grep -E "\[inspect" || true
test -f "$REPORT_WSL" || { echo "inspect failed: no $REPORT_WSL"; exit 1; }

echo "== validate"
"$REPO_DIR/.venv/bin/python" - "$REPORT_WSL" <<'EOF'
import json, sys
from roblox_ugc_pipeline.report import MeshReport
from roblox_ugc_pipeline.validators import registry
rep = MeshReport(**json.load(open(sys.argv[1])))
res = registry.run_all(rep, "avatar")
errs, warns = res.errors(), res.warnings()
print(f"\n{len(res.findings)} findings: {len(errs)} errors, {len(warns)} warnings\n")
for f in errs:
    print(f"  E {f.validator} :: {f.message}")
for f in warns:
    print(f"  W {f.validator} :: {f.message}")
for f in res.findings:
    if f.severity == "info":
        print(f"  I {f.validator} :: {f.message}")
sys.exit(1 if errs else 0)
EOF
