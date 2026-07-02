"""Control-model regression: the validator suite is calibrated against two
fixtures and must keep reading them the same way.

  POSITIVE control — Roblox's own RoundMale template body
    (runs/templates/roundmale_report.json): a Marketplace-valid body by
    definition. ANY error on it is a bug in our validators.

  NEGATIVE control — the rigged cherry mascot
    (runs/loop/cherry-flip/report.json): a body that can never pass (sphere
    torso, fused stub arms). It must keep producing errors from the expected
    families; if it suddenly reads clean, a check went blind.

Run:  .venv/bin/python scripts/regression.py
Re-generate the reports first if inspect.py changed measurements:
  blender --background --python src/roblox_ugc_pipeline/blender/inspect.py -- \
      --in <model> --out <report.json>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from roblox_ugc_pipeline.report import MeshReport  # noqa: E402
from roblox_ugc_pipeline.validators import registry  # noqa: E402

POSITIVE = REPO / "runs/templates/roundmale_report.json"
NEGATIVE = REPO / "runs/loop/cherry-flip/report.json"

# Error families the negative control must keep firing (families, not exact
# messages — inputs get re-rigged and details shift, but a sphere body must
# always be scale-infeasible with out-of-box joints).
NEGATIVE_EXPECTED_FAMILIES = {"scale.feasibility", "attachments.bounds"}


def _run(path: Path):
    rep = MeshReport(**json.loads(path.read_text()))
    return registry.run_all(rep, "avatar")


def main() -> int:
    failures: list[str] = []

    if POSITIVE.exists():
        res = _run(POSITIVE)
        errs = res.errors()
        if errs:
            failures.append(
                f"POSITIVE control regressed: RoundMale template reads {len(errs)} errors "
                f"(must be 0). First: [{errs[0].validator}] {errs[0].message[:120]}"
            )
        else:
            print(f"ok  positive control: 0 errors, {len(res.warnings())} warnings")
    else:
        failures.append(f"positive control report missing: {POSITIVE}")

    if NEGATIVE.exists():
        res = _run(NEGATIVE)
        fired = {f.validator.split(".")[0] + "." + f.validator.split(".")[1]
                 if f.validator.count(".") >= 1 else f.validator
                 for f in res.errors()}
        missing = {fam for fam in NEGATIVE_EXPECTED_FAMILIES
                   if not any(v.startswith(fam) for v in fired)}
        if missing:
            failures.append(
                f"NEGATIVE control went blind: cherry no longer fires {sorted(missing)} "
                f"(fired: {sorted(fired)})"
            )
        else:
            print(f"ok  negative control: {len(res.errors())} errors across {sorted(fired)}")
    else:
        failures.append(f"negative control report missing: {NEGATIVE}")

    if failures:
        print()
        for f in failures:
            print("FAIL", f)
        return 1
    print("\nregression: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
