# CLAUDE.md

This file orients future assistant sessions to the repo. Read it first.

## What this is

A local pipeline for generating, validating, and preparing 3D models for the
Roblox marketplace — UGC avatar bundles and accessories. Inspired by Bloxlab,
Sloyd, and DashBlox, but local-first and free-tier-focused.

## Generation strategy (free-first)

Generation provider priority — try in this order:

1. **`cube3d` (Roblox/cube3d-v0.5)** — **PRIMARY for text-to-3D.** Roblox's own
   foundation model. Apache-style license. Free via HF Space (no GPU
   required) or self-hosted with a >=16GB VRAM CUDA GPU. We pre-fill the
   bounding box from the accessory category so the output already fits
   marketplace size caps. Outputs untextured `.obj`. Generates full closed
   geometry (no missing back) — prefer it when only a text prompt is given.
2. **`trellis` (trellis-community/TRELLIS)** — **PRIMARY for image-to-3D.**
   Best free single-image quality; outputs a *textured* GLB. Free on ZeroGPU.
   Driver: `/start_session` → `/preprocess_image` → `/generate_and_extract_glb`.
   Single-view backs are shallow + hallucinate flat planes — always run the
   `clean` step (see below).
3. **`hunyuan3d-space` (tencent/Hunyuan3D-2)** — image-to-3D, free HF Space,
   **multi-view capable.** Pass up to 4 images `[front, back, left, right]`
   via `/generation_all` for full 360° geometry — the biggest fix for the
   shallow-back problem. Single image also works.
4. **`instantmesh` / `triposr`** — lighter image-to-3D fallbacks when TRELLIS /
   Hunyuan are queued. InstantMesh pipeline: `/check_input_image` →
   `/preprocess` → `/generate_mvs` → `/make3d`.
5. **`sketchfab`** — asset-library remix path. Pull a CC0/CC-BY model,
   modify in Blender. Working via BlenderMCP today.
6. **`hyper3d`, `hunyuan3d`** (paid Tencent-Cloud/BlenderMCP route) — PAID.
   Do not propose unless user explicitly opts in (see memory
   `feedback-paid-3d-generators`). NOTE: `hunyuan3d-space` above is the FREE
   HF-Space variant and is distinct from this paid `hunyuan3d`.

Two quality levers (both implemented):
- **Preprocessing** (`generate._prepare_image`): square-pad on white + upscale.
  Low-res / off-center inputs are the #1 cause of bad reconstructions.
- **Cleanup** (`postprocess.clean_mesh`, CLI `roblox-ugc clean`): splits the
  mesh into connected components and drops the paper-thin planes + needle
  strands single-view models hallucinate. trimesh-based (needs `networkx`),
  bpy-free. Connected needles survive component-splitting — a known limit.

Image-to-3D path: image → `trellis` (or multi-view `hunyuan3d-space`) → `clean`
→ import to Blender → autoprep/autorig → validate. Text-to-3D path: prompt →
`cube3d` (bbox pre-constraint) → import → texture-bake → prep → validate.

Making our own: training a foundation model from scratch is infeasible
free/local. "Our own" = this multi-stage *pipeline* (preprocess → best free
recon → cleanup → Roblox-prep), and the multi-view route (synthesize/supply
back+side views → Hunyuan3D-2mv) is where the real fidelity gains are.

## Architecture

Two execution modes intentionally separated:

1. **Live mode (assistant + Blender MCP)** — assistant calls
   `mcp__blender__*` tools. Used for: importing generated meshes,
   in-Blender editing, exporting FBX, baking textures.
2. **Headless CLI (`roblox-ugc`)** — pure-python validators + shell-out to
   `blender --background --python` for mesh ops. Used for batch validation,
   CI checks, and the `roblox-ugc gen` HF-Space generation command.

## Repo layout

```
src/roblox_ugc_pipeline/
  roblox_spec.py        # OFFICIAL spec values from creator-docs (R15 bones,
                        # *_Geo mesh names, group tri budgets, accessory bounds,
                        # attachments, 2048 texture cap, max 4 bone influences)
  report.py             # MeshReport, ValidationResult, Finding
  validators/           # Pure-python validators
    polycount.py        # Group-level avatar tri budgets + accessory caps
    bounds.py           # Bbox limits per category
    rig.py              # R15 bone presence/naming, accessory rigid check
    attachments.py      # Required attachments (handles tuple-of-names case,
                        # checks Root_Att is at origin)
    materials.py        # PBR maps + 2048 texture-size cap (uses Pillow if avail)
    registry.py
  blender/              # Run inside `blender --background`
    inspect.py          # Walk scene -> MeshReport JSON (catches both *Attachment
                        # and *_Att naming conventions)
    prep.py             # Decimate / center / scale / re-export
  providers/            # Generation backend descriptors
    base.py             # GenerationRequest, Provider ABC
    cube3d.py           # Roblox/cube3d-v0.5 (text-to-3D, PRIMARY)
    instantmesh.py      # InstantMesh + TripoSR HF Spaces
    sketchfab.py        # Asset-library remix workflow
    hyper3d.py          # PAID
    hunyuan3d.py        # PAID
    registry.py
  generate.py           # HF Space drivers (gradio_client) — actual generation
                        # callable from the CLI
  manifest.py           # runs/manifest.jsonl
  cli.py                # typer app: gen, inspect, validate, prep, providers,
                        # plan, manifest
```

## Official Roblox numbers (now in roblox_spec.py)

| Thing | Value |
|---|---|
| R15 total tri budget | 10,742 |
| Head_Geo budget | 4,000 |
| Torso group budget | 1,750 (UpperTorso+LowerTorso combined) |
| Arm/Leg group budget | 1,248 each (3 meshes combined) |
| Rigid accessory tri cap | 4,000 |
| Texture max | 2048×2048 |
| Max bone influences/vertex | 4 |
| Hat bounds | 3×4×3 studs |
| Hair bounds | 3×5×3.5 studs |
| Face bounds | 3×2×2 studs |
| Back bounds | 10×7×4.5 studs |
| Mesh naming | `<BoneName>_Geo` (body), `<BoneName>_OuterCage` (LayeredClothing) |
| Attachment naming | `*Attachment` on accessories, `*_Att` on body |

## Iteration loop (the marketplace-prep workflow)

```
generate (cube3d/instantmesh) -> import to Blender via MCP -> inspect ->
validate -> fix findings in Blender via MCP -> prep (decimate/center) ->
export FBX -> add to manifest
```

Auto-fix is intentionally not wired up. Plan: "automated checks + manual
fixes" — only build remediation after seeing real validator output.

## When the user asks to generate

1. Confirm Blender + BlenderMCP addon is up (`mcp__blender__get_scene_info`).
2. Pick provider by modality:
   - Text → cube3d (text-only, Roblox-native, primary)
   - Image → instantmesh (default) or triposr (fast fallback)
3. For accessories, pre-fill bbox from `ACCESSORY_CATEGORIES[<cat>].max_bounds`
   so the model is born within marketplace size limits.
4. Run via `roblox-ugc gen --provider cube3d --prompt "..." --category Hat`
   OR drive it manually through gradio_client snippets when the CLI doesn't fit.
5. Import the asset into Blender via `mcp__blender__execute_blender_code` with
   `bpy.ops.wm.obj_import(filepath=...)` or `bpy.ops.import_scene.gltf(...)`.
6. Run `roblox-ugc inspect <fbx>` then `roblox-ugc validate ... --target ...`.
7. Surface findings; for each error, fix in Blender (MCP `execute_blender_code`).
8. After fixes, re-run inspect+validate. Append to manifest.

## Things to be cautious about

- **HF Space endpoints change.** `generate.py` has fallback candidates; if a
  call fails, fetch the Space's `app.py` (see `WebFetch`) and update the
  candidate list.
- **ZeroGPU quotas** are roughly ~300s/day for free logged-in users; expect
  rate limits. Recommend setting `HF_TOKEN` env var.
- **License diligence** before marketplace submission: Sketchfab CC-BY needs
  attribution that Roblox doesn't surface — prefer CC0 for resale.
- **Cube3D v0.5 emits no textures.** Pipeline must bake a 2048² BaseColor
  texture before submission, or rely on vertex colors only.
- **Units.** Roblox uses studs (~0.28m). Inspect detects units from Blender
  scene scale; if "unknown" appears in validators, ask the user.

## Conventions

- No comments in code unless explaining a non-obvious WHY.
- Validators import nothing from `bpy`.
- `src/roblox_ugc_pipeline/blender/` requires Blender and MUST NOT be imported by
  the CLI directly; only invoked via subprocess.
- Spec values in `roblox_spec.py` are sourced from the official creator-docs
  repo — when they drift, update there in one place.
