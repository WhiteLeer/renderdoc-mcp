"""RenderDoc adapter backed by the historical MCP implementation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import shutil
import tempfile
import time
from collections import Counter
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional

from ...core.models import AnalysisSummary, ShaderCatalogResult, ShaderTranspileResult
from .._legacy_loader import load_legacy_renderdoc_server


_STAGE_TO_SPIRV_CROSS = {
    "vs": "vert",
    "hs": "tesc",
    "ds": "tese",
    "gs": "geom",
    "ps": "frag",
    "cs": "comp",
}

_SPIRV_ID_TOKEN = re.compile(r"%[A-Za-z_][A-Za-z0-9_\.]*|%\d+")


def _safe_name(value: object) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", text)
    text = text.strip("._")
    return text or "item"


def _timestamp_token() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _make_run_output_dir(base_dir: Path, category: str, source_name: object) -> Path:
    root = base_dir / category
    root.mkdir(parents=True, exist_ok=True)
    slug = f"{_timestamp_token()}__{_safe_name(source_name)}"
    candidate = root / slug
    if not candidate.exists():
        return candidate
    suffix = 2
    while True:
        alt = root / f"{slug}_{suffix:02d}"
        if not alt.exists():
            return alt
        suffix += 1


class RenderDocService:
    """Owns RDC open/analyze/export entrypoints."""

    @property
    def _legacy(self):
        return load_legacy_renderdoc_server()

    def resolve_renderdoc_paths(self, renderdoc_dir: Optional[str] = None) -> tuple[Path, Path]:
        cmd, gui = self._legacy._resolve_renderdoc_paths(renderdoc_dir)
        return Path(cmd), Path(gui)

    def default_analysis_save_root(self) -> Path:
        return Path(self._legacy._default_analysis_save_root())

    def open_rdc(self, rdc_path: Path) -> None:
        if not rdc_path.exists():
            raise FileNotFoundError(str(rdc_path))
        _renderdoccmd, qrenderdoc = self.resolve_renderdoc_paths()
        if not qrenderdoc.exists():
            raise FileNotFoundError(str(qrenderdoc))
        subprocess.Popen([str(qrenderdoc), str(rdc_path)], cwd=str(qrenderdoc.parent), env=self._clean_qrenderdoc_env())

    def analyze_rdc(
        self,
        rdc_path: Path,
        *,
        top_n: int = 12,
        save_root_dir: Optional[Path] = None,
        open_report: bool = False,
        renderdoc_dir: Optional[str] = None,
    ) -> AnalysisSummary:
        if not rdc_path.exists():
            raise FileNotFoundError(str(rdc_path))
        _renderdoccmd, qrenderdoc = self.resolve_renderdoc_paths(renderdoc_dir)
        payload: Dict[str, Any] = self._legacy._analyze_rdc_with_qrenderdoc(
            qrenderdoc=qrenderdoc,
            rdc_path=rdc_path,
            top_n=top_n,
            save_json=True,
            save_root_dir=save_root_dir or self.default_analysis_save_root(),
            open_report=open_report,
        )
        highlights: List[str] = []
        flow = payload.get("flow", {}) or {}
        hotspots = (payload.get("hotspots", {}) or {}).get("topByGpuDuration", []) or []
        textures = (payload.get("textures", {}) or {}).get("topByUsageCount", []) or []
        trace_rows = payload.get("pipeline_trace", []) or []
        resource_map = payload.get("resource_map", {}) or {}

        highlights.append(
            f"事件总数: {flow.get('eventCount', 0)}，Draw 数: {flow.get('drawCount', 0)}，BeginPass: {flow.get('beginPassCount', 0)}，EndPass: {flow.get('endPassCount', 0)}"
        )
        if hotspots:
            top_hotspot = hotspots[0]
            highlights.append(
                f"Top GPU hotspot: event #{top_hotspot.get('eventId')}，{top_hotspot.get('name', '')}，{top_hotspot.get('gpuDuration_us', 0)} us"
            )
        if textures:
            top_texture = textures[0]
            highlights.append(
                f"Top texture usage: {top_texture.get('resourceId')}，{top_texture.get('name', '')}，{top_texture.get('usageCount', 0)} 次"
            )
        if trace_rows:
            top_trace = trace_rows[0]
            ps_resources = top_trace.get("psSampledResources", []) or []
            outputs = top_trace.get("outputTargets", []) or []
            highlights.append(
                f"Pipeline trace: event #{top_trace.get('eventId')}，PS 采样 {len(ps_resources)} 个资源，输出 {len(outputs)} 个目标"
            )
        if resource_map:
            highlights.append(f"资源映射条目: {len(resource_map)}")
        if not highlights and payload.get("errors"):
            highlights.append(f"错误: {payload.get('errors')[0]}")
        return AnalysisSummary(
            rdc_path=rdc_path,
            title=rdc_path.name,
            highlights=highlights or ["Analysis completed."],
        )

    def collect_shader_catalog(
        self,
        rdc_root: Path,
        *,
        save_root_dir: Optional[Path] = None,
        renderdoc_dir: Optional[str] = None,
        progress_callback: Optional[Any] = None,
    ) -> ShaderCatalogResult:
        if not rdc_root.exists():
            raise FileNotFoundError(str(rdc_root))
        rdc_files = sorted(rdc_root.rglob("*.rdc"))
        if not rdc_files:
            raise FileNotFoundError(f"No .rdc files found under {rdc_root}")

        _renderdoccmd, qrenderdoc = self.resolve_renderdoc_paths(renderdoc_dir)
        output_dir = _make_run_output_dir(save_root_dir or self.default_analysis_save_root(), "shader_catalog", rdc_root.name)
        output_dir.mkdir(parents=True, exist_ok=True)

        aggregate: Dict[str, Dict[str, Any]] = {}
        written_files: List[Path] = []
        total_draw_events = 0
        failed_rdc_files: List[Dict[str, str]] = []
        errors: List[str] = []
        processed_rdc_files = 0
        overall_total = len(rdc_files) * 10000

        for rdc_path in rdc_files:
            processed_rdc_files += 1
            try:
                payload = self._collect_shader_usage_with_qrenderdoc(
                    qrenderdoc=qrenderdoc,
                    rdc_path=rdc_path,
                    output_dir=output_dir,
                    progress_callback=progress_callback,
                    progress_base=(processed_rdc_files - 1) * 10000,
                    progress_span=10000,
                    rdc_index=processed_rdc_files,
                    rdc_total=len(rdc_files),
                )
            except Exception as exc:
                failed_rdc_files.append({"rdc": str(rdc_path), "error": str(exc)})
                errors.append(f"{rdc_path.name}: {exc}")
                if callable(progress_callback):
                    try:
                        progress_callback(processed_rdc_files * 10000, overall_total, f"失败: {rdc_path.name}")
                    except Exception:
                        pass
                continue

            per_file = output_dir / f"{rdc_path.stem}.shader_catalog.json"
            per_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            written_files.append(per_file)
            total_draw_events += int(payload.get("draw_event_count", 0))
            for row in payload.get("shaders", []) or []:
                shader_id = str(row.get("shaderId", ""))
                if not shader_id:
                    continue
                merged = aggregate.setdefault(
                    shader_id,
                    {
                        "shaderId": shader_id,
                        "stageCounts": {},
                        "entryPoints": {},
                        "usageCount": 0,
                        "rdcFiles": set(),
                        "sampleEvents": [],
                        "effectTags": [],
                        "effectDescription": "",
                        "rawTargets": [],
                        "rawFiles": [],
                        "rawBinaryTargets": [],
                        "rawBinaryFiles": [],
                        "artifactManifests": set(),
                        "artifactDirs": set(),
                        "disassemblyTargets": set(),
                        "sourceDebugInformation": False,
                        "sourceFileCount": 0,
                    },
                )
                merged["usageCount"] += int(row.get("usageCount", 0))
                merged["rdcFiles"].add(str(rdc_path.name))
                for stage, count in (row.get("stageCounts", {}) or {}).items():
                    merged["stageCounts"][stage] = int(merged["stageCounts"].get(stage, 0)) + int(count)
                for stage, entries in (row.get("entryPoints", {}) or {}).items():
                    stage_entries = merged["entryPoints"].setdefault(stage, set())
                    for entry in entries or []:
                        stage_entries.add(str(entry))
                for sample in row.get("sampleEvents", []) or []:
                    if len(merged["sampleEvents"]) < 12:
                        merged["sampleEvents"].append(sample)
                for tag in row.get("effectTags", []) or []:
                    if tag not in merged["effectTags"] and len(merged["effectTags"]) < 20:
                        merged["effectTags"].append(str(tag))
                if not merged["effectDescription"] and row.get("effectDescription"):
                    merged["effectDescription"] = str(row.get("effectDescription", ""))
                for target in row.get("rawTargets", []) or []:
                    if target not in merged["rawTargets"] and len(merged["rawTargets"]) < 12:
                        merged["rawTargets"].append(str(target))
                for item in row.get("rawFiles", []) or []:
                    if len(merged["rawFiles"]) < 12:
                        merged["rawFiles"].append(item)
                for item in row.get("rawBinaryFiles", []) or []:
                    if len(merged["rawBinaryFiles"]) < 12:
                        merged["rawBinaryFiles"].append(item)
                artifact_manifest = str(row.get("artifactManifest", "") or "")
                if artifact_manifest:
                    merged["artifactManifests"].add(artifact_manifest)
                artifact_dir = str(row.get("artifactDir", "") or "")
                if artifact_dir:
                    merged["artifactDirs"].add(artifact_dir)
                for target in row.get("disassemblyTargets", []) or []:
                    merged["disassemblyTargets"].add(str(target))
            merged["sourceDebugInformation"] = bool(merged["sourceDebugInformation"] or row.get("sourceDebugInformation", False))
            merged["sourceFileCount"] += int(row.get("sourceFileCount", 0) or 0)
            if callable(progress_callback):
                try:
                    progress_callback(processed_rdc_files * 10000, overall_total, f"已处理: {rdc_path.name}")
                except Exception:
                    pass

        top_rows: List[Dict[str, Any]] = []
        for shader_id, row in aggregate.items():
            top_rows.append(
                {
                    "shaderId": shader_id,
                    "usageCount": int(row["usageCount"]),
                    "stageCounts": row["stageCounts"],
                    "entryPoints": {stage: sorted(values) for stage, values in row["entryPoints"].items()},
                    "rdcFiles": sorted(row["rdcFiles"]),
                    "sampleEvents": row["sampleEvents"],
                    "effectTags": row["effectTags"],
                    "effectDescription": row["effectDescription"],
                    "rawTargets": row["rawTargets"],
                    "rawFiles": row["rawFiles"],
                    "rawBinaryTargets": row["rawBinaryTargets"],
                    "rawBinaryFiles": row["rawBinaryFiles"],
                    "artifactManifests": sorted(row["artifactManifests"]),
                    "artifactDirs": sorted(row["artifactDirs"]),
                    "disassemblyTargets": sorted(row["disassemblyTargets"]),
                    "sourceDebugInformation": bool(row["sourceDebugInformation"]),
                    "sourceFileCount": int(row["sourceFileCount"]),
                }
            )
        top_rows.sort(key=lambda x: (int(x.get("usageCount", 0)), str(x.get("shaderId", ""))), reverse=True)

        summary = {
            "rdc_root": str(rdc_root),
            "rdc_count": len(rdc_files),
            "total_draw_events": total_draw_events,
            "unique_shader_count": len(top_rows),
            "top_shaders": top_rows,
            "failed_rdc_files": failed_rdc_files,
            "errors": errors,
        }

        summary_path = output_dir / "shader_catalog.summary.json"
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        written_files.append(summary_path)
        return ShaderCatalogResult(
            rdc_root=rdc_root,
            output_dir=output_dir,
            rdc_count=len(rdc_files),
            shader_count=len(top_rows),
            written_files=written_files,
            top_shaders=top_rows,
            failed_rdc_files=failed_rdc_files,
            errors=errors,
        )

    def transpile_shader_catalog(
        self,
        source_root: Path,
        *,
        save_root_dir: Optional[Path] = None,
        renderdoc_dir: Optional[str] = None,
        progress_callback: Optional[Any] = None,
    ) -> ShaderTranspileResult:
        if not source_root.exists():
            raise FileNotFoundError(str(source_root))

        catalog_root = self._resolve_shader_catalog_root(source_root)
        manifests = sorted(catalog_root.rglob("shader.manifest.json"))
        if not manifests:
            raise FileNotFoundError(f"No shader.manifest.json files found under {catalog_root}")

        spirv_cross = self._resolve_spirv_tool("spirv-cross.exe", renderdoc_dir=renderdoc_dir)
        save_root = save_root_dir or self.default_analysis_save_root()
        output_dir = _make_run_output_dir(save_root, "shader_transpile", catalog_root.name)
        output_dir.mkdir(parents=True, exist_ok=True)
        classification_dir = _make_run_output_dir(save_root, "shader_classify", catalog_root.name)
        classification_dir.mkdir(parents=True, exist_ok=True)

        written_files: List[Path] = []
        failed_shaders: List[Dict[str, str]] = []
        errors: List[str] = []
        shader_count = 0
        duplicate_shader_count = 0
        selected_records: Dict[tuple[str, str], Dict[str, Any]] = {}
        duplicate_sources: Dict[tuple[str, str], List[str]] = {}

        for manifest_path in manifests:
            shader_dir = manifest_path.parent
            artifact: Dict[str, Any] = {}
            try:
                artifact = json.loads(manifest_path.read_text(encoding="utf-8"))
                shader_id = str(artifact.get("shaderId", "") or shader_dir.name)
                stage_tag = self._resolve_stage_tag(artifact)
                entry_name = self._resolve_entry_point_name(artifact, stage_tag)
                spirv_source = self._resolve_spirv_source_path(artifact)
                if spirv_source is None:
                    raise RuntimeError(
                        "No binary SPIR-V source found. Re-run shader collection after enabling raw binary capture."
                    )
                if not spirv_source.exists():
                    raise FileNotFoundError(str(spirv_source))
                shader_signature = self._shader_logic_signature(manifest_path, spirv_source, stage_tag)
                dedupe_key = (stage_tag, shader_signature)
                if dedupe_key in selected_records:
                    duplicate_shader_count += 1
                    duplicate_sources.setdefault(dedupe_key, []).append(str(manifest_path))
                    continue
                selected_records[dedupe_key] = {
                    "manifest_path": manifest_path,
                    "artifact": artifact,
                    "shader_id": shader_id,
                    "stage_tag": stage_tag,
                    "entry_name": entry_name,
                    "spirv_source": spirv_source,
                    "shader_signature": shader_signature,
                    "shader_dir": shader_dir,
                }
            except Exception as exc:
                failed_shaders.append(
                    {
                        "manifest": str(manifest_path),
                        "shaderId": str(artifact.get("shaderId", "") or ""),
                        "error": str(exc),
                    }
                )
                errors.append(f"{manifest_path}: {exc}")

        deduped_items = list(selected_records.values())
        deduped_total = len(deduped_items)
        processed_manifests = 0

        for record in deduped_items:
            processed_manifests += 1
            artifact = record["artifact"]
            manifest_path = record["manifest_path"]
            shader_id = record["shader_id"]
            stage_tag = record["stage_tag"]
            entry_name = record["entry_name"]
            spirv_source = record["spirv_source"]
            shader_signature = record["shader_signature"]
            try:
                shader_out_dir = output_dir / f"{_safe_name(shader_id)}__{_safe_name(stage_tag)}__{_safe_name(entry_name)}"
                shader_out_dir.mkdir(parents=True, exist_ok=True)
                hlsl_path = shader_out_dir / "shader.hlsl"
                shaderlab_path = shader_out_dir / ("shader.compute" if stage_tag == "cs" else "shader.shader")
                meta_path = shader_out_dir / "shader.transpile.json"

                self._run_spirv_cross(
                    spirv_cross=spirv_cross,
                    spirv_source=spirv_source,
                    stage_tag=stage_tag,
                    entry_name=entry_name,
                    output_path=hlsl_path,
                )
                if stage_tag == "cs":
                    shaderlab_text = self._build_compute_shaderlab(shader_id=shader_id, stage_tag=stage_tag, entry_name=entry_name)
                else:
                    shaderlab_text = self._build_graphics_shaderlab(shader_id=shader_id, stage_tag=stage_tag, entry_name=entry_name)
                shaderlab_path.write_text(shaderlab_text, encoding="utf-8")
                meta_path.write_text(
                    json.dumps(
                        {
                            "shaderId": shader_id,
                            "stage": stage_tag,
                            "entryPoint": entry_name,
                            "sourceManifest": str(manifest_path),
                            "sourceSpirv": str(spirv_source),
                            "semanticSignature": shader_signature,
                            "duplicateSourceManifests": duplicate_sources.get((stage_tag, shader_signature), []),
                            "hlslFile": str(hlsl_path),
                            "shaderlabFile": str(shaderlab_path),
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                shader_count += 1
                written_files.extend([hlsl_path, shaderlab_path, meta_path])
                record["hlsl_path"] = hlsl_path
                record["shaderlab_path"] = shaderlab_path
                record["meta_path"] = meta_path
                if callable(progress_callback):
                    try:
                        progress_callback(processed_manifests, deduped_total, f"已汇总: {shader_id}")
                    except Exception:
                        pass
            except Exception as exc:
                failed_shaders.append(
                    {
                        "manifest": str(manifest_path),
                        "shaderId": str(shader_id),
                        "error": str(exc),
                    }
                )
                errors.append(f"{manifest_path}: {exc}")
                if callable(progress_callback):
                    try:
                        progress_callback(processed_manifests, deduped_total, f"失败: {manifest_path.name}")
                    except Exception:
                        pass

        summary = {
            "source_root": str(source_root),
            "catalog_root": str(catalog_root),
            "output_dir": str(output_dir),
            "classification_output_dir": str(classification_dir),
            "shader_count": shader_count,
            "duplicate_shader_count": duplicate_shader_count,
            "manifest_count": len(manifests),
            "unique_manifest_count": deduped_total,
            "failed_shaders": failed_shaders,
            "errors": errors,
        }
        summary_path = output_dir / "shader_transpile.summary.json"
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        written_files.append(summary_path)
        classification_steps = 4
        classification_base = deduped_total
        classification_total = max(deduped_total + classification_steps, 1)
        if callable(progress_callback):
            try:
                progress_callback(classification_base, classification_total, "正在生成分类摘要")
            except Exception:
                pass
        family_summary_path, family_count = self._build_shader_family_summary(deduped_items, classification_dir)
        if family_summary_path is not None:
            written_files.append(family_summary_path)
        if callable(progress_callback):
            try:
                progress_callback(classification_base + 1, classification_total, "正在生成 family 分类")
            except Exception:
                pass
        effect_summary_path, effect_count = self._build_shader_effect_summary(classification_dir)
        if effect_summary_path is not None:
            written_files.append(effect_summary_path)
        if callable(progress_callback):
            try:
                progress_callback(classification_base + 2, classification_total, "正在生成 effect 分类")
            except Exception:
                pass
        role_summary_path, role_count = self._build_shader_role_summary(classification_dir)
        if role_summary_path is not None:
            written_files.append(role_summary_path)
        if callable(progress_callback):
            try:
                progress_callback(classification_base + 3, classification_total, "正在生成 role 分类")
            except Exception:
                pass
        core_summary_path, core_count = self._build_shader_core_summary(classification_dir)
        if core_summary_path is not None:
            written_files.append(core_summary_path)
        if callable(progress_callback):
            try:
                progress_callback(classification_base + 4, classification_total, "正在生成 core 分类")
            except Exception:
                pass
        return ShaderTranspileResult(
            source_dir=catalog_root,
            output_dir=output_dir,
            shader_count=shader_count,
            classification_output_dir=classification_dir,
            duplicate_shader_count=duplicate_shader_count,
            family_count=family_count,
            effect_count=effect_count,
            role_count=role_count,
            core_count=core_count,
            summary_file=summary_path,
            family_summary_file=family_summary_path,
            effect_summary_file=effect_summary_path,
            role_summary_file=role_summary_path,
            core_summary_file=core_summary_path,
            written_files=written_files,
            failed_shaders=failed_shaders,
            errors=errors,
        )

    def _resolve_shader_catalog_root(self, source_root: Path) -> Path:
        if self._is_shader_catalog_run_dir(source_root):
            return source_root
        if source_root.name == "shader_catalog":
            return self._select_latest_shader_catalog_run(source_root)
        candidate = source_root / "shader_catalog"
        if candidate.exists():
            return self._select_latest_shader_catalog_run(candidate)
        return source_root

    def _select_latest_shader_catalog_run(self, catalog_root: Path) -> Path:
        if self._is_shader_catalog_run_dir(catalog_root):
            return catalog_root
        candidates: List[Path] = []
        for child in catalog_root.iterdir():
            if not child.is_dir():
                continue
            if self._is_shader_catalog_run_dir(child):
                candidates.append(child)
        if not candidates:
            return catalog_root
        return max(candidates, key=lambda path: (path.stat().st_mtime, path.name))

    def _is_shader_catalog_run_dir(self, path: Path) -> bool:
        if not path.is_dir():
            return False
        if any(path.glob("*.shader_catalog.json")):
            return True
        if (path / "shaders").is_dir():
            return True
        return any(child.is_dir() and child.name.startswith("ResourceId_") for child in path.iterdir())

    def focus_rdc_event(
        self,
        rdc_path: Path,
        *,
        event_id: Optional[int] = None,
        hotspot_rank: int = 1,
        hotspot_top_n: int = 12,
        context_top_n: int = 24,
        show_event_browser: bool = True,
        keep_qrenderdoc_open: bool = True,
        persist_context: bool = True,
        save_root_dir: Optional[Path] = None,
        renderdoc_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        _renderdoccmd, qrenderdoc = self.resolve_renderdoc_paths(renderdoc_dir)
        return self._legacy._focus_rdc_event(
            {
                "rdc_path": str(rdc_path),
                "event_id": event_id,
                "hotspot_rank": hotspot_rank,
                "hotspot_top_n": hotspot_top_n,
                "context_top_n": context_top_n,
                "show_event_browser": show_event_browser,
                "keep_qrenderdoc_open": keep_qrenderdoc_open,
                "persist_context": persist_context,
                "save_root_dir": str(save_root_dir) if save_root_dir else None,
                "renderdoc_dir": str(qrenderdoc.parent),
            }
        )

    def analyze_event(
        self,
        rdc_path: Path,
        *,
        event_id: int,
        export_images: bool = True,
        save_root_dir: Optional[Path] = None,
        renderdoc_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        _renderdoccmd, qrenderdoc = self.resolve_renderdoc_paths(renderdoc_dir)
        return self._legacy._analyze_event(
            {
                "rdc_path": str(rdc_path),
                "event_id": int(event_id),
                "export_images": export_images,
                "save_root_dir": str(save_root_dir) if save_root_dir else None,
                "renderdoc_dir": str(qrenderdoc.parent),
            }
        )

    def get_event_state(
        self,
        rdc_path: Path,
        *,
        event_id: int,
        save_root_dir: Optional[Path] = None,
        renderdoc_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        _renderdoccmd, qrenderdoc = self.resolve_renderdoc_paths(renderdoc_dir)
        return self._legacy._get_event_state(
            {
                "rdc_path": str(rdc_path),
                "event_id": int(event_id),
                "save_root_dir": str(save_root_dir) if save_root_dir else None,
                "renderdoc_dir": str(qrenderdoc.parent),
            }
        )

    def compare_events(
        self,
        rdc_path: Path,
        *,
        event_a: int,
        event_b: int,
        save_root_dir: Optional[Path] = None,
        renderdoc_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        _renderdoccmd, qrenderdoc = self.resolve_renderdoc_paths(renderdoc_dir)
        return self._legacy._compare_events(
            {
                "rdc_path": str(rdc_path),
                "event_a": int(event_a),
                "event_b": int(event_b),
                "save_root_dir": str(save_root_dir) if save_root_dir else None,
                "renderdoc_dir": str(qrenderdoc.parent),
            }
        )

    def _resolve_spirv_tool(self, tool_name: str, *, renderdoc_dir: Optional[str] = None) -> Path:
        repo_root = Path(__file__).resolve().parents[4]
        candidates = [
            repo_root / "plugins" / "spirv" / tool_name,
            Path(r"C:\Program Files\Tuanjie\Hub\Editor\2022.3.2t13\Editor\Data\Tools") / tool_name,
        ]
        if renderdoc_dir:
            renderdoc_root = Path(renderdoc_dir).expanduser()
            candidates.append(renderdoc_root / tool_name)
            candidates.append(renderdoc_root.parent / "Tools" / tool_name)
        which = shutil.which(tool_name)
        if which:
            candidates.append(Path(which))
        for candidate in candidates:
            if candidate.exists():
                return candidate
        raise FileNotFoundError(f"{tool_name} not found. Checked: " + "; ".join(str(path) for path in candidates))

    def _clean_qrenderdoc_env(self) -> Dict[str, str]:
        env = dict(os.environ)
        env.pop("QT_QPA_PLATFORM", None)
        env.pop("QT_QPA_PLATFORM_PLUGIN_PATH", None)
        env.pop("QT_PLUGIN_PATH", None)
        env.pop("QML2_IMPORT_PATH", None)
        env.pop("QML_IMPORT_PATH", None)
        env.pop("QML_DISABLE_DISK_CACHE", None)
        env["QT_QPA_PLATFORM"] = "windows"
        return env

    def _resolve_stage_tag(self, artifact: Dict[str, Any]) -> str:
        stage_counts = artifact.get("stageCounts", {}) or {}
        stage_order = ["vs", "hs", "ds", "gs", "ps", "cs"]
        first_stage = str(artifact.get("firstStage", "") or "").strip().lower()
        if first_stage in _STAGE_TO_SPIRV_CROSS:
            return first_stage
        for stage_tag in stage_order:
            if int(stage_counts.get(stage_tag, 0) or 0) > 0:
                return stage_tag
        return "ps"

    def _resolve_entry_point_name(self, artifact: Dict[str, Any], stage_tag: str) -> str:
        entry_source = str(artifact.get("entrySourceName", "") or "").strip()
        if entry_source:
            return entry_source
        entry_points = artifact.get("entryPoints", {}) or {}
        stage_entries = entry_points.get(stage_tag, []) or []
        if stage_entries:
            return str(stage_entries[0])
        for entries in entry_points.values():
            if entries:
                return str(entries[0])
        return "main"

    def _shader_source_signature(self, spirv_source: Path, stage_tag: str) -> str:
        digest = hashlib.sha256()
        digest.update(stage_tag.encode("utf-8"))
        digest.update(b"\0")
        digest.update(spirv_source.read_bytes())
        return digest.hexdigest()

    def _shader_logic_signature(self, manifest_path: Path, spirv_source: Path, stage_tag: str) -> str:
        digest = hashlib.sha256()
        digest.update(stage_tag.encode("utf-8"))
        digest.update(b"\0")
        signature_text = self._load_spirv_semantic_text(manifest_path, spirv_source)
        digest.update(signature_text.encode("utf-8"))
        return digest.hexdigest()

    def _build_shader_family_summary(self, shader_records: List[Dict[str, Any]], output_dir: Path) -> tuple[Optional[Path], int]:
        if not shader_records:
            return None, 0

        buckets: Dict[str, List[Dict[str, Any]]] = {}
        for record in shader_records:
            hlsl_path = record.get("hlsl_path")
            if not isinstance(hlsl_path, Path) or not hlsl_path.exists():
                continue
            stage = str(record.get("stage_tag", "") or "ps")
            normalized = self._normalize_hlsl_lines(hlsl_path.read_text(encoding="utf-8", errors="ignore"))
            if not normalized:
                continue
            struct_key = self._family_struct_key(normalized)
            bucket_key = f"{stage}|{struct_key}"
            buckets.setdefault(bucket_key, []).append(
                {
                    "shaderId": str(record.get("shader_id", "")),
                    "stage": stage,
                    "entryPoint": str(record.get("entry_name", "main")),
                    "hlslPath": str(hlsl_path),
                    "semanticSignature": str(record.get("shader_signature", "")),
                    "normalizedLines": normalized,
                    "sourceManifest": str(record.get("manifest_path", "")),
                    "duplicateSourceManifests": [str(x) for x in record.get("duplicateSourceManifests", [])] if record.get("duplicateSourceManifests") else [],
                }
            )

        families: List[Dict[str, Any]] = []
        family_index = 0
        for bucket_key in sorted(buckets.keys()):
            members = buckets[bucket_key]
            members.sort(key=lambda item: (len(item["normalizedLines"]), item["shaderId"], item["entryPoint"]), reverse=True)
            clusters: List[Dict[str, Any]] = []
            for member in members:
                assigned = False
                for cluster in clusters:
                    rep = cluster["members"][0]
                    if self._family_similarity(rep["normalizedLines"], member["normalizedLines"]) >= 0.82:
                        cluster["members"].append(member)
                        assigned = True
                        break
                if not assigned:
                    clusters.append({"members": [member]})

            for cluster in clusters:
                family_index += 1
                members_list = cluster["members"]
                prefix = self._common_prefix_lines([m["normalizedLines"] for m in members_list])
                suffix = self._common_suffix_lines([m["normalizedLines"] for m in members_list])
                middles = []
                for member in members_list:
                    middle = member["normalizedLines"][len(prefix): len(member["normalizedLines"]) - len(suffix) if suffix else len(member["normalizedLines"])]
                    middles.append(
                        {
                            "shaderId": member["shaderId"],
                            "stage": member["stage"],
                            "entryPoint": member["entryPoint"],
                            "semanticSignature": member["semanticSignature"],
                            "sourceManifest": member["sourceManifest"],
                            "duplicateSourceManifests": member["duplicateSourceManifests"],
                            "middleLines": middle[:120],
                        }
                    )
                family = {
                    "familyId": f"family_{family_index:04d}",
                    "stage": members_list[0]["stage"],
                    "bucketKey": bucket_key,
                    "profileKey": self._family_profile_key(members_list),
                    "effectKey": self._family_effect_key(members_list),
                    "memberCount": len(members_list),
                    "representativeShaderId": members_list[0]["shaderId"],
                    "representativeHlslPath": members_list[0]["hlslPath"],
                    "commonPrefix": prefix[:120],
                    "commonSuffix": suffix[:120],
                    "members": [
                        {
                            "shaderId": m["shaderId"],
                            "entryPoint": m["entryPoint"],
                            "semanticSignature": m["semanticSignature"],
                            "sourceManifest": m["sourceManifest"],
                            "hlslPath": m["hlslPath"],
                            "duplicateSourceManifests": m["duplicateSourceManifests"],
                        }
                        for m in members_list
                    ],
                    "branchCandidates": middles,
                }
                families.append(family)

        family_summary = {
            "source_root": str(output_dir),
            "family_count": len(families),
            "families": families,
        }
        family_summary_path = output_dir / "shader_family.summary.json"
        family_summary_path.write_text(json.dumps(family_summary, ensure_ascii=False, indent=2), encoding="utf-8")
        self._write_shader_family_skeletons(output_dir, families)
        return family_summary_path, len(families)

    def _write_shader_group_bundle(
        self,
        *,
        group_dir: Path,
        stage: str,
        representative_shader_id: str,
        representative_hlsl_path: str,
        payload: Dict[str, Any],
    ) -> None:
        group_dir.mkdir(parents=True, exist_ok=True)
        source_hlsl_path = Path(representative_hlsl_path)
        target_hlsl_path = group_dir / "shader.hlsl"
        if source_hlsl_path.exists():
            target_hlsl_path.write_text(source_hlsl_path.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
        else:
            target_hlsl_path.write_text("", encoding="utf-8")

        if stage == "cs":
            shaderlab_path = group_dir / "shader.compute"
            shaderlab_text = self._build_compute_shaderlab(
                shader_id=representative_shader_id,
                stage_tag=stage,
                entry_name="main",
            )
        else:
            shaderlab_path = group_dir / "shader.shader"
            shaderlab_text = self._build_group_graphics_shaderlab(
                group_id=group_dir.name,
                stage_tag=stage,
                payload=payload,
            )
        shaderlab_path.write_text(shaderlab_text, encoding="utf-8")
        (group_dir / "group.summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _build_group_graphics_shaderlab(self, *, group_id: str, stage_tag: str, payload: Dict[str, Any]) -> str:
        group_name = f"RenderDoc/Classified/{_safe_name(group_id)}"
        members = payload.get("members", []) or []
        if not members:
            members = [
                {
                    "memberName": "Member_1",
                    "stage": stage_tag,
                    "representativeShaderId": str(payload.get("representativeShaderId", "") or ""),
                    "representativeHlslPath": str(payload.get("representativeHlslPath", "") or ""),
                }
            ]

        common_tags = [
            "        Tags { \"RenderType\" = \"Opaque\" \"Queue\" = \"Geometry+100\" }",
        ]
        pass_blocks: List[str] = []
        for idx, member in enumerate(members, 1):
            member_label = (
                member.get("familyId")
                or member.get("effectId")
                or member.get("roleId")
                or member.get("coreId")
                or member.get("shaderId")
                or f"Pass_{idx:02d}"
            )
            source_tag = (
                member.get("shaderId")
                or member.get("familyId")
                or member.get("effectId")
                or member.get("roleId")
                or member.get("coreId")
                or "unknown"
            )
            hlsl_path = (
                member.get("representativeHlslPath")
                or member.get("hlslPath")
                or payload.get("representativeHlslPath")
                or payload.get("representativeHlslPath")
                or ""
            )
            pass_name = _safe_name(member_label)
            pass_blocks.extend(
                [
                    "        Pass",
                    "        {",
                    f'            Name "{pass_name}"',
                    f'            Tags {{ "LightMode" = "{stage_tag.upper()}_{idx:02d}" }}',
                    "            Cull Off",
                    "            ZWrite On",
                    "            HLSLPROGRAM",
                    f"            // Group: {group_id}",
                    f"            // Member: {member_label}",
                    f"            // Source: {source_tag}",
                    f"            // HLSL: {hlsl_path}",
                    "            #pragma target 5.0",
                ]
            )
            if stage_tag == "vs":
                pass_blocks.extend(
                    [
                        "            #pragma vertex rdc_passthrough_vert",
                        "            #pragma fragment rdc_passthrough_frag",
                    ]
                )
            elif stage_tag == "ps":
                pass_blocks.extend(
                    [
                        "            #pragma vertex rdc_passthrough_vert",
                        "            #pragma fragment rdc_passthrough_frag",
                    ]
                )
            elif stage_tag == "gs":
                pass_blocks.extend(
                    [
                        "            #pragma vertex rdc_passthrough_vert",
                        "            #pragma fragment rdc_passthrough_frag",
                    ]
                )
            elif stage_tag == "hs":
                pass_blocks.extend(
                    [
                        "            #pragma vertex rdc_passthrough_vert",
                        "            #pragma fragment rdc_passthrough_frag",
                    ]
                )
            elif stage_tag == "ds":
                pass_blocks.extend(
                    [
                        "            #pragma vertex rdc_passthrough_vert",
                        "            #pragma fragment rdc_passthrough_frag",
                    ]
                )
            else:
                pass_blocks.extend(
                    [
                        "            #pragma vertex rdc_passthrough_vert",
                        "            #pragma fragment rdc_passthrough_frag",
                    ]
                )
            pass_blocks.extend(
                [
                    "            ENDHLSL",
                    "        }",
                    "",
                ]
            )

        wrapper = [
            f'Shader "{group_name}"',
            "{",
            "    Properties",
            "    {",
            "    }",
            "    HLSLINCLUDE",
            '        #include "shader.hlsl"',
            "    ENDHLSL",
            "    SubShader",
            "    {",
        ]
        wrapper.extend(common_tags)
        wrapper.append(f'        // Group stage: {stage_tag}')
        wrapper.extend(pass_blocks)
        wrapper.extend(
            [
                "    }",
                "}",
            ]
        )
        return "\n".join(wrapper) + "\n"

    def _write_shader_family_skeletons(self, output_dir: Path, families: List[Dict[str, Any]]) -> None:
        skeleton_dir = output_dir / "shader_families"
        skeleton_dir.mkdir(parents=True, exist_ok=True)
        for family in families:
            group_dir = skeleton_dir / family["familyId"]
            self._write_shader_group_bundle(
                group_dir=group_dir,
                stage=str(family.get("stage", "ps") or "ps"),
                representative_shader_id=str(family.get("representativeShaderId", "")),
                representative_hlsl_path=str(family.get("representativeHlslPath", "")),
                payload=family,
            )

    def _build_shader_effect_summary(self, output_dir: Path) -> tuple[Optional[Path], int]:
        family_summary_path = output_dir / "shader_family.summary.json"
        if not family_summary_path.exists():
            return None, 0

        family_summary = json.loads(family_summary_path.read_text(encoding="utf-8"))
        families = family_summary.get("families", []) or []
        if not families:
            return None, 0

        buckets: Dict[str, List[Dict[str, Any]]] = {}
        for family in families:
            effect_key = str(family.get("effectKey", "") or "")
            if not effect_key:
                effect_key = str(family.get("profileKey", "") or family.get("stage", "") or "unknown")
            buckets.setdefault(effect_key, []).append(family)

        effects: List[Dict[str, Any]] = []
        effect_index = 0
        for bucket_key in sorted(buckets.keys()):
            bucket_families = buckets[bucket_key]
            bucket_families.sort(key=lambda item: (int(item.get("memberCount", 0)), str(item.get("familyId", ""))), reverse=True)
            clusters: List[List[Dict[str, Any]]] = []
            for family in bucket_families:
                assigned = False
                for cluster in clusters:
                    rep = cluster[0]
                    if self._effect_similarity(rep, family) >= 0.72:
                        cluster.append(family)
                        assigned = True
                        break
                if not assigned:
                    clusters.append([family])

            for cluster in clusters:
                effect_index += 1
                members: List[Dict[str, Any]] = []
                stages = []
                for family in cluster:
                    stages.append(str(family.get("stage", "unknown")))
                    members.append(
                        {
                            "familyId": family.get("familyId"),
                            "stage": family.get("stage"),
                            "memberCount": family.get("memberCount", 0),
                            "representativeShaderId": family.get("representativeShaderId"),
                            "representativeHlslPath": family.get("representativeHlslPath"),
                            "bucketKey": family.get("bucketKey"),
                            "profileKey": family.get("profileKey"),
                            "effectKey": family.get("effectKey"),
                        }
                    )
                effect = {
                    "effectId": f"effect_{effect_index:04d}",
                    "bucketKey": bucket_key,
                    "effectKey": bucket_key,
                    "memberFamilyCount": len(cluster),
                    "stages": sorted(set(stages)),
                    "representativeShaderId": cluster[0].get("representativeShaderId"),
                    "representativeHlslPath": cluster[0].get("representativeHlslPath"),
                    "members": members,
                    "commonPrefix": self._common_prefix_lines([list(family.get("commonPrefix", []) or []) for family in cluster])[:120],
                    "commonSuffix": self._common_suffix_lines([list(family.get("commonSuffix", []) or []) for family in cluster])[:120],
                }
                effects.append(effect)

        effect_summary = {
            "source_root": str(output_dir),
            "effect_count": len(effects),
            "effects": effects,
        }
        effect_summary_path = output_dir / "shader_effect.summary.json"
        effect_summary_path.write_text(json.dumps(effect_summary, ensure_ascii=False, indent=2), encoding="utf-8")
        self._write_shader_effect_skeletons(output_dir, effects)
        return effect_summary_path, len(effects)

    def _write_shader_effect_skeletons(self, output_dir: Path, effects: List[Dict[str, Any]]) -> None:
        skeleton_dir = output_dir / "shader_effects"
        skeleton_dir.mkdir(parents=True, exist_ok=True)
        for effect in effects:
            group_dir = skeleton_dir / effect["effectId"]
            self._write_shader_group_bundle(
                group_dir=group_dir,
                stage=str((effect.get("stages", []) or ["ps"])[0] or "ps"),
                representative_shader_id=str(effect.get("representativeShaderId", "")),
                representative_hlsl_path=str(effect.get("representativeHlslPath", "")),
                payload=effect,
            )

    def _build_shader_role_summary(self, output_dir: Path) -> tuple[Optional[Path], int]:
        effect_summary_path = output_dir / "shader_effect.summary.json"
        if not effect_summary_path.exists():
            return None, 0

        effect_summary = json.loads(effect_summary_path.read_text(encoding="utf-8"))
        effects = effect_summary.get("effects", []) or []
        if not effects:
            return None, 0

        buckets: Dict[str, List[Dict[str, Any]]] = {}
        for effect in effects:
            role_key = self._compress_role_key(str(effect.get("effectKey", "") or ""))
            if not role_key:
                role_key = str(effect.get("bucketKey", "") or "unknown")
            buckets.setdefault(role_key, []).append(effect)

        roles: List[Dict[str, Any]] = []
        role_index = 0
        for bucket_key in sorted(buckets.keys()):
            bucket_effects = buckets[bucket_key]
            bucket_effects.sort(key=lambda item: (int(item.get("memberFamilyCount", 0)), str(item.get("effectId", ""))), reverse=True)
            clusters: List[List[Dict[str, Any]]] = []
            for effect in bucket_effects:
                assigned = False
                for cluster in clusters:
                    rep = cluster[0]
                    if self._role_similarity(rep, effect) >= 0.56:
                        cluster.append(effect)
                        assigned = True
                        break
                if not assigned:
                    clusters.append([effect])

            for cluster in clusters:
                role_index += 1
                stages: List[str] = []
                members: List[Dict[str, Any]] = []
                for effect in cluster:
                    stages.extend(str(stage) for stage in effect.get("stages", []) or [])
                    members.append(
                        {
                            "effectId": effect.get("effectId"),
                            "stages": effect.get("stages", []),
                            "memberFamilyCount": effect.get("memberFamilyCount", 0),
                            "representativeHlslPath": effect.get("representativeHlslPath"),
                            "bucketKey": effect.get("bucketKey"),
                            "effectKey": effect.get("effectKey"),
                        }
                    )
                role = {
                    "roleId": f"role_{role_index:04d}",
                    "bucketKey": bucket_key,
                    "roleKey": bucket_key,
                    "memberEffectCount": len(cluster),
                    "stages": sorted(set(stages)),
                    "representativeEffectId": cluster[0].get("effectId"),
                    "representativeShaderId": cluster[0].get("representativeShaderId", ""),
                    "representativeHlslPath": cluster[0].get("representativeHlslPath", ""),
                    "members": members,
                    "commonPrefix": self._common_prefix_lines([list(effect.get("commonPrefix", []) or []) for effect in cluster])[:120],
                    "commonSuffix": self._common_suffix_lines([list(effect.get("commonSuffix", []) or []) for effect in cluster])[:120],
                }
                roles.append(role)

        role_summary = {
            "source_root": str(output_dir),
            "role_count": len(roles),
            "roles": roles,
        }
        role_summary_path = output_dir / "shader_role.summary.json"
        role_summary_path.write_text(json.dumps(role_summary, ensure_ascii=False, indent=2), encoding="utf-8")
        self._write_shader_role_skeletons(output_dir, roles)
        return role_summary_path, len(roles)

    def _build_shader_core_summary(self, output_dir: Path) -> tuple[Optional[Path], int]:
        family_summary_path = output_dir / "shader_family.summary.json"
        if not family_summary_path.exists():
            return None, 0

        family_summary = json.loads(family_summary_path.read_text(encoding="utf-8"))
        families = family_summary.get("families", []) or []
        if not families:
            return None, 0

        buckets: Dict[str, List[Dict[str, Any]]] = {}
        for family in families:
            hlsl_path_text = str(family.get("representativeHlslPath", "") or "")
            if not hlsl_path_text:
                continue
            hlsl_path = Path(hlsl_path_text)
            if not hlsl_path.exists():
                continue
            core_lines = self._normalize_core_hlsl_lines(hlsl_path.read_text(encoding="utf-8", errors="ignore"))
            if not core_lines:
                continue
            stage = str(family.get("stage", "unknown") or "unknown")
            bucket_key = f"{stage}|{self._core_profile_key(core_lines)}"
            buckets.setdefault(bucket_key, []).append(
                {
                    "familyId": family.get("familyId"),
                    "stage": stage,
                    "memberCount": family.get("memberCount", 0),
                    "representativeShaderId": family.get("representativeShaderId"),
                    "hlslPath": hlsl_path_text,
                    "coreLines": core_lines,
                }
            )

        cores: List[Dict[str, Any]] = []
        core_index = 0
        for bucket_key in sorted(buckets.keys()):
            bucket_families = buckets[bucket_key]
            bucket_families.sort(key=lambda item: (int(item.get("memberCount", 0)), str(item.get("familyId", ""))), reverse=True)
            clusters: List[List[Dict[str, Any]]] = []
            for family in bucket_families:
                assigned = False
                for cluster in clusters:
                    rep = cluster[0]
                    if self._core_similarity(rep["coreLines"], family["coreLines"]) >= 0.54:
                        cluster.append(family)
                        assigned = True
                        break
                if not assigned:
                    clusters.append([family])

            for cluster in clusters:
                core_index += 1
                stages: List[str] = []
                members: List[Dict[str, Any]] = []
                for family in cluster:
                    stages.append(str(family.get("stage", "unknown")))
                    members.append(
                        {
                            "familyId": family.get("familyId"),
                            "stage": family.get("stage"),
                            "memberCount": family.get("memberCount", 0),
                            "representativeShaderId": family.get("representativeShaderId"),
                            "representativeHlslPath": family.get("representativeHlslPath"),
                            "hlslPath": family.get("hlslPath"),
                        }
                    )
                core_lines = self._common_prefix_lines([list(family.get("coreLines", []) or []) for family in cluster])[:120]
                core_suffix = self._common_suffix_lines([list(family.get("coreLines", []) or []) for family in cluster])[:120]
                core = {
                    "coreId": f"core_{core_index:04d}",
                    "bucketKey": bucket_key,
                    "coreKey": bucket_key,
                    "memberFamilyCount": len(cluster),
                    "stages": sorted(set(stages)),
                    "representativeShaderId": cluster[0].get("representativeShaderId", ""),
                    "representativeHlslPath": cluster[0].get("representativeHlslPath", ""),
                    "members": members,
                    "commonPrefix": core_lines,
                    "commonSuffix": core_suffix,
                }
                cores.append(core)

        core_summary = {
            "source_root": str(output_dir),
            "core_count": len(cores),
            "cores": cores,
        }
        core_summary_path = output_dir / "shader_core.summary.json"
        core_summary_path.write_text(json.dumps(core_summary, ensure_ascii=False, indent=2), encoding="utf-8")
        self._write_shader_core_skeletons(output_dir, cores)
        return core_summary_path, len(cores)

    def _write_shader_core_skeletons(self, output_dir: Path, cores: List[Dict[str, Any]]) -> None:
        skeleton_dir = output_dir / "shader_cores"
        skeleton_dir.mkdir(parents=True, exist_ok=True)
        for core in cores:
            group_dir = skeleton_dir / core["coreId"]
            self._write_shader_group_bundle(
                group_dir=group_dir,
                stage=str((core.get("stages", []) or ["ps"])[0] or "ps"),
                representative_shader_id=str(core.get("representativeShaderId", "")),
                representative_hlsl_path=str(core.get("representativeHlslPath", "")),
                payload=core,
            )

    def _write_shader_role_skeletons(self, output_dir: Path, roles: List[Dict[str, Any]]) -> None:
        skeleton_dir = output_dir / "shader_roles"
        skeleton_dir.mkdir(parents=True, exist_ok=True)
        for role in roles:
            group_dir = skeleton_dir / role["roleId"]
            self._write_shader_group_bundle(
                group_dir=group_dir,
                stage=str((role.get("stages", []) or ["ps"])[0] or "ps"),
                representative_shader_id=str(role.get("representativeShaderId", "")),
                representative_hlsl_path=str(role.get("representativeHlslPath", "")),
                payload=role,
            )

    def _normalize_hlsl_lines(self, text: str) -> List[str]:
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        lines: List[str] = []
        for raw_line in text.splitlines():
            line = raw_line.split("//", 1)[0].strip()
            if not line:
                continue
            line = re.sub(r"0x[0-9A-Fa-f]+", "<num>", line)
            line = re.sub(r"(?<![A-Za-z0-9_\.])(?:\d+\.\d*|\d*\.\d+|\d+)(?:[eE][+\-]?\d+)?[fFuUlL]?", "<num>", line)
            line = re.sub(r"\b[A-Za-z_][A-Za-z0-9_]*\b", lambda m: self._normalize_hlsl_token(m.group(0)), line)
            line = re.sub(r"\s+", " ", line).strip()
            if line:
                lines.append(line)
        return lines

    def _normalize_core_hlsl_lines(self, text: str) -> List[str]:
        lines = self._normalize_hlsl_lines(text)
        filtered: List[str] = []
        for line in lines:
            if line.startswith("#"):
                continue
            if line.startswith("cbuffer ") or line.startswith("struct ") or line.startswith("Texture") or line.startswith("SamplerState"):
                continue
            if ": register(" in line or ": packoffset(" in line:
                continue
            if ": SV_" in line or " SV_" in line:
                continue
            if "gl_Position" in line or "gl_FragCoord" in line or "gl_GlobalInvocationID" in line:
                continue
            if line.startswith("void ") and line.endswith(")"):
                continue
            if line.startswith("static const ") or line.startswith("static float") or line.startswith("static int") or line.startswith("static bool"):
                continue
            if line.endswith("{") and ("cbuffer" in line or "struct" in line):
                continue
            if line == "}" or line == "{":
                continue
            filtered.append(line)
        return filtered

    def _extract_hlsl_profile(self, normalized_lines: List[str]) -> Dict[str, int]:
        counters = Counter()
        for line in normalized_lines:
            if line.startswith("cbuffer "):
                counters["cbuffer"] += 1
            if "Texture2D" in line:
                counters["texture2d"] += 1
            if "Texture3D" in line:
                counters["texture3d"] += 1
            if "TextureCube" in line:
                counters["texturecube"] += 1
            if "SamplerState" in line:
                counters["sampler"] += 1
            if "SV_Target" in line:
                counters["sv_target"] += 1
            if "SV_Position" in line:
                counters["sv_position"] += 1
            if "SV_Depth" in line:
                counters["sv_depth"] += 1
            if "if (" in line or line.startswith("if "):
                counters["if"] += 1
            if "switch (" in line or line.startswith("switch "):
                counters["switch"] += 1
            if "for (" in line or line.startswith("for "):
                counters["for"] += 1
            if "while (" in line or line.startswith("while "):
                counters["while"] += 1
            if "SampleLevel" in line:
                counters["samplelevel"] += 1
            elif "Sample(" in line or ".Sample(" in line:
                counters["sample"] += 1
            if "Load(" in line or ".Load(" in line:
                counters["load"] += 1
            if "discard" in line or "clip(" in line:
                counters["discard"] += 1
        counters["lines"] = len(normalized_lines)
        return dict(counters)

    def _normalize_hlsl_token(self, token: str) -> str:
        keywords = {
            "if", "else", "switch", "case", "default", "break", "continue", "return",
            "for", "while", "do", "struct", "cbuffer", "static", "const", "void", "true", "false",
            "float", "float2", "float3", "float4", "int", "int2", "int3", "int4", "uint", "uint2", "uint3", "uint4",
            "bool", "bool2", "bool3", "bool4", "half", "half2", "half3", "half4",
            "Texture1D", "Texture2D", "Texture3D", "TextureCube", "SamplerState", "SamplerComparisonState",
            "Sample", "SampleLevel", "SampleGrad", "Load", "Gather", "GatherRed", "GatherGreen", "GatherBlue", "GatherAlpha",
            "abs", "acos", "asin", "atan", "atan2", "ceil", "clamp", "cos", "cross", "ddx", "ddy", "determinant",
            "dot", "exp", "exp2", "floor", "frac", "length", "lerp", "log", "log2", "max", "min", "normalize",
            "pow", "rcp", "reflect", "refract", "round", "rsqrt", "saturate", "sin", "sincos", "smoothstep", "sqrt",
            "step", "transpose", "all", "any", "discard", "clip", "SV_Position", "SV_Target0", "SV_Target1", "SV_Target2",
            "SV_Target3", "SV_Depth", "SV_DispatchThreadID", "SV_GroupThreadID", "SV_GroupID", "SV_VertexID", "SV_InstanceID",
        }
        if token in keywords:
            return token
        if token.startswith("SV_"):
            return token
        return "<id>"

    def _family_struct_key(self, normalized_lines: List[str]) -> str:
        profile = self._extract_hlsl_profile(normalized_lines)
        key_parts = [
            f"lines={profile.get('lines', 0)}",
            *[f"{token}={profile.get(token, 0)}" for token in ("if", "switch", "for", "while", "sample", "samplelevel", "load", "discard", "texture2d", "texture3d", "sampler")],
        ]
        return "|".join(key_parts)

    def _family_profile_key(self, members_list: List[Dict[str, Any]]) -> str:
        normalized_lines = members_list[0].get("normalizedLines", []) if members_list else []
        profile = self._extract_hlsl_profile([str(line) for line in normalized_lines])
        parts = [
            f"lines={profile.get('lines', 0)}",
            f"cbuffer={profile.get('cbuffer', 0)}",
            f"texture2d={profile.get('texture2d', 0)}",
            f"texture3d={profile.get('texture3d', 0)}",
            f"texturecube={profile.get('texturecube', 0)}",
            f"sampler={profile.get('sampler', 0)}",
            f"sv_target={profile.get('sv_target', 0)}",
            f"sv_position={profile.get('sv_position', 0)}",
            f"sv_depth={profile.get('sv_depth', 0)}",
            f"if={profile.get('if', 0)}",
            f"switch={profile.get('switch', 0)}",
            f"for={profile.get('for', 0)}",
            f"while={profile.get('while', 0)}",
            f"sample={profile.get('sample', 0)}",
            f"samplelevel={profile.get('samplelevel', 0)}",
            f"load={profile.get('load', 0)}",
            f"discard={profile.get('discard', 0)}",
        ]
        return "|".join(parts)

    def _family_effect_key(self, members_list: List[Dict[str, Any]]) -> str:
        normalized_lines = members_list[0].get("normalizedLines", []) if members_list else []
        profile = self._extract_hlsl_profile([str(line) for line in normalized_lines])
        line_bucket = int(round(profile.get("lines", 0) / 50.0) * 50)
        parts = [
            f"lines={line_bucket}",
            f"cbuffer={profile.get('cbuffer', 0)}",
            f"texture2d={profile.get('texture2d', 0)}",
            f"texture3d={profile.get('texture3d', 0)}",
            f"texturecube={profile.get('texturecube', 0)}",
            f"sampler={profile.get('sampler', 0)}",
            f"if={profile.get('if', 0)}",
            f"switch={profile.get('switch', 0)}",
            f"for={profile.get('for', 0)}",
            f"while={profile.get('while', 0)}",
            f"sample={profile.get('sample', 0)}",
            f"samplelevel={profile.get('samplelevel', 0)}",
            f"load={profile.get('load', 0)}",
            f"discard={profile.get('discard', 0)}",
        ]
        return "|".join(parts)

    def _effect_similarity(self, left: Dict[str, Any], right: Dict[str, Any]) -> float:
        left_text = "\n".join(left.get("commonPrefix", []) + left.get("commonSuffix", []) + [str(left.get("effectKey", ""))])
        right_text = "\n".join(right.get("commonPrefix", []) + right.get("commonSuffix", []) + [str(right.get("effectKey", ""))])
        return SequenceMatcher(None, left_text, right_text, autojunk=False).ratio()

    def _role_similarity(self, left: Dict[str, Any], right: Dict[str, Any]) -> float:
        left_text = "\n".join(left.get("commonPrefix", []) + left.get("commonSuffix", []) + [self._compress_role_key(str(left.get("effectKey", "")))])
        right_text = "\n".join(right.get("commonPrefix", []) + right.get("commonSuffix", []) + [self._compress_role_key(str(right.get("effectKey", "")))])
        return SequenceMatcher(None, left_text, right_text, autojunk=False).ratio()

    def _strip_effect_line_bucket(self, effect_key: str) -> str:
        parts = [part for part in effect_key.split("|") if not part.startswith("lines=")]
        return "|".join(parts)

    def _compress_role_key(self, effect_key: str) -> str:
        keep_prefixes = ("cbuffer=", "texture2d=", "texture3d=", "texturecube=", "sampler=", "sample=", "samplelevel=", "load=", "discard=")
        parts = [part for part in effect_key.split("|") if part.startswith(keep_prefixes)]
        return "|".join(parts)

    def _core_profile_key(self, core_lines: List[str]) -> str:
        profile = self._extract_hlsl_profile(core_lines)
        parts = [
            f"if={profile.get('if', 0)}",
            f"switch={profile.get('switch', 0)}",
            f"for={profile.get('for', 0)}",
            f"while={profile.get('while', 0)}",
            f"sample={profile.get('sample', 0)}",
            f"samplelevel={profile.get('samplelevel', 0)}",
            f"load={profile.get('load', 0)}",
            f"discard={profile.get('discard', 0)}",
        ]
        return "|".join(parts)

    def _core_similarity(self, left: List[str], right: List[str]) -> float:
        if not left or not right:
            return 0.0
        left_text = "\n".join(left[:180])
        right_text = "\n".join(right[:180])
        seq_ratio = SequenceMatcher(None, left_text, right_text, autojunk=False).ratio()
        left_set = set(left)
        right_set = set(right)
        union = left_set | right_set
        overlap = len(left_set & right_set) / len(union) if union else 1.0
        return (seq_ratio * 0.65) + (overlap * 0.35)

    def _family_similarity(self, left: List[str], right: List[str]) -> float:
        left_text = "\n".join(left)
        right_text = "\n".join(right)
        return SequenceMatcher(None, left_text, right_text, autojunk=False).ratio()

    def _common_prefix_lines(self, sequences: List[List[str]]) -> List[str]:
        if not sequences:
            return []
        prefix = list(sequences[0])
        for seq in sequences[1:]:
            i = 0
            max_i = min(len(prefix), len(seq))
            while i < max_i and prefix[i] == seq[i]:
                i += 1
            prefix = prefix[:i]
            if not prefix:
                break
        return prefix

    def _common_suffix_lines(self, sequences: List[List[str]]) -> List[str]:
        if not sequences:
            return []
        suffix = list(sequences[0])
        for seq in sequences[1:]:
            i = 0
            max_i = min(len(suffix), len(seq))
            while i < max_i and suffix[-(i + 1)] == seq[-(i + 1)]:
                i += 1
            suffix = suffix[len(suffix) - i:]
            if not suffix:
                break
        return suffix

    def _load_spirv_semantic_text(self, manifest_path: Path, spirv_source: Path) -> str:
        candidates: List[Path] = []
        for item in manifest_path.parent.joinpath("raw").glob("*.spvasm.txt"):
            candidates.append(item)
        for item in manifest_path.parent.joinpath("disassembly").glob("SPIR-V*.txt"):
            candidates.append(item)
        if not candidates:
            alt = spirv_source.with_suffix(".spvasm.txt")
            if alt.exists():
                candidates.append(alt)
        if candidates:
            text_path = sorted(candidates)[0]
            text = text_path.read_text(encoding="utf-8", errors="ignore")
            return self._normalize_spirv_disassembly(text)
        return self._normalize_spirv_disassembly(spirv_source.read_bytes().hex())

    def _normalize_spirv_disassembly(self, text: str) -> str:
        lines: List[str] = []
        for raw_line in text.splitlines():
            line = raw_line.split(";", 1)[0].strip()
            if not line:
                continue
            if line.startswith("OpName") or line.startswith("OpMemberName") or line.startswith("OpSource") or line.startswith("OpLine"):
                continue
            line = _SPIRV_ID_TOKEN.sub("%ID", line)
            line = re.sub(r"\s+", " ", line).strip()
            if line:
                lines.append(line)
        return "\n".join(lines)

    def _resolve_spirv_source_path(self, artifact: Dict[str, Any]) -> Optional[Path]:
        for key in ("rawBinaryFiles", "rawFiles"):
            for item in artifact.get(key, []) or []:
                path_text = str(item.get("path", "") or "").strip()
                if not path_text:
                    continue
                path = Path(path_text)
                if path.suffix.lower() == ".spv" or path.name.lower().endswith(".spv"):
                    return path
        return None

    def _run_spirv_cross(
        self,
        *,
        spirv_cross: Path,
        spirv_source: Path,
        stage_tag: str,
        entry_name: str,
        output_path: Path,
    ) -> None:
        stage_arg = _STAGE_TO_SPIRV_CROSS.get(stage_tag, "frag")
        cmd = [
            str(spirv_cross),
            str(spirv_source),
            "--hlsl",
            "--shader-model",
            "50",
            "--hlsl-preserve-structured-buffers",
            "--entry",
            entry_name,
            "--stage",
            stage_arg,
            "--output",
            str(output_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            details = [
                f"spirv-cross failed for {spirv_source}",
                f"stage={stage_tag}",
                f"entry={entry_name}",
                f"cmd={' '.join(cmd)}",
            ]
            if proc.stdout.strip():
                details.append("stdout:\n" + proc.stdout.strip())
            if proc.stderr.strip():
                details.append("stderr:\n" + proc.stderr.strip())
            raise RuntimeError("\n".join(details))

    def _build_graphics_shaderlab(self, *, shader_id: str, stage_tag: str, entry_name: str) -> str:
        pass_name = {
            "vs": "VertexPass",
            "ps": "ForwardBase",
            "gs": "GeometryPass",
            "hs": "HullPass",
            "ds": "DomainPass",
        }.get(stage_tag, "RenderPass")
        light_mode = {
            "vs": "BXForwardBase",
            "ps": "BXForwardBase",
            "gs": "BXForwardBase",
            "hs": "BXForwardBase",
            "ds": "BXForwardBase",
        }.get(stage_tag, "BXForwardBase")
        pragma_lines = [
            "#pragma target 5.0",
        ]
        if stage_tag == "vs":
            pragma_lines.extend(
                [
                    f"#pragma vertex {entry_name}",
                    "#pragma fragment rdc_passthrough_frag",
                ]
            )
        elif stage_tag == "ps":
            pragma_lines.extend(
                [
                    "#pragma vertex rdc_passthrough_vert",
                    f"#pragma fragment {entry_name}",
                ]
            )
        elif stage_tag == "gs":
            pragma_lines.extend(
                [
                    "#pragma vertex rdc_passthrough_vert",
                    f"#pragma geometry {entry_name}",
                    "#pragma fragment rdc_passthrough_frag",
                ]
            )
        elif stage_tag == "hs":
            pragma_lines.extend(
                [
                    "#pragma vertex rdc_passthrough_vert",
                    f"#pragma hull {entry_name}",
                    "#pragma fragment rdc_passthrough_frag",
                ]
            )
        elif stage_tag == "ds":
            pragma_lines.extend(
                [
                    "#pragma vertex rdc_passthrough_vert",
                    f"#pragma domain {entry_name}",
                    "#pragma fragment rdc_passthrough_frag",
                ]
            )
        else:
            pragma_lines.extend(
                [
                    "#pragma vertex rdc_passthrough_vert",
                    "#pragma fragment rdc_passthrough_frag",
                ]
            )

        wrapper = [
            f'Shader "RenderDoc/Transpiled/{_safe_name(shader_id)}"',
            "{",
            "    Properties",
            "    {",
            "    }",
            "    SubShader",
            "    {",
            '        Tags { "RenderType" = "Opaque" "Queue" = "Geometry+100" }',
            "        Pass",
            "        {",
            f'            Name "{pass_name}"',
            f'            Tags {{ "LightMode" = "{light_mode}" }}',
            "            Cull Off",
            "            ZWrite On",
            "            HLSLPROGRAM",
            *["            " + line for line in pragma_lines],
            '            #include "shader.hlsl"',
            "",
            "            struct rdc_appdata { float4 vertex : POSITION; };",
            "            struct rdc_v2f { float4 pos : SV_POSITION; };",
            "            rdc_v2f rdc_passthrough_vert(rdc_appdata v)",
            "            {",
            "                rdc_v2f o;",
            "                o.pos = v.vertex;",
            "                return o;",
            "            }",
            "            float4 rdc_passthrough_frag(rdc_v2f i) : SV_Target",
            "            {",
            "                return float4(0.0, 0.0, 0.0, 1.0);",
            "            }",
            "            ENDHLSL",
            "        }",
            "    }",
            "}",
        ]
        return "\n".join(wrapper) + "\n"

    def _build_compute_shaderlab(self, *, shader_id: str, stage_tag: str, entry_name: str) -> str:
        wrapper = [
            f"// Compute shader generated from {shader_id}",
            f"// Stage: {stage_tag}",
            f"// Entry: {entry_name}",
            "#pragma kernel main",
            "",
            '#include "shader.hlsl"',
        ]
        return "\n".join(wrapper)

    def _collect_shader_usage_with_qrenderdoc(
        self,
        qrenderdoc: Path,
        rdc_path: Path,
        output_dir: Path,
        *,
        progress_callback: Optional[Any] = None,
        progress_base: int = 0,
        progress_span: int = 10000,
        rdc_index: int = 1,
        rdc_total: int = 1,
    ) -> Dict[str, Any]:
        if not qrenderdoc.exists():
            raise ValueError(f"qrenderdoc.exe not found: {qrenderdoc}")
        if not rdc_path.exists():
            raise ValueError(f"rdc_path not found: {rdc_path}")
        if rdc_path.suffix.lower() != ".rdc":
            raise ValueError(f"rdc_path must be .rdc: {rdc_path}")

        run_dir = Path(tempfile.mkdtemp(prefix="renderdoc_workbench_shader_catalog_"))
        script_path = Path(__file__).with_name("shader_catalog_capture.py")
        runner_path = run_dir / "shader_catalog_runner.py"
        result_path = run_dir / "shader_catalog_result.json"
        log_path = run_dir / "shader_catalog.log"
        progress_path = run_dir / "shader_catalog.progress.jsonl"

        cfg = {
            "rdc_path": str(rdc_path),
            "output_dir": str(output_dir),
            "result_path": str(result_path),
            "log_path": str(log_path),
            "progress_path": str(progress_path),
            "collect_mode": "raw_spirv",
        }
        cfg_path = run_dir / "shader_catalog_config.json"
        cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        cfg_json = json.dumps(cfg, ensure_ascii=False)
        runner_script = f"""import importlib.util
import json
import traceback
from pathlib import Path

cfg = json.loads({json.dumps(cfg_json)})
helper_path = {json.dumps(str(script_path))}

try:
    spec = importlib.util.spec_from_file_location("shader_catalog_capture", helper_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load helper script: %s" % helper_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.run_from_config(cfg)
except Exception:
    try:
        payload = {{
            "rdc_path": cfg.get("rdc_path", ""),
            "open_ok": False,
            "pipeline": None,
            "draw_event_count": 0,
            "shader_count": 0,
            "disassembly_targets": [],
            "shaders": [],
            "errors": [traceback.format_exc()],
        }}
        Path(cfg["result_path"]).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
"""
        runner_path.write_text(runner_script, encoding="utf-8")

        proc = subprocess.Popen(
            [str(qrenderdoc), "--python", str(runner_path)],
            cwd=str(qrenderdoc.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=self._clean_qrenderdoc_env(),
        )
        deadline = time.time() + 240
        last_progress_line = ""
        last_emitted_done = -1
        last_emitted_text = ""
        emit_step = max(1, progress_span // 100)
        while time.time() < deadline:
            if result_path.exists():
                break
            if callable(progress_callback) and progress_path.exists():
                try:
                    content = progress_path.read_text(encoding="utf-8", errors="replace").strip()
                    if content:
                        lines = [line for line in content.splitlines() if line.strip()]
                        if lines:
                            last_line = lines[-1]
                            if last_line != last_progress_line:
                                last_progress_line = last_line
                                try:
                                    payload = json.loads(last_line)
                                    local_done = int(payload.get("done", 0) or 0)
                                    local_total = max(1, int(payload.get("total", 1) or 1))
                                    message = str(payload.get("message", "") or "")
                                    scaled_done = progress_base + int((local_done / local_total) * progress_span)
                                    scaled_total = max(rdc_total * progress_span, 1)
                                    if (
                                        scaled_done >= progress_base + progress_span
                                        or scaled_done - last_emitted_done >= emit_step
                                        or message != last_emitted_text
                                    ):
                                        last_emitted_done = scaled_done
                                        last_emitted_text = message
                                        progress_callback(scaled_done, scaled_total, message)
                                except Exception:
                                    pass
                except Exception:
                    pass
            if proc.poll() is not None:
                break
            time.sleep(0.2)
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

        if not result_path.exists():
            stdout = ""
            stderr = ""
            try:
                if proc.stdout is not None:
                    stdout = proc.stdout.read() or ""
            except Exception:
                stdout = ""
            try:
                if proc.stderr is not None:
                    stderr = proc.stderr.read() or ""
            except Exception:
                stderr = ""

            log_text = ""
            try:
                if log_path.exists():
                    log_text = log_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                log_text = ""

            details = [
                "shader catalog result file not produced",
                f"rdc={rdc_path}",
                f"runner={runner_path}",
                f"result={result_path}",
            ]
            if stdout.strip():
                details.append("stdout:\n" + stdout.strip())
            if stderr.strip():
                details.append("stderr:\n" + stderr.strip())
            if log_text.strip():
                details.append("log:\n" + log_text.strip())
            raise RuntimeError("\n".join(details))
        if callable(progress_callback) and progress_path.exists():
            try:
                content = progress_path.read_text(encoding="utf-8", errors="replace").strip()
                if content:
                    lines = [line for line in content.splitlines() if line.strip()]
                    if lines:
                        payload = json.loads(lines[-1])
                        local_done = int(payload.get("done", 0) or 0)
                        local_total = max(1, int(payload.get("total", 1) or 1))
                        message = str(payload.get("message", "") or "")
                        scaled_done = progress_base + int((local_done / local_total) * progress_span)
                        scaled_total = max(rdc_total * progress_span, 1)
                        progress_callback(scaled_done, scaled_total, message)
            except Exception:
                pass
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        if callable(progress_callback):
            try:
                progress_callback(progress_base + progress_span, max(rdc_total * progress_span, 1), f"完成: {rdc_path.name}")
            except Exception:
                pass
        return payload
