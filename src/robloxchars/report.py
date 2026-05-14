"""Normalized mesh report produced by the Blender inspect script.

The inspect script runs inside `blender --background`, walks the scene, and
emits a JSON document matching `MeshReport`. All validators consume that JSON,
so they remain pure Python and easy to unit-test without Blender.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


Vec3 = tuple[float, float, float]


class AttachmentReport(BaseModel):
    name: str
    parent_bone: str | None = None
    position: Vec3 = (0.0, 0.0, 0.0)


class ArmatureReport(BaseModel):
    name: str
    bone_names: list[str]


class MaterialReport(BaseModel):
    name: str
    base_color_texture: str | None = None
    normal_texture: str | None = None
    metallic_roughness_texture: str | None = None


class ObjectReport(BaseModel):
    name: str
    triangle_count: int
    vertex_count: int
    materials: list[str] = Field(default_factory=list)
    parent: str | None = None
    bbox_min: Vec3
    bbox_max: Vec3


class MeshReport(BaseModel):
    """Output of `robloxchars inspect <path>` — the canonical input to validators."""

    source_path: str
    units: Literal["studs", "meters", "centimeters", "unknown"] = "unknown"
    # Combined bbox across all visible mesh objects.
    bbox_min: Vec3
    bbox_max: Vec3
    triangle_count: int
    vertex_count: int
    objects: list[ObjectReport] = Field(default_factory=list)
    armature: ArmatureReport | None = None
    attachments: list[AttachmentReport] = Field(default_factory=list)
    materials: list[MaterialReport] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    def extents(self) -> Vec3:
        return (
            self.bbox_max[0] - self.bbox_min[0],
            self.bbox_max[1] - self.bbox_min[1],
            self.bbox_max[2] - self.bbox_min[2],
        )


class Finding(BaseModel):
    """A single validator finding."""

    validator: str
    severity: Literal["error", "warn", "info"]
    message: str
    remediation: str | None = None


class ValidationResult(BaseModel):
    target: Literal["avatar", "accessory", "prop"]
    accessory_category: str | None = None
    findings: list[Finding] = Field(default_factory=list)

    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "warn"]

    def passed(self) -> bool:
        return not self.errors()
