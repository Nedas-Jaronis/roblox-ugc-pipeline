# roblox-ugc-pipeline

A **local-first, free-tier** pipeline for generating, validating, and preparing
3D content for the **Roblox UGC marketplace** — full R15 avatar bundles,
standalone accessories (hats, hair, back items), and face decals.

It takes a text prompt or an image all the way to a marketplace-shaped FBX:
generate the mesh, auto-rig it to the R15 skeleton, generate and bake textures,
stamp the required attachments, decimate to Roblox's tri budgets, validate
against the official spec, and (optionally) upload via Roblox Open Cloud.

Inspired by Bloxlab, Sloyd, and DashBlox — but built to run on your own machine
with free models and no per-asset cost.

> **Status: working prototype.** Every stage below has been run end to end and
> produced real assets (hats, rigged farmer/humanoid avatars, anime face decals,
> and baked textures). The rough edges are mostly that hosted model endpoints
> (HuggingFace Spaces) drift over time and occasionally need their call
> signatures refreshed. Contributions welcome — see
> [CONTRIBUTING.md](./CONTRIBUTING.md).

---

## What it does

```
                 ┌─────────────────────────────────────────────────────────┐
  text prompt ──▶│ cube3d (Roblox's text-to-3D foundation model)           │
  image       ──▶│ InstantMesh / TripoSR (image-to-3D)                     │──┐
  Sketchfab   ──▶│ CC0/CC-BY asset remix                                   │  │
                 └─────────────────────────────────────────────────────────┘  │
                                                                                ▼
   ┌──────────────────────────────────────────────────────────────────────────────┐
   │  Blender (headless) pipeline                                                   │
   │   • autorig   → fit mesh to R15, auto-weight, split into <Bone>_Geo pieces,    │
   │                 stamp *_Att attachments, per-group decimate to tri budgets     │
   │   • texture   → multi-view SD images → UV project → bake one 2048² BaseColor   │
   │   • face      → PNG → flat handle mesh + FaceCenterAttachment                  │
   │   • previews  → 4-view turntable renders for thumbnails                        │
   └──────────────────────────────────────────────────────────────────────────────┘
                                                                                ▼
   ┌──────────────────────────────────────────────────────────────────────────────┐
   │  Validation (pure Python, no Blender)                                          │
   │   polycount · bounds · R15 rig · attachments · materials/texture-size          │
   └──────────────────────────────────────────────────────────────────────────────┘
                                                                                ▼
                              rigged + validated FBX  ──▶  Roblox Open Cloud upload
```

### Capabilities

| Area | What's built |
|---|---|
| **Text → 3D** | `cube3d` (Roblox's own foundation model) via HF Space, bbox-constrained to the accessory category so output is born marketplace-sized. Full closed geometry. |
| **Image → 3D** | **TRELLIS** (best free quality, textured GLB) primary; **Hunyuan3D-2** for multi-view (front/back/left/right → full 360°); InstantMesh/TripoSR fallbacks. Inputs are auto square-padded + upscaled. All free HF Spaces. |
| **Mesh cleanup** | `clean` strips the paper-thin planes + needle artifacts single-view models hallucinate for unseen geometry (trimesh, bpy-free). |
| **Asset remix** | Sketchfab CC0/CC-BY pull + in-Blender edit (via BlenderMCP). |
| **Auto-rigging** | Arbitrary humanoid mesh → fitted to R15 proportions → heat-diffusion auto-weights → split into exact `<Bone>_Geo` meshes → `*_Att` attachments stamped at spec positions → per-group decimation to avatar tri budgets. |
| **Texturing** | Text → multi-view Stable Diffusion images → camera-projected UVs → baked into a single shared 2048² BaseColor PNG. Also bakes vertex-color / Sketchfab meshes into a clean Principled-BSDF texture. |
| **Face decals** | Face PNG → flat handle plane + `FaceCenterAttachment`, exported as a Roblox Face Accessory. |
| **Previews** | Headless 4-view turntable renders for marketplace thumbnails. |
| **Validation** | Pure-Python validators for polycount (group tri budgets), bounding box per category, R15 bone presence/naming, required attachments, and PBR materials + 2048² texture cap. |
| **Upload** | Roblox Open Cloud Assets API uploader (FBX/Decal/etc.) with async operation polling. |
| **Tracking** | Every generation logged to `runs/manifest.jsonl` with provider, prompt, and validation result. |

---

## Quick start

### 1. Prerequisites

- **Python 3.10+**
- **Blender 4.x** — <https://www.blender.org/download/> (needed for inspect / prep / autorig / bake / face / previews)
- **BlenderMCP addon** (optional, for interactive in-Blender editing) — <https://github.com/ahujasid/blender-mcp>

### 2. Install

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .\.venv\Scripts\Activate.ps1
pip install -e ".[gen]"
```

### 3. Configure (optional but recommended)

```bash
export HF_TOKEN="hf_xxx"                                   # higher ZeroGPU quotas
export ROBLOX_UGC_BLENDER="/path/to/blender"              # only if not on PATH
export ROBLOX_API_KEY="..."                                # only for `upload`
```

On Windows (PowerShell): `$env:HF_TOKEN = "hf_xxx"`, etc. You can also drop the
HF token in `.hf_token` and the Roblox key in `.roblox_api_key` — both are
gitignored.

---

## Usage

### Full pipeline: text → rigged avatar

```bash
roblox-ugc gen-and-rig --prompt "cartoon farmer in overalls" --out runs/farmer.fbx
```

Generates the mesh with cube3d, produces 4-view SD textures, auto-rigs to R15,
bakes the texture, and writes a marketplace-shaped FBX.

### Generate a mesh

```bash
# Text → 3D (cube3d, primary)
roblox-ugc gen --provider cube3d --prompt "stylized cowboy hat" --category Hat

# Image → 3D (TRELLIS — best free quality; --clean strips single-view artifacts)
roblox-ugc gen --provider trellis --image ref.png --category Hat --clean

# Image → 3D, multi-view for full 360° geometry (front, back, left, right)
roblox-ugc gen --provider hunyuan3d-space \
    --image front.png --image back.png --image left.png --image right.png

# Clean an existing mesh (strip hallucinated planes/needles)
roblox-ugc clean runs/<run>/model.glb --out clean.glb
```

### Auto-rig an existing humanoid mesh

```bash
roblox-ugc autorig character.glb --out rigged.fbx --height 5.0 \
    --texture-prompt "red hoodie, blue jeans"
```

### Auto-prep an accessory (orient / scale / center / decimate / attachments)

```bash
roblox-ugc autoprep raw_hat.glb --out hat.fbx --category Hat --bake
```

### Validate

```bash
roblox-ugc inspect rigged.fbx --out report.json
roblox-ugc validate report.json --target avatar
roblox-ugc validate report.json --target accessory --category Hat
```

### Upload to Roblox

```bash
roblox-ugc upload rigged.fbx --user-id <your-id> --asset-type Model --name "Farmer"
```

### Utility

```bash
roblox-ugc providers          # list generation backends (free first)
roblox-ugc plan --provider cube3d --prompt "..." --category Hat   # print the workflow
roblox-ugc manifest list      # show generation history
```

### Interactive (Claude Code + BlenderMCP)

For Sketchfab remixing and multi-step in-Blender iteration, open the repo in
Claude Code and just ask:

> "Pull a low-poly cowboy hat from Sketchfab and prep it for Roblox as a Hat."

The assistant drives BlenderMCP, runs `inspect`/`validate`, and proposes fixes.

The Colab notebooks in [`colab/`](./colab/) (`cube3d_generate.ipynb`,
`face_decals.ipynb`) run the cube3d and face-decal paths on free GPUs.

---

## Official Roblox spec (baked into `roblox_spec.py`)

Sourced from the official creator-docs. See [CLAUDE.md](./CLAUDE.md) for the
full table.

| Thing | Value |
|---|---|
| R15 total tri budget | 10,742 |
| Head_Geo budget | 4,000 |
| Rigid accessory tri cap | 4,000 |
| Texture max | 2048×2048 |
| Max bone influences / vertex | 4 |
| Hat bounds | 3×4×3 studs |
| Mesh naming | `<BoneName>_Geo` |

---

## Architecture

Two intentionally separated execution modes:

1. **Headless CLI (`roblox-ugc`)** — pure-Python validators + shell-out to
   `blender --background --python` for mesh ops. Batch/CI friendly.
2. **Live mode (assistant + BlenderMCP)** — interactive in-Blender editing.

The validators never import `bpy`, so they run anywhere. Anything under
`src/roblox_ugc_pipeline/blender/` requires Blender and is only invoked via subprocess.

```
src/roblox_ugc_pipeline/
  roblox_spec.py        # official spec values (single source of truth)
  report.py             # MeshReport / ValidationResult / Finding models
  validators/           # pure-python: polycount, bounds, rig, attachments, materials
  blender/              # run inside `blender --background`
    inspect.py            scene → MeshReport JSON
    prep.py / autoprep.py decimate / center / scale / attachments / export
    autorig.py            R15 fit + auto-weight + bone-split + decimate
    r15_armature.py       R15 template skeleton (exact bone names)
    bake.py               bake effective BaseColor → 2048² PNG
    project_paint.py      camera-project a 2D image onto a mesh, bake to UV
    face_accessory.py     face PNG → Roblox Face Accessory FBX
    render_previews.py    4-view turntable renders
  providers/            # generation backend descriptors (cube3d, instantmesh, ...)
  generate.py           # HF Space drivers (gradio_client)
  texture_gen.py        # text → multi-view SD images (host-side)
  uploader.py           # Roblox Open Cloud Assets upload + polling
  manifest.py           # runs/manifest.jsonl
  cli.py                # typer app
```

See [CLAUDE.md](./CLAUDE.md) for the deeper architectural overview and the
generation-provider priority order.

---

## Caveats

- **HF Space endpoints drift.** If a `gen` call fails, fetch the Space's
  `app.py` and update the candidate endpoints in `generate.py`.
- **ZeroGPU free quota** is ~300s/day for logged-in users — set `HF_TOKEN`.
- **cube3d emits no textures** — the bake step adds a 2048² BaseColor.
- **Single-view texture projection** stretches on surfaces hidden from the
  camera; use the multi-view path for 360° props.
- **License diligence** before marketplace resale: prefer CC0 Sketchfab assets;
  CC-BY needs attribution Roblox doesn't surface.

---

## Contributing

Issues and PRs welcome — see [CONTRIBUTING.md](./CONTRIBUTING.md). Good first
areas: refreshing HF Space endpoints, multi-view texture blending, normal/
metallic-roughness baking, and LayeredClothing cage automation.

## License

[MIT](./LICENSE) © 2026 Nedas Jaronis.

This project builds on third-party models and tools under their own licenses —
Roblox cube3d, InstantMesh, TripoSR, FLUX.1-schnell, BlenderMCP, and any
Sketchfab assets you pull. Check each before commercial/marketplace use.
