# CLAUDE.md

This file orients future assistant sessions to the repo. Read it first.

## What this is

A local pipeline for generating, validating, and preparing 3D models for the
Roblox marketplace — UGC avatar bundles and accessories. Inspired by Bloxlab,
Sloyd, and DashBlox, but local-first and free-tier-focused.

## Generation strategy (free-first)

Generation provider priority — try in this order:

1. **`cube3d` (Roblox/cube3d-v0.5)** — **PRIMARY** for text-to-3D. Roblox's own
   foundation model. Apache-style license. Free via HF Space (no GPU
   required) or self-hosted with a >=16GB VRAM CUDA GPU. We pre-fill the
   bounding box from the accessory category so the output already fits
   marketplace size caps. Outputs untextured `.obj`.
2. **`instantmesh` (TencentARC/InstantMesh)** — image-to-3D via HF Space.
   Free on ZeroGPU, rate-limited. Outputs GLB with vertex colors. Apply
   axis fix (`vertices[:, [1, 2, 0]]`) for Roblox coordinate system.
3. **`triposr` (Stability/Tripo)** — lighter image-to-3D fallback when
   InstantMesh is queued/rate-limited. Lower quality but fast.
4. **`sketchfab`** — asset-library remix path. Pull a CC0/CC-BY model,
   modify in Blender. Working via BlenderMCP today.
5. **`hyper3d`, `hunyuan3d`** — PAID. Do not propose unless user
   explicitly opts in (see memory `feedback-paid-3d-generators`).

Image-to-3D path: input image → `instantmesh` or `triposr` → import to
Blender → prep → validate. Text-to-3D path: prompt → `cube3d` (with bbox
pre-constraint from accessory category) → import → texture-bake → prep →
validate.

## Architecture

Two execution modes intentionally separated:

1. **Live mode (assistant + Blender MCP)** — assistant calls
   `mcp__blender__*` tools. Used for: importing generated meshes,
   in-Blender editing, exporting FBX, baking textures.
2. **Headless CLI (`robloxchars`)** — pure-python validators + shell-out to
   `blender --background --python` for mesh ops. Used for batch validation,
   CI checks, and the `robloxchars gen` HF-Space generation command.

## Repo layout

```
src/robloxchars/
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
4. Run via `robloxchars gen --provider cube3d --prompt "..." --category Hat`
   OR drive it manually through gradio_client snippets when the CLI doesn't fit.
5. Import the asset into Blender via `mcp__blender__execute_blender_code` with
   `bpy.ops.wm.obj_import(filepath=...)` or `bpy.ops.import_scene.gltf(...)`.
6. Run `robloxchars inspect <fbx>` then `robloxchars validate ... --target ...`.
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
- `src/robloxchars/blender/` requires Blender and MUST NOT be imported by
  the CLI directly; only invoked via subprocess.
- Spec values in `roblox_spec.py` are sourced from the official creator-docs
  repo — when they drift, update there in one place.
