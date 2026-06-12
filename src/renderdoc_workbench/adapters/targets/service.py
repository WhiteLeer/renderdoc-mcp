"""Target session adapter for MuMu control and package launch."""

from __future__ import annotations

import os
import json
import subprocess
import time
from pathlib import Path
from typing import Iterable, List, Optional

from ...core.errors import ConfigurationError
from ...core.models import LaunchRequest, LaunchResult
from .._legacy_loader import load_legacy_renderdoc_server


class TargetSessionAdapter:
    """Owns MuMu opening, package discovery, and package launch orchestration."""

    _EMULATOR_NAMES = (
        "MuMuNxDevice.exe",
        "MuMuVMMHeadless.exe",
        "MuMuPlayer.exe",
        "MuMuLauncher.exe",
    )

    def _find_candidate(self, root: Path, names: Iterable[str]) -> Optional[Path]:
        if root.is_file():
            return root if root.name.lower() in {name.lower() for name in names} else None
        for name in names:
            for path in root.rglob(name):
                if path.is_file():
                    return path.resolve()
        return None

    def _find_adb(self, root: Path) -> Optional[Path]:
        candidates = []
        if root.is_file() and root.name.lower() == "adb.exe":
            return root.resolve()
        candidates.extend(
            [
                root / "adb.exe",
                root / "platform-tools" / "adb.exe",
                root / "adb" / "adb.exe",
            ]
        )
        for candidate in candidates:
            if candidate.exists():
                return candidate.resolve()
        for path in root.rglob("adb.exe"):
            if path.is_file():
                return path.resolve()
        return None

    def _find_vm_config_dir(self, root: Path) -> Optional[Path]:
        vms_root = root / "vms"
        if not vms_root.exists():
            return None
        candidates = []
        for shell_cfg in vms_root.glob("*/configs/shell_config.json"):
            candidates.append(shell_cfg.parent)
        if not candidates:
            return None
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return candidates[0]

    def get_render_backend(self, target_root: Path) -> str:
        config_dir = self._find_vm_config_dir(target_root)
        if config_dir is None:
            raise ConfigurationError(f"MuMu 配置目录未找到: {target_root}")
        shell_cfg = config_dir / "shell_config.json"
        customer_cfg = config_dir / "customer_config.json"
        backend = "DirectX"
        if shell_cfg.exists():
            try:
                shell_data = json.loads(shell_cfg.read_text(encoding="utf-8"))
                platform = str((shell_data.get("renderer") or {}).get("platform", "")).lower()
                if platform == "vulkan":
                    backend = "Vulkan"
                elif platform == "dx11":
                    backend = "DirectX"
            except Exception:
                pass
        if customer_cfg.exists():
            try:
                customer_data = json.loads(customer_cfg.read_text(encoding="utf-8"))
                choose = str((((customer_data.get("setting") or {}).get("render") or {}).get("mode") or {}).get("choose", ""))
                if choose == "render.mode.highperformance":
                    backend = "Vulkan"
                elif choose == "render.mode.stable" and backend not in ("Vulkan", "DirectX"):
                    backend = "DirectX"
            except Exception:
                pass
        return backend

    def set_render_backend(self, target_root: Path, backend: str) -> None:
        config_dir = self._find_vm_config_dir(target_root)
        if config_dir is None:
            raise ConfigurationError(f"MuMu 配置目录未找到: {target_root}")
        backend_norm = backend.strip().lower()
        if backend_norm not in {"directx", "vulkan"}:
            raise ConfigurationError(f"不支持的渲染后端: {backend}")

        shell_cfg = config_dir / "shell_config.json"
        customer_cfg = config_dir / "customer_config.json"
        if shell_cfg.exists():
            shell_data = json.loads(shell_cfg.read_text(encoding="utf-8"))
            shell_renderer = shell_data.setdefault("renderer", {})
            shell_renderer["platform"] = "vulkan" if backend_norm == "vulkan" else "dx11"
            shell_cfg.write_text(json.dumps(shell_data, ensure_ascii=False, indent=2), encoding="utf-8")

        if customer_cfg.exists():
            customer_data = json.loads(customer_cfg.read_text(encoding="utf-8"))
            render_mode = (((customer_data.setdefault("setting", {}).setdefault("render", {})).setdefault("mode", {})))
            if backend_norm == "vulkan":
                render_mode["choose"] = "render.mode.highperformance"
            else:
                render_mode["choose"] = "render.mode.stable"
            render_mode["highperformance"] = "Vulkan"
            render_mode["stable"] = "DirectX"
            customer_cfg.write_text(json.dumps(customer_data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _connect_common_mumu_ports(self, adb: Path) -> None:
        for address in ("127.0.0.1:7555", "127.0.0.1:5555"):
            subprocess.run([str(adb), "connect", address], capture_output=True, text=True)

    def _adb_serial(self, adb: Path) -> Optional[str]:
        self._connect_common_mumu_ports(adb)
        proc = subprocess.run([str(adb), "devices"], capture_output=True, text=True)
        if proc.returncode != 0:
            return None
        for line in (proc.stdout or "").splitlines():
            line = line.strip()
            if not line or line.startswith("List of devices"):
                continue
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                return parts[0]
        return None

    def _adb_shell(self, adb: Path, *args: str) -> subprocess.CompletedProcess[str]:
        serial = self._adb_serial(adb)
        cmd = [str(adb)]
        if serial:
            cmd.extend(["-s", serial])
        cmd.extend(["shell", *args])
        return subprocess.run(cmd, capture_output=True, text=True)

    def _wait_for_pid(self, process_name: str, timeout_sec: float = 60.0) -> Optional[int]:
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            proc = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {process_name}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
            )
            for line in (proc.stdout or "").splitlines():
                line = line.strip().strip('"')
                if not line or process_name.lower() not in line.lower():
                    continue
                parts = [part.strip().strip('"') for part in line.split('","')]
                if len(parts) >= 2:
                    try:
                        return int(parts[1])
                    except ValueError:
                        continue
            time.sleep(1.0)
        return None

    def discover_packages(self, target_root: Path) -> Iterable[str]:
        if not target_root.exists():
            raise ConfigurationError(f"Target root does not exist: {target_root}")
        adb = self._find_adb(target_root)
        if adb is None:
            raise ConfigurationError(f"adb.exe not found under: {target_root}")
        self._connect_common_mumu_ports(adb)
        serial = self._adb_serial(adb)
        cmd = [str(adb)]
        if serial:
            cmd.extend(["-s", serial])
        cmd.extend(["shell", "pm", "list", "packages", "-3"])
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise ConfigurationError(proc.stderr.strip() or "Unable to query installed packages")
        packages: List[str] = []
        for line in (proc.stdout or "").splitlines():
            line = line.strip()
            if line.startswith("package:"):
                packages.append(line.split("package:", 1)[1].strip())
        return sorted(dict.fromkeys(packages))

    def launch_and_attach(self, request: LaunchRequest) -> LaunchResult:
        if not request.target_root.exists():
            raise ConfigurationError(f"Target root does not exist: {request.target_root}")

        if not request.package_name:
            return self._open_and_inject_mumu(request.target_root)
        return self._run_package(request.target_root, request.package_name)

    def _open_and_inject_mumu(self, target_root: Path) -> LaunchResult:
        emulator = self._find_candidate(target_root, self._EMULATOR_NAMES)
        if emulator is None:
            raise ConfigurationError(f"MuMu launcher not found under: {target_root}")

        subprocess.Popen([str(emulator)], cwd=str(emulator.parent))
        vmm_pid = self._wait_for_pid("MuMuVMMHeadless.exe", timeout_sec=90.0)
        if vmm_pid is None:
            raise ConfigurationError("MuMuVMMHeadless.exe did not appear in time")

        legacy = load_legacy_renderdoc_server()
        capture_output = target_root.parent / "captures" / f"mumu_attach_{time.strftime('%Y%m%d_%H%M%S')}_{'{timestamp}'}.rdc"
        payload = legacy._capture_game(
            {
                "capture_mode": "attach",
                "target_pid": int(vmm_pid),
                "target_process_name": "MuMuVMMHeadless.exe",
                "capture_output": str(capture_output),
                "auto_trigger": False,
                "trigger_backend": "hotkey",
                "trigger_delay_sec": 0,
                "allow_focus_hotkey": False,
                "wait_for_exit": False,
                "open_in_qrenderdoc": False,
                "capture_callstacks": False,
                "capture_callstacks_only_actions": False,
                "ref_all_resources": False,
                "capture_all_cmd_lists": False,
                "verify_buffer_access": False,
                "hook_children": True,
                "emulator_profile": "mumu",
            }
        )
        message = str(payload.get("trigger_note") or payload.get("stdout") or "MuMu 已打开并注入。")
        if payload.get("latest_capture"):
            message = f"{message} latest_capture={payload.get('latest_capture')}"
        message = f"{message}; vmm_pid={vmm_pid}"
        return LaunchResult(process_id=int(vmm_pid), attached=True, message=message)

    def _run_package(self, target_root: Path, package_name: str) -> LaunchResult:
        adb = self._find_adb(target_root)
        if adb is None:
            raise ConfigurationError(f"adb.exe not found under: {target_root}")

        self._connect_common_mumu_ports(adb)
        serial = self._adb_serial(adb)
        cmd = [str(adb)]
        if serial:
            cmd.extend(["-s", serial])
        cmd.extend(
            [
                "shell",
                "monkey",
                "-p",
                package_name,
                "-c",
                "android.intent.category.LAUNCHER",
                "1",
            ]
        )
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise ConfigurationError(proc.stderr.strip() or proc.stdout.strip() or "Package launch failed")
        message = proc.stdout.strip() or f"已运行 {package_name}"
        return LaunchResult(process_id=None, attached=True, message=message)
