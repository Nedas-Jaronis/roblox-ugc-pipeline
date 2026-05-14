# robloxchars

Local pipeline to generate, validate, and prep 3D models for the Roblox
marketplace — full UGC avatar bundles and standalone accessories.

Free-first generation: built around **Roblox's own [cube3d](https://github.com/Roblox/cube)
foundation model** (text-to-3D) and the **InstantMesh / TripoSR HuggingFace
Spaces** (image-to-3D), with Sketchfab as a remix fallback. Driven through
the [BlenderMCP](https://github.com/ahujasid/blender-mcp) addon for in-Blender
operations.

## Setup

1. Install Blender 4.x: <https://www.blender.org/download/>
2. Install the BlenderMCP addon: <https://github.com/ahujasid/blender-mcp>
   - In Blender, enable the addon and click **Start MCP Server** in the sidebar.
3. Install this package:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -e ".[gen]"
   ```
4. Optional but recommended — log into HuggingFace for higher ZeroGPU quotas:
   ```powershell
   $env:HF_TOKEN = "hf_xxxxxxxxxxxxxxxxx"
   ```
5. Tell the CLI where Blender lives (only if not on PATH):
   ```powershell
   $env:ROBLOXCHARS_BLENDER = "C:\Program Files\Blender Foundation\Blender 4.2\blender.exe"
   ```

## Usage

### Text-to-3D (Roblox cube3d, primary path)

```powershell
robloxchars gen --provider cube3d --prompt "stylized cowboy hat" --category Hat
```

cube3d auto-constrains the output to the Hat bounding box (3×4×3 studs) so
the generated mesh starts marketplace-compliant.

### Image-to-3D (InstantMesh)

```powershell
robloxchars gen --provider instantmesh --image .\refs\hat.png --category Hat
```

### Validate any model

```powershell
robloxchars inspect runs/<run>/model.fbx --out runs/<run>/report.json
robloxchars validate runs/<run>/report.json --target accessory --category Hat
```

### Prep (decimate / center / rescale)

```powershell
robloxchars prep input.fbx --out cleaned.fbx --decimate 3500 --center --target-height 4.0
```

### Other commands

```powershell
robloxchars providers              # list provider table (free first)
robloxchars plan --provider cube3d --prompt "..." --target accessory --category Hat
robloxchars manifest list
```

### Sketchfab + interactive generation via Claude Code

For interactive workflows (Sketchfab remix, in-Blender editing, multi-step
iteration), open the repo in Claude Code and just ask:

> "Pull a low-poly cowboy hat from Sketchfab and prep it for Roblox as a Hat
> accessory."

The assistant drives BlenderMCP, runs `inspect`/`validate` on the result,
and proposes fixes for any findings.

## Repo layout

See [CLAUDE.md](./CLAUDE.md) for the architectural overview, official Roblox
spec numbers, and the assistant playbook.

## Status

| Capability | Status |
|---|---|
| Pure-python validators (polycount, bounds, rig, attachments, materials) | ✅ |
| Roblox spec values (from official creator-docs) | ✅ |
| Blender headless inspect / prep | ✅ (needs Blender to test) |
| Provider abstractions (cube3d, instantmesh, triposr, sketchfab, hyper3d, hunyuan3d) | ✅ |
| HF Space generation (`robloxchars gen`) | ✅ (endpoint shapes need live verification) |
| Manifest / run tracking | ✅ |
| Auto-remediation (decimate-on-fail, etc.) | ⛔ intentional — manual fixes |
| R15 auto-rigging from generated mesh | ⛔ next milestone |
| Texture baking pipeline (cube3d outputs are untextured) | ⛔ next milestone |
| Roblox Studio upload | ⛔ out of scope for v0 |
| LayeredClothing cage automation | ⛔ out of scope for v0 |
