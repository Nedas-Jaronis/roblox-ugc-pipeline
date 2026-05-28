"""CLI entrypoint.

Two layers:
  * pure-python: validate, manifest, plan (no Blender required)
  * blender-shellout: inspect, prep, export (shells `blender --background`)

Set ROBLOX_UGC_BLENDER to override the Blender executable path.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from . import manifest as manifest_mod
from .providers import available_providers, get_provider
from .providers.base import GenerationRequest
from .report import MeshReport, ValidationResult
from .validators import run_all
from . import generate as generate_mod

app = typer.Typer(help="Roblox character / accessory generation + validation pipeline.")
console = Console()

ROOT = Path(os.environ.get("ROBLOX_UGC_PROJECT", os.getcwd()))


def _blender_exe() -> str:
    override = os.environ.get("ROBLOX_UGC_BLENDER")
    if override:
        return override
    found = shutil.which("blender")
    if found:
        return found
    # Common Windows install path.
    for guess in (
        r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 4.1\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 4.0\blender.exe",
    ):
        if Path(guess).exists():
            return guess
    raise typer.BadParameter(
        "Could not find Blender. Set ROBLOX_UGC_BLENDER=<path to blender.exe>."
    )


def _run_blender(script: str, extra: list[str]) -> None:
    script_path = Path(__file__).parent / "blender" / script
    cmd = [_blender_exe(), "--background", "--python", str(script_path), "--", *extra]
    console.print(f"[dim]$ {' '.join(cmd)}[/dim]")
    proc = subprocess.run(cmd, check=False)
    if proc.returncode != 0:
        raise typer.Exit(code=proc.returncode)


# ---- inspect ---------------------------------------------------------------

@app.command()
def inspect(
    source: Path = typer.Argument(..., exists=True, help="Input model (.blend/.fbx/.obj/.glb)"),
    out: Path = typer.Option(None, "--out", help="Where to write the report JSON"),
):
    """Run Blender headless to produce a MeshReport JSON for SOURCE."""
    out = out or source.with_suffix(".report.json")
    _run_blender("inspect.py", ["--in", str(source), "--out", str(out)])
    console.print(f"[green]Report written:[/green] {out}")


# ---- validate --------------------------------------------------------------

@app.command()
def validate(
    report_path: Path = typer.Argument(..., exists=True, help="MeshReport JSON from `inspect`"),
    target: str = typer.Option("accessory", "--target", help="avatar | accessory | prop"),
    category: Optional[str] = typer.Option(None, "--category", help="Accessory category (Hat, Hair, ...)"),
    json_out: bool = typer.Option(False, "--json", help="Emit raw JSON instead of a table"),
):
    """Validate a MeshReport against Roblox UGC rules."""
    data = json.loads(report_path.read_text(encoding="utf-8"))
    report = MeshReport.model_validate(data)
    result = run_all(report, target=target, accessory_category=category)
    if json_out:
        typer.echo(result.model_dump_json(indent=2))
        return
    _print_result(result)
    raise typer.Exit(code=0 if result.passed() else 1)


def _print_result(result: ValidationResult) -> None:
    table = Table(title=f"Validation: {result.target}" + (f" / {result.accessory_category}" if result.accessory_category else ""))
    table.add_column("Severity", style="bold")
    table.add_column("Validator")
    table.add_column("Message")
    table.add_column("Remediation", style="dim")
    sev_color = {"error": "red", "warn": "yellow", "info": "cyan"}
    for f in result.findings:
        table.add_row(
            f"[{sev_color.get(f.severity, 'white')}]{f.severity}[/]",
            f.validator,
            f.message,
            f.remediation or "",
        )
    if not result.findings:
        console.print("[green]No findings — ready for marketplace prep.[/green]")
    else:
        console.print(table)
        e = len(result.errors())
        w = len(result.warnings())
        status = "[red]FAIL[/red]" if e else "[yellow]PASS WITH WARNINGS[/yellow]"
        console.print(f"{status}  errors={e} warnings={w}")


# ---- prep ------------------------------------------------------------------

@app.command()
def prep(
    source: Path = typer.Argument(..., exists=True),
    out: Path = typer.Option(..., "--out"),
    decimate: int = typer.Option(0, "--decimate", help="Target total tris (0=skip)"),
    center: bool = typer.Option(False, "--center"),
    target_height: float = typer.Option(0.0, "--target-height", help="Scale to this Z extent"),
):
    """Decimate / center / rescale a model via Blender headless."""
    extra = ["--in", str(source), "--out", str(out)]
    if decimate:
        extra += ["--decimate", str(decimate)]
    if center:
        extra.append("--center")
    if target_height:
        extra += ["--target-height", str(target_height)]
    _run_blender("prep.py", extra)
    console.print(f"[green]Prep written:[/green] {out}")


@app.command()
def autoprep(
    source: Path = typer.Argument(..., exists=True, help="Input mesh"),
    out: Path = typer.Option(..., "--out", help="Output FBX path"),
    category: str = typer.Option(..., "--category", help="Hat / Hair / Face / ..."),
    bake: bool = typer.Option(False, "--bake", help="Bake BaseColor into a 2048^2 PNG"),
    bake_resolution: int = typer.Option(2048, "--bake-resolution"),
):
    """Auto-prep an accessory mesh for Roblox: orient, scale, center, decimate, stamp attachments, optionally bake."""
    extra = ["--in", str(source), "--out", str(out), "--category", category]
    if bake:
        extra += ["--bake", "--bake-resolution", str(bake_resolution)]
    _run_blender("autoprep.py", extra)
    console.print(f"[green]Auto-prepped:[/green] {out}")


@app.command()
def autorig(
    source: Path = typer.Argument(..., exists=True, help="Humanoid mesh (.glb/.fbx/.obj/.blend)"),
    out: Path = typer.Option(..., "--out", help="Output rigged FBX path"),
    height: float = typer.Option(5.0, "--height", help="Target avatar height in studs"),
    texture_prompt: Optional[str] = typer.Option(
        None, "--texture-prompt",
        help="Generate multi-view SD textures host-side from this prompt, then project + bake",
    ),
    texture_resolution: int = typer.Option(1024, "--texture-resolution"),
    no_decimate: bool = typer.Option(False, "--no-decimate"),
    join_prefix: Optional[str] = typer.Option(None, "--join-prefix"),
):
    """Auto-rig a humanoid mesh to R15 and optionally generate + bake textures."""
    import json as _json
    extra = [
        "--in", str(source),
        "--out", str(out),
        "--height", str(height),
        "--texture-resolution", str(texture_resolution),
    ]
    if no_decimate:
        extra.append("--no-decimate")
    if join_prefix:
        extra += ["--join-prefix", join_prefix]

    if texture_prompt:
        # Generate multi-view SD images host-side (Blender's bundled Python
        # doesn't have gradio_client). Then pass paths to Blender.
        from . import texture_gen
        tex_dir = out.parent / f"{out.stem}_textures"
        tex_dir.mkdir(parents=True, exist_ok=True)
        console.print(f"[cyan]Generating 4-view SD images for: {texture_prompt!r}[/cyan]")
        sources = texture_gen.generate_multi_view(texture_prompt, tex_dir,
                                                   width=768, height=1024)
        sources_json = _json.dumps({k: str(v) for k, v in sources.items()})
        extra += ["--multi-view-sources", sources_json,
                  "--texture-dir", str(tex_dir)]
        console.print(f"[green]SD views ready in:[/green] {tex_dir}")

    _run_blender("autorig_runner.py", extra)
    console.print(f"[green]Auto-rigged:[/green] {out}")


@app.command()
def upload(
    asset: Path = typer.Argument(..., exists=True, help="FBX/GLB/PNG/etc to upload"),
    user_id: Optional[str] = typer.Option(None, "--user-id", help="Roblox user ID (you must own the key with this user)"),
    group_id: Optional[str] = typer.Option(None, "--group-id"),
    asset_type: str = typer.Option("Model", "--asset-type", help="Model | Decal | Audio | Animation | Video"),
    display_name: str = typer.Option("", "--name", help="Display name (defaults to filename)"),
    description: str = typer.Option("", "--description"),
):
    """Upload an asset to Roblox via the Open Cloud Assets API.

    Auth: set ROBLOX_API_KEY env var or put a token in `./.roblox_api_key`
    (gitignored). The key must have asset:read + asset:write scopes for the
    target user / group.
    """
    from . import uploader
    if not user_id and not group_id:
        console.print("[red]ERROR:[/red] specify --user-id or --group-id")
        raise typer.Exit(2)
    console.print(f"[cyan]Uploading {asset.name} as {asset_type}…[/cyan]")
    result = uploader.upload_asset(
        asset,
        asset_type=asset_type,  # type: ignore[arg-type]
        display_name=display_name,
        description=description,
        creator_user_id=user_id,
        creator_group_id=group_id,
    )
    console.print(f"[green]Uploaded.[/green] asset_id={result.asset_id} op={result.operation_id}")


@app.command(name="gen-and-rig")
def gen_and_rig(
    prompt: str = typer.Option(..., "--prompt",
                                help="Text prompt for the character (drives BOTH cube3d and SD textures)"),
    out: Path = typer.Option(..., "--out", help="Output rigged FBX path"),
    height: float = typer.Option(5.0, "--height"),
    texture_resolution: int = typer.Option(1024, "--texture-resolution"),
):
    """Full pipeline: cube3d humanoid -> multi-view SD textures -> autorig -> rigged FBX.

    Single command from text prompt to validated marketplace-shape avatar.
    """
    from .providers.base import GenerationRequest
    from . import generate as gen_mod
    req = GenerationRequest(prompt=prompt, modality="text", target="avatar")
    console.print(f"[cyan]1/3 Generating mesh via cube3d for: {prompt!r}[/cyan]")
    gen_result = gen_mod.gen_cube3d(req, project_dir=ROOT)
    console.print(f"[green]   mesh:[/green] {gen_result.asset_path}")

    autorig(
        source=gen_result.asset_path,
        out=out,
        height=height,
        texture_prompt=prompt,
        texture_resolution=texture_resolution,
        no_decimate=False,
        join_prefix=None,
    )


# ---- providers / plan -----------------------------------------------------

@app.command(name="providers")
def providers_cmd():
    """List configured generation providers and their cost hints."""
    table = Table(title="Providers")
    table.add_column("Name", style="bold")
    table.add_column("Free?", justify="center")
    table.add_column("Cost")
    table.add_column("Notes")
    for name, p in available_providers().items():
        table.add_row(name, "yes" if p.free_tier else "no", p.cost_hint(), p.notes)
    console.print(table)


@app.command()
def gen(
    provider: str = typer.Option("cube3d", "--provider", help="cube3d | instantmesh"),
    prompt: str = typer.Option("", "--prompt"),
    image: list[Path] = typer.Option([], "--image", exists=True),
    target: str = typer.Option("accessory", "--target"),
    category: Optional[str] = typer.Option(None, "--category"),
):
    """Generate a model via a free HF Space.

    Currently supports cube3d (text) and instantmesh (image). The result is
    saved under runs/<timestamp>-<provider>/ with a meta.json sidecar.
    """
    modality = "image" if image else "text"
    req = GenerationRequest(
        prompt=prompt,
        modality=modality,  # type: ignore[arg-type]
        image_paths=list(image),
        target=target,  # type: ignore[arg-type]
        accessory_category=category,
    )
    result = generate_mod.generate(req, provider=provider, project_dir=ROOT)
    console.print(f"[green]Generated:[/green] {result.asset_path}")
    console.print(f"[dim]Run dir:[/dim] {result.run_dir}")


@app.command()
def plan(
    provider: str = typer.Option("hyper3d", "--provider"),
    prompt: str = typer.Option(..., "--prompt"),
    modality: str = typer.Option("text", "--modality", help="text | image | multi"),
    image: list[Path] = typer.Option([], "--image", help="Image input(s) for image/multi modes"),
    target: str = typer.Option("accessory", "--target"),
    category: Optional[str] = typer.Option(None, "--category"),
):
    """Print the step-by-step plan the assistant should run via Blender MCP.

    This command does NOT call MCP itself — it prints the workflow for the
    operator (or an assistant session) to execute.
    """
    p = get_provider(provider)
    req = GenerationRequest(
        prompt=prompt,
        modality=modality,  # type: ignore[arg-type]
        image_paths=list(image),
        target=target,  # type: ignore[arg-type]
        accessory_category=category,
    )
    for step in p.live_workflow(req):
        console.print(step)


# ---- manifest --------------------------------------------------------------

manifest_app = typer.Typer(help="Run manifest commands")
app.add_typer(manifest_app, name="manifest")


@manifest_app.command("add")
def manifest_add(
    provider: str = typer.Option(...),
    prompt: str = typer.Option(...),
    target: str = typer.Option(...),
    asset: Path = typer.Option(...),
    category: Optional[str] = typer.Option(None, "--category"),
    parent: Optional[str] = typer.Option(None, "--parent", help="Parent run id (for iterations)"),
    notes: Optional[str] = typer.Option(None, "--notes"),
):
    """Append a row to runs/manifest.jsonl."""
    from datetime import datetime, timezone
    row = manifest_mod.ManifestRow(
        run_id=manifest_mod.new_run_id(),
        timestamp=datetime.now(timezone.utc).isoformat(),
        provider=provider,
        prompt=prompt,
        target=target,
        accessory_category=category,
        asset_path=str(asset),
        parent_run_id=parent,
        notes=notes,
    )
    manifest_mod.append(ROOT, row)
    console.print(f"[green]Added[/green] run {row.run_id}")


@manifest_app.command("list")
def manifest_list():
    rows = manifest_mod.read_all(ROOT)
    if not rows:
        console.print("[dim]No manifest entries yet.[/dim]")
        return
    table = Table(title="Runs")
    for col in ("run_id", "timestamp", "provider", "target", "category", "asset", "notes"):
        table.add_column(col)
    for r in rows:
        table.add_row(
            r.run_id, r.timestamp, r.provider, r.target,
            r.accessory_category or "", r.asset_path or "", (r.notes or "")[:40],
        )
    console.print(table)


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
