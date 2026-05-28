# Contributing to robloxchars

Thanks for your interest! This is an open, free-tier project for generating and
prepping Roblox UGC content. Issues and PRs are welcome.

## Getting set up

```bash
git clone https://github.com/Nedas-Jaronis/roblox-ugc-pipeline.git
cd roblox-ugc-pipeline
python -m venv .venv && source .venv/bin/activate
pip install -e ".[gen]"
```

You'll also want **Blender 4.x** on your PATH (or set `ROBLOXCHARS_BLENDER`) for
any of the mesh-operation commands. The pure-Python validators run without it.

## Project layout

See [README.md](./README.md#architecture) and [CLAUDE.md](./CLAUDE.md). The
short version:

- `src/robloxchars/validators/` — **pure Python, never import `bpy`.** Runs
  anywhere, easy to test.
- `src/robloxchars/blender/` — **requires Blender**, only invoked via
  subprocess (`blender --background --python ...`). Don't import these from the
  CLI directly.
- `src/robloxchars/roblox_spec.py` — the single source of truth for Roblox spec
  numbers (tri budgets, bounds, bone names). If a value drifts, change it here.

## Conventions

- **No comments unless they explain a non-obvious *why*.** Match the style of
  the surrounding code.
- Keep validators free of any Blender dependency.
- Spec constants live only in `roblox_spec.py`.
- Never commit secrets. `.hf_token`, `.roblox_api_key`, and `.env` are
  gitignored — keep it that way. Generated assets under `runs/` are also
  gitignored.

## Good first contributions

- **Refresh HF Space endpoints** in `generate.py` when a Space's `app.py`
  changes its call signature (this is the most common breakage).
- **Multi-view texture blending** — reduce back-projection stretching in
  `project_paint.py`.
- **Normal + MetallicRoughness baking** — extend `bake.py` beyond BaseColor.
- **LayeredClothing cage automation** — `*_OuterCage` generation.
- **Tests** for the validators (they're pure Python — a great place to start).

## Submitting changes

1. Branch off `main`.
2. Make your change; keep it focused.
3. If you touched a validator, include a short before/after of the validator
   output in the PR description.
4. Open a PR against `main` with a clear description of what and why.

## Reporting issues

Include the command you ran, the full output, your OS, and your Blender version.
For generation failures, note which provider/Space and paste the error — Space
endpoints change, so that context helps a lot.
