"""qrenderdoc-side shader catalog capture script."""

import json
import os
import re
import traceback
from pathlib import Path


def _safe_name(value: object) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", text)
    text = text.strip("._")
    return text or "item"


def _append_unique(items, value, limit=None):
    if value is None:
        return
    text = str(value).strip()
    if not text:
        return
    if text not in items and (limit is None or len(items) < limit):
        items.append(text)


_STAGE_LABELS = {
    "vs": "顶点",
    "hs": "Hull",
    "ds": "Domain",
    "gs": "几何",
    "ps": "像素",
    "cs": "计算",
}

_STAGE_ORDER = ["vs", "hs", "ds", "gs", "ps", "cs"]


def _stage_label(stage_tag):
    return _STAGE_LABELS.get(str(stage_tag), str(stage_tag))


def _stage_summary(stage_counts):
    items = []
    for stage_tag in _STAGE_ORDER:
        count = int((stage_counts or {}).get(stage_tag, 0) or 0)
        if count > 0:
            items.append(_stage_label(stage_tag))
    if not items:
        return "未知阶段"
    return "、".join(items)


def _build_effect_tags(shader_row):
    tags = []
    stage_counts = shader_row.get("stageCounts", {}) or {}
    for stage_tag in _STAGE_ORDER:
        if int(stage_counts.get(stage_tag, 0) or 0) > 0:
            _append_unique(tags, "stage:%s" % stage_tag)
    if int(shader_row.get("usageCount", 0) or 0) > 0:
        _append_unique(tags, "usage:%d" % int(shader_row.get("usageCount", 0) or 0))
    if shader_row.get("firstStage"):
        _append_unique(tags, "first_stage:%s" % shader_row.get("firstStage"))
    if shader_row.get("firstEventName"):
        _append_unique(tags, "first_event:%s" % shader_row.get("firstEventName"))
    if shader_row.get("entrySourceName"):
        _append_unique(tags, "entry_source:%s" % shader_row.get("entrySourceName"))
    if shader_row.get("sourceDebugInformation"):
        _append_unique(tags, "source_debug:yes")
    if int(shader_row.get("sourceFileCount", 0) or 0) > 0:
        _append_unique(tags, "source_files:%d" % int(shader_row.get("sourceFileCount", 0) or 0))
    for name in shader_row.get("boundResourceNames", []) or []:
        _append_unique(tags, "resource:%s" % name)
    for name in shader_row.get("constantBlockNames", []) or []:
        _append_unique(tags, "constant:%s" % name)
    return tags


def _build_effect_description(shader_row):
    stage_counts = shader_row.get("stageCounts", {}) or {}
    stage_summary = _stage_summary(stage_counts)
    usage_count = int(shader_row.get("usageCount", 0) or 0)
    first_event = str(shader_row.get("firstEventName", "") or "")
    first_stage = str(shader_row.get("firstStage", "") or "")
    entry_source_name = str(shader_row.get("entrySourceName", "") or "")
    bound_count = len(shader_row.get("boundResourceNames", []) or [])
    block_count = len(shader_row.get("constantBlockNames", []) or [])
    source_file_count = int(shader_row.get("sourceFileCount", 0) or 0)

    if len([stage for stage, count in stage_counts.items() if int(count or 0) > 0]) <= 1:
        parts = ["%s阶段着色器" % stage_summary]
    else:
        parts = ["多阶段着色器（%s）" % stage_summary]
    if usage_count > 0:
        parts.append("复用 %d 次" % usage_count)
    if first_event:
        parts.append("首次出现在 %s" % first_event)
    if first_stage:
        parts.append("首个阶段 %s" % _stage_label(first_stage))
    if bound_count > 0:
        parts.append("绑定 %d 个只读资源" % bound_count)
    if block_count > 0:
        parts.append("包含 %d 个常量块" % block_count)
    if entry_source_name:
        parts.append("入口源名 %s" % entry_source_name)
    if bool(shader_row.get("sourceDebugInformation", False)):
        if source_file_count > 0:
            parts.append("含 %d 个源文件" % source_file_count)
        else:
            parts.append("含源文件调试信息")
    else:
        parts.append("无源文件调试信息")
    return "；".join(parts)


def _write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _emit_progress(progress_path, done, total, message):
    if not progress_path:
        return
    payload = {
        "done": int(done),
        "total": int(total),
        "message": str(message or ""),
    }
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    with progress_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        f.flush()


def _collect_shader_artifact(ctrl, state, refl, pipeline, shader_row, capture_dir, rdc_stem, target_list, rmap):
    shader_id = shader_row["shaderId"]
    shader_dir = capture_dir / "shaders" / _safe_name(rdc_stem) / _safe_name(shader_id)
    disasm_dir = shader_dir / "disassembly"
    source_dir = shader_dir / "source"
    raw_dir = shader_dir / "raw"
    shader_dir.mkdir(parents=True, exist_ok=True)

    di = getattr(refl, "debugInfo", None)
    source_debug = bool(getattr(di, "sourceDebugInformation", False))
    entry_source_name = str(getattr(di, "entrySourceName", "") or "")
    compiler = str(getattr(di, "compiler", "") or "")
    debug_status = str(getattr(di, "debugStatus", "") or "")

    artifact = {
        "shaderId": shader_id,
        "artifactDir": str(shader_dir),
        "rdcStem": rdc_stem,
        "collectionMode": shader_row.get("collectionMode", "source_only"),
        "stageCounts": shader_row.get("stageCounts", {}),
        "entryPoints": shader_row.get("entryPoints", {}),
        "firstEventId": shader_row.get("firstEventId"),
        "firstEventName": shader_row.get("firstEventName"),
        "firstStage": shader_row.get("firstStage"),
        "firstPipelineObject": shader_row.get("firstPipelineObject"),
        "debuggable": bool(getattr(di, "debuggable", False)),
        "sourceDebugInformation": source_debug,
        "entrySourceName": entry_source_name,
        "compiler": compiler,
        "debugStatus": debug_status,
        "effectDescription": shader_row.get("effectDescription", ""),
        "effectTags": shader_row.get("effectTags", []),
        "actionNames": shader_row.get("actionNames", []),
        "boundResourceNames": shader_row.get("boundResourceNames", []),
        "constantBlockNames": shader_row.get("constantBlockNames", []),
        "rawTargets": [],
        "rawFiles": [],
        "rawBinaryTargets": [],
        "rawBinaryFiles": [],
        "disassemblyTargets": [],
        "disassemblyFiles": [],
        "sourceFiles": [],
    }

    collect_mode = str(shader_row.get("collectionMode", "source_only") or "source_only")
    if collect_mode == "raw_spirv":
        selected_targets = [str(target) for target in target_list if "SPIR-V" in str(target)]
        if not selected_targets and target_list:
            selected_targets = [str(target_list[0])]
        for target in selected_targets[:1]:
            try:
                text = ctrl.DisassembleShader(pipeline, refl, target)
            except Exception as exc:
                artifact.setdefault("disassemblyErrors", []).append({"target": str(target), "error": str(exc)})
                continue
            out_path = raw_dir / ("%s.spvasm.txt" % _safe_name(target))
            _write_text(out_path, text)
            artifact["rawTargets"].append(str(target))
            artifact["rawFiles"].append({
                "target": str(target),
                "path": str(out_path),
                "size": len(text),
            })
            try:
                raw_bytes = bytes(getattr(refl, "rawBytes", b"") or b"")
            except Exception:
                raw_bytes = b""
            if raw_bytes:
                bin_path = raw_dir / ("%s.spv" % _safe_name(target))
                bin_path.parent.mkdir(parents=True, exist_ok=True)
                bin_path.write_bytes(raw_bytes)
                artifact["rawBinaryTargets"].append(str(target))
                artifact["rawBinaryFiles"].append({
                    "target": str(target),
                    "path": str(bin_path),
                    "size": len(raw_bytes),
                })
    elif collect_mode != "source_only":
        selected_targets = list(target_list)
        for target in selected_targets:
            try:
                text = ctrl.DisassembleShader(pipeline, refl, target)
            except Exception as exc:
                artifact.setdefault("disassemblyErrors", []).append({"target": str(target), "error": str(exc)})
                continue
            out_path = disasm_dir / ("%s.txt" % _safe_name(target))
            _write_text(out_path, text)
            artifact["disassemblyTargets"].append(str(target))
            artifact["disassemblyFiles"].append({
                "target": str(target),
                "path": str(out_path),
                "size": len(text),
            })

    if source_debug:
        files = list(getattr(di, "files", []) or [])
        for index, src_file in enumerate(files):
            try:
                filename = str(getattr(src_file, "filename", "") or "")
                contents = str(getattr(src_file, "contents", "") or "")
            except Exception:
                continue
            safe_file = _safe_name(Path(filename).name if filename else "source_%02d" % index)
            out_path = source_dir / ("%02d_%s" % (index, safe_file))
            _write_text(out_path, contents)
            artifact["sourceFiles"].append({
                "filename": filename,
                "path": str(out_path),
                "size": len(contents),
            })

    manifest_path = shader_dir / "shader.manifest.json"
    _write_text(manifest_path, json.dumps(artifact, ensure_ascii=False, indent=2))
    shader_row["artifactDir"] = str(shader_dir)
    shader_row["artifactManifest"] = str(manifest_path)
    shader_row["sourceDebugInformation"] = source_debug
    shader_row["entrySourceName"] = entry_source_name
    shader_row["compiler"] = compiler
    shader_row["debugStatus"] = debug_status
    shader_row["rawTargets"] = artifact["rawTargets"]
    shader_row["rawFiles"] = artifact["rawFiles"]
    shader_row["rawBinaryTargets"] = artifact["rawBinaryTargets"]
    shader_row["rawBinaryFiles"] = artifact["rawBinaryFiles"]
    shader_row["disassemblyTargets"] = artifact["disassemblyTargets"]
    shader_row["sourceFileCount"] = len(artifact["sourceFiles"])


def _get_pipeline_object(state):
    pipeline = None
    getter = getattr(state, "GetGraphicsPipelineObject", None)
    if getter is not None:
        try:
            pipeline = getter()
        except Exception:
            pipeline = None
    if pipeline is None or str(pipeline) in ("ResourceId::0", "0"):
        getter = getattr(state, "GetComputePipelineObject", None)
        if getter is not None:
            try:
                pipeline = getter()
            except Exception:
                pipeline = pipeline
    return pipeline


def run_from_config(cfg):
    import renderdoc as rd

    rdc_path = cfg["rdc_path"]
    output_dir = Path(cfg["output_dir"])
    result_path = Path(cfg["result_path"])
    log_path = Path(cfg["log_path"])
    progress_path = Path(cfg["progress_path"]) if cfg.get("progress_path") else None
    rdc_stem = Path(rdc_path).stem

    def _log(msg):
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(msg + "\n")

    res = {
        "rdc_path": rdc_path,
        "open_ok": False,
        "pipeline": None,
        "draw_event_count": 0,
        "shader_count": 0,
        "disassembly_targets": [],
        "collection_mode": str(cfg.get("collect_mode", "raw_spirv") or "raw_spirv"),
        "shaders": [],
        "errors": [],
    }

    cap = None
    ctrl = None
    try:
        cap = rd.OpenCaptureFile()
        status = cap.OpenFile(rdc_path, "", None)
        if status != rd.ResultCode.Succeeded:
            raise RuntimeError("OpenFile failed: %s" % status)

        status2, ctrl = cap.OpenCapture(rd.ReplayOptions(), None)
        if status2 != rd.ResultCode.Succeeded:
            raise RuntimeError("OpenCapture failed: %s" % status2)

        res["open_ok"] = True
        res["pipeline"] = str(ctrl.GetAPIProperties().pipelineType)
        try:
            res["disassembly_targets"] = [str(x) for x in ctrl.GetDisassemblyTargets(True)]
        except Exception:
            res["disassembly_targets"] = []

        resource_map = {}
        try:
            for resource in ctrl.GetResources():
                resource_map[str(resource.resourceId)] = str(resource.name)
        except Exception:
            resource_map = {}

        shader_rows = {}
        queue = list(ctrl.GetRootActions())
        draw_actions = []
        while queue:
            action = queue.pop(0)
            queue.extend(list(action.children))

            flags = int(action.flags)
            is_draw = bool(flags & int(rd.ActionFlags.Drawcall))
            is_dispatch = bool(getattr(rd.ActionFlags, "Dispatch", 0) and (flags & int(getattr(rd.ActionFlags, "Dispatch"))))
            if not (is_draw or is_dispatch):
                continue
            draw_actions.append(action)

        total_steps = max(1, len(draw_actions) * 6)
        current_step = 0
        _emit_progress(progress_path, 0, total_steps, "开始扫描着色器")

        for action in draw_actions:
            event_id = int(action.eventId)
            try:
                action_name = str(action.customName)
            except Exception:
                action_name = ""
            if not action_name:
                try:
                    action_name = str(action.GetName(ctrl.GetStructuredFile()))
                except Exception:
                    action_name = ""

            res["draw_event_count"] += 1
            ctrl.SetFrameEvent(event_id, True)
            state = ctrl.GetPipelineState()
            pipeline = _get_pipeline_object(state)
            stage_specs = [
                ("vs", rd.ShaderStage.Vertex),
                ("hs", rd.ShaderStage.Hull),
                ("ds", rd.ShaderStage.Domain),
                ("gs", rd.ShaderStage.Geometry),
                ("ps", rd.ShaderStage.Pixel),
                ("cs", rd.ShaderStage.Compute),
            ]
            for stage_index, (stage_tag, stage_enum) in enumerate(stage_specs, start=1):
                current_step += 1
                try:
                    shader_id = str(state.GetShader(stage_enum))
                except Exception:
                    _emit_progress(
                        progress_path,
                        current_step,
                        total_steps,
                        "事件 %d/%d: %s" % (res["draw_event_count"], len(draw_actions), action_name),
                    )
                    continue
                if not shader_id or shader_id in ("0", "ResourceId::0"):
                    _emit_progress(
                        progress_path,
                        current_step,
                        total_steps,
                        "事件 %d/%d: %s" % (res["draw_event_count"], len(draw_actions), action_name),
                    )
                    continue

                try:
                    entry_point = str(state.GetShaderEntryPoint(stage_enum))
                except Exception:
                    entry_point = ""

                row = shader_rows.get(shader_id)
                if row is None:
                    row = {
                        "shaderId": shader_id,
                        "usageCount": 0,
                        "stageCounts": {},
                        "entryPoints": {},
                        "sampleEvents": [],
                        "actionNames": [],
                        "boundResourceNames": [],
                        "constantBlockNames": [],
                        "effectTags": [],
                        "effectDescription": "",
                        "firstEventId": event_id,
                        "firstEventName": action_name,
                        "firstStage": stage_tag,
                        "firstPipelineObject": str(pipeline),
                        "artifactDir": "",
                        "artifactManifest": "",
                        "sourceDebugInformation": False,
                        "entrySourceName": "",
                        "compiler": "",
                        "debugStatus": "",
        "rawTargets": [],
        "rawFiles": [],
        "rawBinaryTargets": [],
        "rawBinaryFiles": [],
        "disassemblyTargets": [],
        "sourceFileCount": 0,
        "collectionMode": str(res["collection_mode"]),
                    }
                    shader_rows[shader_id] = row

                row["usageCount"] += 1
                row["stageCounts"][stage_tag] = int(row["stageCounts"].get(stage_tag, 0)) + 1
                row["entryPoints"].setdefault(stage_tag, [])
                if entry_point and entry_point not in row["entryPoints"][stage_tag]:
                    row["entryPoints"][stage_tag].append(entry_point)
                if len(row["sampleEvents"]) < 8:
                    row["sampleEvents"].append({"eventId": event_id, "name": action_name})
                _append_unique(row["actionNames"], action_name, 12)

                try:
                    refl = state.GetShaderReflection(stage_enum)
                except Exception:
                    refl = None
                if refl is None:
                    continue

                try:
                    di = refl.debugInfo
                    row["sourceDebugInformation"] = bool(getattr(di, "sourceDebugInformation", False))
                    row["entrySourceName"] = str(getattr(di, "entrySourceName", "") or row["entrySourceName"])
                    row["compiler"] = str(getattr(di, "compiler", "") or row["compiler"])
                    row["debugStatus"] = str(getattr(di, "debugStatus", "") or row["debugStatus"])
                except Exception:
                    pass

                try:
                    for block in refl.constantBlocks:
                        _append_unique(row["constantBlockNames"], getattr(block, "name", ""), 12)
                except Exception:
                    pass

                try:
                    for resource in refl.readOnlyResources:
                        _append_unique(row["boundResourceNames"], getattr(resource, "name", ""), 12)
                except Exception:
                    pass

                try:
                    row["effectTags"] = _build_effect_tags(row)
                    row["effectDescription"] = _build_effect_description(row)
                except Exception:
                    pass

                if not row["artifactManifest"]:
                    try:
                        _collect_shader_artifact(
                            ctrl,
                            state,
                            refl,
                            pipeline,
                            row,
                            output_dir,
                            rdc_stem,
                            res["disassembly_targets"],
                            resource_map,
                        )
                    except Exception as exc:
                        res["errors"].append("shader %s artifact: %s" % (shader_id, exc))
                _emit_progress(
                    progress_path,
                    current_step,
                    total_steps,
                    "事件 %d/%d: %s / %s" % (res["draw_event_count"], len(draw_actions), action_name, stage_tag),
                )

        res["shader_count"] = len(shader_rows)
        for shader_row in shader_rows.values():
            manifest_path = str(shader_row.get("artifactManifest", "") or "")
            if not manifest_path:
                continue
            try:
                manifest_file = Path(manifest_path)
                if not manifest_file.exists():
                    continue
                artifact = json.loads(manifest_file.read_text(encoding="utf-8"))
                artifact["effectDescription"] = shader_row.get("effectDescription", "")
                artifact["effectTags"] = shader_row.get("effectTags", [])
                artifact["rawTargets"] = shader_row.get("rawTargets", [])
                artifact["rawFiles"] = shader_row.get("rawFiles", [])
                artifact["rawBinaryTargets"] = shader_row.get("rawBinaryTargets", [])
                artifact["rawBinaryFiles"] = shader_row.get("rawBinaryFiles", [])
                _write_text(manifest_file, json.dumps(artifact, ensure_ascii=False, indent=2))
            except Exception as exc:
                res["errors"].append("shader %s manifest refresh: %s" % (shader_row.get("shaderId", ""), exc))
        res["shaders"] = sorted(shader_rows.values(), key=lambda x: (int(x.get("usageCount", 0)), str(x.get("shaderId", ""))), reverse=True)
        _emit_progress(progress_path, total_steps, total_steps, "完成")
        _write_text(result_path, json.dumps(res, ensure_ascii=False, indent=2))
        _log("[shader_catalog] success: %d shaders" % res["shader_count"])
    except Exception:
        res["errors"].append(traceback.format_exc())
        _write_text(result_path, json.dumps(res, ensure_ascii=False, indent=2))
        _log("[shader_catalog] failed")
    finally:
        try:
            if ctrl is not None:
                ctrl.Shutdown()
        except Exception:
            pass
        try:
            if cap is not None:
                cap.Shutdown()
        except Exception:
            pass


def main():
    cfg_path = os.environ.get("RDC_SHADER_CATALOG_CFG", "")
    if not cfg_path:
        raise RuntimeError("RDC_SHADER_CATALOG_CFG is not set")
    cfg = json.loads(Path(cfg_path).read_text(encoding="utf-8"))
    run_from_config(cfg)


if __name__ == "__main__":
    main()
