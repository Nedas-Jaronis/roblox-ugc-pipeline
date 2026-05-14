"""CLI entrypoint.

Two layers:
  * pure-python: validate, manifest, plan (no Blender required)
  * blender-shellout: inspect, prep, export (shells `blender --background`)

Set ROBLOXCHARS_BLENDER to override the Blender executable path.
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

ROOT = Path(os.environ.get("ROBLOXCHARS_PROJECT", os.getcwd()))


def _blender_exe() -> str:
    override = os.environ.get("ROBLOXCHARS_BLENDER")
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
        "Could not find Blender. Set ROBLOXCHARS_BLENDER=<path to blender.exe>."
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
