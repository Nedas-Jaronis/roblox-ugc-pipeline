"""Run manifest — one JSONL file per project tracking every generation/iteration.

Each row records: timestamp, provider, prompt, target, accessory category,
input image paths, output asset path, and validation summary if known.

The manifest lets the assistant (or a future UI) browse history, compare
iterations, and re-validate older runs.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class ManifestRow:
    run_id: str
    timestamp: str
    provider: str
    prompt: str
    target: str
    accessory_category: str | None
    image_inputs: list[str] = field(default_factory=list)
    asset_path: str | None = None
    parent_run_id: str | None = None
    notes: str | None = None
    validation: dict | None = None


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]


def runs_root(project_dir: Path) -> Path:
    p = project_dir / "runs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def manifest_path(project_dir: Path) -> Path:
    return project_dir / "runs" / "manifest.jsonl"


def append(project_dir: Path, row: ManifestRow) -> None:
    mp = manifest_path(project_dir)
    mp.parent.mkdir(parents=True, exist_ok=True)
    with mp.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(row)) + os.linesep)


def read_all(project_dir: Path) -> list[ManifestRow]:
    mp = manifest_path(project_dir)
    if not mp.exists():
        return []
    rows: list[ManifestRow] = []
    for line in mp.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        data = json.loads(line)
        rows.append(ManifestRow(**data))
    return rows
