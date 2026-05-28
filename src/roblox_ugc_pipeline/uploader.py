"""Roblox Open Cloud Assets uploader.

Uploads an FBX (or other accepted file) to a creator's inventory via the
public Open Cloud `POST /assets/v1/assets` endpoint, then polls the async
operation until done.

Caveat (per Roblox docs as of 2026): the Open Cloud API accepts `assetType`
values like `Model`, `Decal`, `Audio`, `Animation`. **There is no `Hat` or
`Hair` `assetType` for uploads** — accessories upload as `Model` (FBX) and
must then be converted to a marketplace UGC accessory via Studio's Avatar
Setup (or a follow-up `PATCH` with accessory-specific data). This module
covers the upload + polling; the marketplace-publish step is out of scope.

Auth: pass `--api-key` or set `ROBLOX_API_KEY` env or put a token in
`./.roblox_api_key`. The API key needs the `asset:read` and `asset:write`
scopes for the target user or group.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


ASSETS_ENDPOINT = "https://apis.roblox.com/assets/v1/assets"
OPERATIONS_BASE = "https://apis.roblox.com"

AssetType = Literal["Model", "Decal", "Audio", "Animation", "Video"]
SUFFIX_TO_MIME: dict[str, str] = {
    ".fbx":  "model/fbx",
    ".gltf": "model/gltf+json",
    ".glb":  "model/gltf-binary",
    ".rbxm": "model/x-rbxm",
    ".rbxmx": "model/x-rbxmx",
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".bmp":  "image/bmp",
    ".tga":  "image/x-targa",
    ".mp3":  "audio/mpeg",
    ".ogg":  "audio/ogg",
    ".wav":  "audio/wav",
    ".flac": "audio/flac",
}


def _api_key() -> str | None:
    """Resolve API key from env vars or local files (gitignored)."""
    for var in ("ROBLOX_API_KEY", "ROBLOX_OPEN_CLOUD_KEY"):
        v = os.environ.get(var)
        if v:
            return v.strip()
    for path in (
        Path.cwd() / ".roblox_api_key",
        Path(__file__).resolve().parent.parent / ".roblox_api_key",
        Path.home() / ".roblox_api_key",
    ):
        if path.exists():
            text = path.read_text(encoding="utf-8").strip()
            if text:
                return text
    return None


@dataclass
class UploadResult:
    operation_id: str
    asset_id: str | None
    response: dict
    file: str


def upload_asset(
    file_path: Path,
    *,
    asset_type: AssetType = "Model",
    display_name: str = "",
    description: str = "",
    creator_user_id: str | None = None,
    creator_group_id: str | None = None,
    api_key: str | None = None,
    poll_interval_s: float = 2.0,
    poll_timeout_s: float = 180.0,
    expected_price_robux: int = 0,
) -> UploadResult:
    """Upload `file_path` as an Open Cloud asset and poll until done.

    Either `creator_user_id` or `creator_group_id` MUST be set — the API
    requires one or the other.

    Returns an `UploadResult` with the final asset_id (or raises on failure).
    """
    try:
        import requests  # standard host-side dep; not in Blender's bundled python
    except ImportError as e:
        raise RuntimeError(
            "requests not installed. Run: pip install requests"
        ) from e

    file_path = Path(file_path).resolve()
    if not file_path.exists():
        raise FileNotFoundError(file_path)
    if creator_user_id is None and creator_group_id is None:
        raise ValueError("must set creator_user_id or creator_group_id")
    if creator_user_id and creator_group_id:
        raise ValueError("set ONLY ONE of creator_user_id / creator_group_id")

    key = api_key or _api_key()
    if not key:
        raise RuntimeError(
            "No Roblox API key. Set ROBLOX_API_KEY env or put a token in .roblox_api_key."
        )

    mime = SUFFIX_TO_MIME.get(file_path.suffix.lower(), "application/octet-stream")
    creator: dict[str, str] = {}
    if creator_user_id:
        creator["userId"] = str(creator_user_id)
    if creator_group_id:
        creator["groupId"] = str(creator_group_id)

    meta = {
        "assetType": asset_type,
        "displayName": display_name or file_path.stem,
        "description": description,
        "creationContext": {
            "creator": creator,
            "expectedPrice": expected_price_robux,
        },
    }

    with open(file_path, "rb") as f:
        resp = requests.post(
            ASSETS_ENDPOINT,
            headers={"x-api-key": key},
            data={"request": json.dumps(meta)},
            files={"fileContent": (file_path.name, f, mime)},
            timeout=60,
        )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"Upload failed {resp.status_code}: {resp.text[:500]}"
        )
    initial = resp.json()
    op_path = initial.get("path")  # "operations/<id>"
    if not op_path:
        raise RuntimeError(f"No operation path in response: {initial}")

    # Poll until done.
    deadline = time.time() + poll_timeout_s
    while time.time() < deadline:
        time.sleep(poll_interval_s)
        r = requests.get(
            f"{OPERATIONS_BASE}/{op_path}",
            headers={"x-api-key": key},
            timeout=30,
        )
        if r.status_code >= 400:
            raise RuntimeError(f"Operation poll failed {r.status_code}: {r.text[:300]}")
        data = r.json()
        if data.get("done"):
            if "error" in data and data["error"]:
                raise RuntimeError(f"Upload failed: {data['error']}")
            response = data.get("response") or {}
            asset_id = response.get("assetId") or response.get("id")
            return UploadResult(
                operation_id=op_path.split("/")[-1],
                asset_id=str(asset_id) if asset_id else None,
                response=response,
                file=str(file_path),
            )
    raise TimeoutError(f"Upload didn't finish in {poll_timeout_s}s")
