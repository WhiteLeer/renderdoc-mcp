"""Main window for renderdoc_workbench."""

from __future__ import annotations

import traceback
from pathlib import Path
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QPlainTextEdit,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..core.models import AnalysisSummary, SessionState
from ..core.services import ServiceRegistry
from ..config.defaults import DEFAULT_OUTPUT_DIR, DEFAULT_RDC_DIR
from ..workflows.analysis import AnalysisWorkflow
from ..workflows.export import ExportWorkflow
from ..workflows.launch import LaunchWorkflow
from ..workflows.rdc_browser import RdcBrowserWorkflow
from ..workflows.shader_catalog import ShaderCatalogWorkflow
from ..workflows.shader_transpile import ShaderTranspileWorkflow


def _group(title: str) -> QGroupBox:
    box = QGroupBox(title)
    box.setFlat(False)
    return box


DEFAULT_MUMU_DIR = Path(r"C:\Program Files\NetEase\MuMu")


class _BackgroundWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, task: Callable[[Callable[[int, int, str], None]], Any]) -> None:
        super().__init__()
        self._task = task

    def run(self) -> None:
        try:
            result = self._task(self.progress.emit)
        except Exception:
            self.failed.emit(traceback.format_exc())
            return
        self.finished.emit(result)


class MainWindow(QMainWindow):
    """Operational GUI for target control and RDC analysis."""

    def __init__(self, services: ServiceRegistry) -> None:
        super().__init__()
        self._services = services
        self._launch_workflow = LaunchWorkflow(services)
        self._rdc_workflow = RdcBrowserWorkflow(services)
        self._analysis_workflow = AnalysisWorkflow(services)
        self._export_workflow = ExportWorkflow(services)
        self._shader_catalog_workflow = ShaderCatalogWorkflow(services)
        self._shader_transpile_workflow = ShaderTranspileWorkflow(services)
        self._state = SessionState()
        self._current_summary: Optional[AnalysisSummary] = None
        self._task_thread: Optional[QThread] = None
        self._task_worker: Optional[_BackgroundWorker] = None
        self._task_running = False
        self._task_title: str = ""
        self._task_on_success: Optional[Callable[[Any], None]] = None
        self._build_ui()
        self._refresh_render_backend()
        self._sync_state()

    def _build_ui(self) -> None:
        self.setWindowTitle("RenderDoc 工作台")
        self.resize(1380, 900)

        root = QWidget(self)
        outer = QVBoxLayout(root)

        header = QHBoxLayout()
        self._title_label = QLabel("RenderDoc 工作台")
        self._status_label = QLabel("空闲")
        self._status_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        header.addWidget(self._title_label)
        header.addStretch(1)
        header.addWidget(self._status_label)
        outer.addLayout(header)

        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self._build_body())
        splitter.addWidget(self._build_log_panel())
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)
        outer.addWidget(splitter)

        self.setCentralWidget(root)

    def _build_body(self) -> QWidget:
        body = QWidget(self)
        layout = QGridLayout(body)
        layout.setColumnStretch(0, 3)
        layout.setColumnStretch(1, 4)
        layout.setColumnStretch(2, 3)

        layout.addWidget(self._build_target_panel(), 0, 0)
        layout.addWidget(self._build_rdc_panel(), 0, 1)
        layout.addWidget(self._build_state_panel(), 0, 2)
        return body

    def _build_target_panel(self) -> QGroupBox:
        box = _group("目标控制")
        layout = QVBoxLayout(box)

        self.target_root_edit = QLineEdit()
        self.target_root_edit.setPlaceholderText(r"C:\Program Files\NetEase\MuMu")
        if DEFAULT_MUMU_DIR.exists():
            self.target_root_edit.setText(str(DEFAULT_MUMU_DIR))
        self.package_filter_edit = QLineEdit()
        self.package_filter_edit.setPlaceholderText("筛选包名 / exe 名称")
        self.package_list = QListWidget()
        self.package_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.package_list.currentItemChanged.connect(self._on_package_selected)
        self.target_root_edit.textChanged.connect(self._sync_state)
        self.package_filter_edit.textChanged.connect(self._filter_packages)

        browse_row = QHBoxLayout()
        browse_target_btn = QPushButton("选择目标目录")
        browse_target_btn.clicked.connect(self._browse_target_root)
        refresh_pkg_btn = QPushButton("刷新包列表")
        refresh_pkg_btn.clicked.connect(self._refresh_packages)
        browse_row.addWidget(browse_target_btn)
        browse_row.addWidget(refresh_pkg_btn)

        self.package_path_edit = QLineEdit()
        self.package_path_edit.setPlaceholderText("已选包名")

        self.render_backend_combo = QComboBox()
        self.render_backend_combo.addItems(["DirectX", "Vulkan"])
        self.render_backend_combo.currentTextChanged.connect(self._on_render_backend_changed)
        self.apply_render_backend_button = QPushButton("应用渲染后端")
        self.apply_render_backend_button.clicked.connect(self._apply_render_backend)

        self.launch_button = QPushButton("打开并注入 MuMu")
        self.launch_button.clicked.connect(self._launch_target)
        self.run_button = QPushButton("运行所选包")
        self.run_button.clicked.connect(self._run_selected_package)

        layout.addWidget(QLabel("MuMu 路径"))
        layout.addWidget(self.target_root_edit)
        layout.addLayout(browse_row)
        layout.addWidget(QLabel("包过滤"))
        layout.addWidget(self.package_filter_edit)
        layout.addWidget(QLabel("包列表"))
        layout.addWidget(self.package_list, 1)
        layout.addWidget(QLabel("当前包"))
        layout.addWidget(self.package_path_edit)
        layout.addWidget(QLabel("渲染后端"))
        render_row = QHBoxLayout()
        render_row.addWidget(self.render_backend_combo)
        render_row.addWidget(self.apply_render_backend_button)
        layout.addLayout(render_row)
        layout.addWidget(self.launch_button)
        layout.addWidget(self.run_button)
        return box

    def _build_rdc_panel(self) -> QGroupBox:
        box = _group("RDC 浏览")
        layout = QVBoxLayout(box)

        self.rdc_root_edit = QLineEdit()
        self.rdc_root_edit.setPlaceholderText(r"C:\Users\wepie\Desktop\RenderDoc-mcp\captures")
        self.rdc_root_edit.setText(str(DEFAULT_RDC_DIR))
        self.rdc_root_edit.textChanged.connect(self._sync_state)

        self.output_root_edit = QLineEdit()
        self.output_root_edit.setPlaceholderText(r"C:\Users\wepie\Desktop\RenderDoc-mcp\analysis")
        self.output_root_edit.setText(str(DEFAULT_OUTPUT_DIR))

        root_btn_row = QHBoxLayout()
        browse_rdc_btn = QPushButton("选择 RDC 目录")
        browse_rdc_btn.clicked.connect(self._browse_rdc_root)
        refresh_rdc_btn = QPushButton("刷新 RDC 列表")
        refresh_rdc_btn.clicked.connect(self._refresh_rdc_entries)
        root_btn_row.addWidget(browse_rdc_btn)
        root_btn_row.addWidget(refresh_rdc_btn)

        self.rdc_list = QListWidget()
        self.rdc_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.rdc_list.currentItemChanged.connect(self._on_rdc_selected)

        action_row = QHBoxLayout()
        self.open_button = QPushButton("打开 RDC")
        self.open_button.clicked.connect(self._open_selected_rdc)
        self.analyze_button = QPushButton("分析 RDC")
        self.analyze_button.clicked.connect(self._analyze_selected_rdc)
        self.focus_button = QPushButton("聚焦事件")
        self.focus_button.clicked.connect(self._focus_selected_event)
        self.export_button = QPushButton("导出摘要")
        self.export_button.clicked.connect(self._export_summary)
        self.shader_button = QPushButton("收集目录 Shader")
        self.shader_button.clicked.connect(self._collect_shader_catalog)
        self.transpile_button = QPushButton("反汇编为 HLSL")
        self.transpile_button.clicked.connect(self._transpile_shader_catalog)
        self._shader_task_buttons = [
            self.open_button,
            self.analyze_button,
            self.focus_button,
            self.export_button,
            self.shader_button,
            self.transpile_button,
        ]
        action_row.addWidget(self.open_button)
        action_row.addWidget(self.analyze_button)
        action_row.addWidget(self.focus_button)
        action_row.addWidget(self.export_button)
        action_row.addWidget(self.shader_button)
        action_row.addWidget(self.transpile_button)

        self.shader_progress_label = QLabel("就绪")
        self.shader_progress_bar = QProgressBar()
        self.shader_progress_bar.setVisible(False)
        self.shader_progress_bar.setRange(0, 1)
        self.shader_progress_bar.setValue(0)
        self._shader_progress_total = 1

        self.event_id_edit = QLineEdit()
        self.event_id_edit.setPlaceholderText("可选事件 ID")
        self.event_id_edit.setMaximumWidth(180)

        self.analysis_preview = QPlainTextEdit()
        self.analysis_preview.setReadOnly(True)
        self.analysis_preview.setPlaceholderText("分析摘要会显示在这里。")

        layout.addWidget(QLabel("RDC 目录"))
        layout.addWidget(self.rdc_root_edit)
        layout.addLayout(root_btn_row)
        layout.addWidget(QLabel("RDC 文件"))
        layout.addWidget(self.rdc_list, 1)
        layout.addWidget(QLabel("分析输出目录"))
        layout.addWidget(self.output_root_edit)
        layout.addWidget(QLabel("事件 ID"))
        layout.addWidget(self.event_id_edit)
        layout.addLayout(action_row)
        layout.addWidget(self.shader_progress_label)
        layout.addWidget(self.shader_progress_bar)
        layout.addWidget(QLabel("分析预览"))
        layout.addWidget(self.analysis_preview, 1)
        return box

    def _build_state_panel(self) -> QGroupBox:
        box = _group("会话状态")
        layout = QFormLayout(box)

        self.renderdoc_state_label = QLabel("未知")
        self.target_state_label = QLabel("无")
        self.package_state_label = QLabel("无")
        self.rdc_state_label = QLabel("无")
        self.output_state_label = QLabel("无")
        self.result_state_label = QLabel("无")

        layout.addRow("RenderDoc", self.renderdoc_state_label)
        layout.addRow("目标", self.target_state_label)
        layout.addRow("当前包", self.package_state_label)
        layout.addRow("当前 RDC", self.rdc_state_label)
        layout.addRow("输出", self.output_state_label)
        layout.addRow("上次结果", self.result_state_label)
        layout.addRow(QLabel(""), QLabel(""))
        return box

    def _build_log_panel(self) -> QWidget:
        box = QWidget(self)
        layout = QVBoxLayout(box)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("日志、结果和错误会显示在这里。")
        layout.addWidget(self.log_view)
        return box

    def _browse_target_root(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select target root", self.target_root_edit.text() or str(Path.home()))
        if path:
            self.target_root_edit.setText(path)
            self._refresh_packages()
            self._refresh_render_backend()

    def _browse_rdc_root(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select RDC root", self.rdc_root_edit.text() or str(Path.cwd()))
        if path:
            self.rdc_root_edit.setText(path)
            self._refresh_rdc_entries()

    def _refresh_packages(self) -> None:
        target_root = self._read_path(self.target_root_edit.text())
        if target_root is None:
            return
        try:
            packages = list(self._services.targets.discover_packages(target_root))
        except Exception as exc:
            self._log(f"[packages] {exc}")
            QMessageBox.warning(self, "包扫描失败", str(exc))
            return
        self.package_list.clear()
        for package in packages:
            item = QListWidgetItem(str(package))
            self.package_list.addItem(item)
        if packages:
            self.package_list.setCurrentRow(0)
        self._log(f"[包列表] 发现 {len(packages)} 个安装包")
        self._sync_state()

    def _refresh_rdc_entries(self) -> None:
        rdc_root = self._read_path(self.rdc_root_edit.text())
        if rdc_root is None:
            return
        try:
            entries = list(self._rdc_workflow.refresh(rdc_root))
        except Exception as exc:
            self._log(f"[rdc] {exc}")
            QMessageBox.warning(self, "RDC 扫描失败", str(exc))
            return
        self.rdc_list.clear()
        for entry in entries:
            item = QListWidgetItem(f"{entry.display_name}  [{entry.size_bytes} bytes]")
            item.setData(Qt.UserRole, str(entry.path))
            self.rdc_list.addItem(item)
        if entries:
            self.rdc_list.setCurrentRow(0)
        self._log(f"[RDC] 发现 {len(entries)} 个捕获文件")
        self._sync_state()

    def _launch_target(self) -> None:
        target_root = self._read_path(self.target_root_edit.text())
        if target_root is None:
            return
        try:
            result = self._launch_workflow.run(target_root, None)
        except Exception as exc:
            self._log(f"[launch] {exc}")
            QMessageBox.warning(self, "启动失败", str(exc))
            return
        self.result_state_label.setText(result.message)
        self._state.target_root = target_root
        self._state.process_id = result.process_id
        self._state.renderdoc_attached = result.attached
        self._log(f"[启动] {result.message}")
        self._sync_state()

    def _run_selected_package(self) -> None:
        target_root = self._read_path(self.target_root_edit.text())
        package_name = self._current_package_name()
        if target_root is None or package_name is None:
            return
        try:
            result = self._launch_workflow.run(target_root, package_name)
        except Exception as exc:
            self._log(f"[run] {exc}")
            QMessageBox.warning(self, "运行失败", str(exc))
            return
        self.result_state_label.setText(result.message)
        self._log(f"[运行] {package_name} -> {result.message}")
        self._sync_state()

    def _refresh_render_backend(self) -> None:
        target_root = self._read_path(self.target_root_edit.text())
        if target_root is None:
            return
        try:
            backend = self._services.targets.get_render_backend(target_root)
        except Exception as exc:
            self._log(f"[渲染] {exc}")
            return
        idx = self.render_backend_combo.findText(backend)
        if idx >= 0:
            self.render_backend_combo.blockSignals(True)
            self.render_backend_combo.setCurrentIndex(idx)
            self.render_backend_combo.blockSignals(False)
        self._log(f"[渲染] 当前后端: {backend}")

    def _on_render_backend_changed(self, _backend: str) -> None:
        self._sync_state()

    def _apply_render_backend(self) -> None:
        target_root = self._read_path(self.target_root_edit.text())
        if target_root is None:
            return
        backend = self.render_backend_combo.currentText()
        try:
            self._services.targets.set_render_backend(target_root, backend)
        except Exception as exc:
            self._log(f"[渲染] {exc}")
            QMessageBox.warning(self, "应用渲染后端失败", str(exc))
            return
        self._log(f"[渲染] 已切换为 {backend}")
        self.result_state_label.setText(f"已切换为 {backend}")
        self._sync_state()

    def _open_selected_rdc(self) -> None:
        rdc_path = self._current_rdc_path()
        if rdc_path is None:
            return
        try:
            self._services.renderdoc.open_rdc(rdc_path)
        except Exception as exc:
            self._log(f"[open] {exc}")
            QMessageBox.warning(self, "打开失败", str(exc))
            return
        self._log(f"[打开] {rdc_path}")
        self.result_state_label.setText(f"已打开 {rdc_path.name}")
        self._state.selected_rdc = rdc_path
        self._sync_state()

    def _analyze_selected_rdc(self) -> None:
        rdc_path = self._current_rdc_path()
        if rdc_path is None:
            return
        try:
            summary = self._analysis_workflow.run(rdc_path)
        except Exception as exc:
            self._log(f"[analyze] {exc}")
            QMessageBox.warning(self, "分析失败", str(exc))
            return
        self._current_summary = summary
        self.analysis_preview.setPlainText(self._format_summary(summary))
        self.result_state_label.setText("分析完成")
        self._state.selected_rdc = rdc_path
        self._log(f"[分析] {rdc_path} -> {summary.title}")
        for line in summary.highlights:
            self._log(f"  - {line}")
        self._sync_state()

    def _focus_selected_event(self) -> None:
        rdc_path = self._current_rdc_path()
        if rdc_path is None:
            return
        event_id = self._parse_optional_int(self.event_id_edit.text())
        try:
            payload = self._services.renderdoc.focus_rdc_event(
                rdc_path,
                event_id=event_id,
                save_root_dir=self._read_path(self.output_root_edit.text()) or self._services.renderdoc.default_analysis_save_root(),
            )
        except Exception as exc:
            self._log(f"[focus] {exc}")
            QMessageBox.warning(self, "聚焦失败", str(exc))
            return
        self._log(f"[聚焦] {payload.get('resolved_event_id') or event_id}")
        self.result_state_label.setText(f"已聚焦事件 {payload.get('resolved_event_id') or event_id}")
        self._sync_state()

    def _export_summary(self) -> None:
        if self._current_summary is None:
            QMessageBox.information(self, "无法导出", "请先执行一次分析。")
            return
        output_root = self._read_path(self.output_root_edit.text()) or self._services.renderdoc.default_analysis_save_root()
        try:
            result = self._export_workflow.run(self._current_summary, output_root)
        except Exception as exc:
            self._log(f"[export] {exc}")
            QMessageBox.warning(self, "导出失败", str(exc))
            return
        self.result_state_label.setText(f"已导出 {len(result.written_files)} 个文件")
        self._log(f"[导出] {result.output_dir}")
        for path in result.written_files:
            self._log(f"  - {path}")
        self._sync_state()

    def _collect_shader_catalog(self) -> None:
        rdc_root = self._read_path(self.rdc_root_edit.text())
        if rdc_root is None:
            QMessageBox.information(self, "缺少 RDC 目录", "请先选择一个包含 .rdc 的目录。")
            return
        output_root = self._read_path(self.output_root_edit.text()) or self._services.renderdoc.default_analysis_save_root()
        self._start_background_task(
            title="收集目录 Shader",
            task=lambda progress: self._shader_catalog_workflow.run(
                rdc_root,
                output_root,
                progress_callback=progress,
            ),
            on_success=self._handle_shader_catalog_result,
            busy_text="正在收集目录 Shader",
        )

    def _transpile_shader_catalog(self) -> None:
        output_root = self._read_path(self.output_root_edit.text()) or self._services.renderdoc.default_analysis_save_root()
        self._start_background_task(
            title="反汇编为 HLSL",
            task=lambda progress: self._shader_transpile_workflow.run(
                output_root,
                output_root,
                progress_callback=progress,
            ),
            on_success=self._handle_shader_transpile_result,
            busy_text="正在转译 Shader",
        )

    def _start_background_task(
        self,
        *,
        title: str,
        task: Callable[[Callable[[int, int, str], None]], Any],
        on_success: Callable[[Any], None],
        busy_text: str,
    ) -> None:
        if self._task_running:
            QMessageBox.information(self, title, "当前已有任务在运行，请先等它结束。")
            return

        self._task_running = True
        self._task_title = title
        self._task_on_success = on_success
        self._set_shader_task_busy(True, busy_text)
        self._status_label.setText(busy_text)

        thread = QThread(self)
        worker = _BackgroundWorker(task)
        self._task_thread = thread
        self._task_worker = worker

        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_shader_task_progress)
        worker.finished.connect(self._on_shader_task_finished)
        worker.failed.connect(self._on_shader_task_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(self._clear_shader_task_refs)
        thread.start()

    def _handle_shader_catalog_result(self, result: Any) -> None:
        self.result_state_label.setText(f"已收集 {result.shader_count} 个 Shader")
        self._log(f"[Shader] 目录: {result.rdc_root}")
        self._log(f"[Shader] RDC 数: {result.rdc_count}")
        self._log(f"[Shader] Shader 数: {result.shader_count}")
        self._log(f"[Shader] 输出: {result.output_dir}")
        self._log(f"[Shader] 原始 Shader 归档到: {result.output_dir / 'shaders'}")
        if result.top_shaders:
            top_shader = result.top_shaders[0]
            description = str(top_shader.get("effectDescription", "") or "")
            if description:
                self._log(f"[Shader] 示例描述: {description}")
            raw_targets = top_shader.get("rawTargets", []) or []
            if raw_targets:
                self._log(f"[Shader] 示例原始目标: {', '.join(str(x) for x in raw_targets[:3])}")
        if result.failed_rdc_files:
            self._log(f"[Shader] 有 {len(result.failed_rdc_files)} 个 RDC 收集失败")
            for item in result.failed_rdc_files[:8]:
                self._log(f"  - {item.get('rdc')}: {item.get('error')}")
        if result.errors:
            self._log(f"[Shader] 警告: {len(result.errors)} 条")
            for line in result.errors[:8]:
                self._log(f"  - {line}")
        for path in result.written_files:
            self._log(f"  - {path}")

    def _handle_shader_transpile_result(self, result: Any) -> None:
        core_count = getattr(result, "core_count", 0)
        if core_count:
            self.result_state_label.setText(f"已转译 {result.shader_count} 个 Shader，核心组 {core_count} 个")
        else:
            self.result_state_label.setText(f"已转译 {result.shader_count} 个 Shader")
        self._log(f"[Shader] 读取目录: {result.source_dir}")
        self._log(f"[Shader] 转译输出: {result.output_dir}")
        classification_output_dir = getattr(result, "classification_output_dir", None)
        if classification_output_dir:
            self._log(f"[Shader] 分类输出: {classification_output_dir}")
        self._log(f"[Shader] 汇总后唯一数量: {result.shader_count}")
        duplicate_count = getattr(result, "duplicate_shader_count", 0)
        if duplicate_count:
            self._log(f"[Shader] 去重跳过: {duplicate_count} 个重复项")
        family_count = getattr(result, "family_count", 0)
        if family_count:
            self._log(f"[Shader] 家族数量: {family_count} 个")
        effect_count = getattr(result, "effect_count", 0)
        if effect_count:
            self._log(f"[Shader] 效果组数量: {effect_count} 个")
        role_count = getattr(result, "role_count", 0)
        if role_count:
            self._log(f"[Shader] 角色组数量: {role_count} 个")
        if core_count:
            self._log(f"[Shader] 核心组数量: {core_count} 个")
        summary_file = getattr(result, "summary_file", None)
        if summary_file:
            self._log(f"[Shader] 汇总摘要: {summary_file}")
        family_summary_file = getattr(result, "family_summary_file", None)
        if family_summary_file:
            self._log(f"[Shader] 家族摘要: {family_summary_file}")
        effect_summary_file = getattr(result, "effect_summary_file", None)
        if effect_summary_file:
            self._log(f"[Shader] 效果摘要: {effect_summary_file}")
        role_summary_file = getattr(result, "role_summary_file", None)
        if role_summary_file:
            self._log(f"[Shader] 角色摘要: {role_summary_file}")
        core_summary_file = getattr(result, "core_summary_file", None)
        if core_summary_file:
            self._log(f"[Shader] 核心摘要: {core_summary_file}")
        if result.failed_shaders:
            self._log(f"[Shader] 转译失败: {len(result.failed_shaders)} 个")
            for item in result.failed_shaders[:8]:
                self._log(f"  - {item.get('manifest')}: {item.get('error')}")
        if result.errors:
            self._log(f"[Shader] 转译警告: {len(result.errors)} 条")
            for line in result.errors[:8]:
                self._log(f"  - {line}")
        for path in result.written_files:
            self._log(f"  - {path}")

    def _on_shader_task_progress(self, done: int, total: int, message: str) -> None:
        self.shader_progress_bar.setVisible(True)
        if message:
            self.shader_progress_label.setText(message)
            self._status_label.setText(message)
        if total > 0 and total != self._shader_progress_total:
            self._shader_progress_total = total
            self.shader_progress_bar.setRange(0, total)
        if total > 0:
            self.shader_progress_bar.setValue(min(done, total))
        else:
            self.shader_progress_bar.setRange(0, 0)

    def _on_shader_task_finished(self, result: Any) -> None:
        try:
            if self._task_on_success is not None:
                self._task_on_success(result)
        finally:
            self._task_running = False
            self._task_title = ""
            self._task_on_success = None
            self._set_shader_task_busy(False, "就绪")
            self._sync_state()

    def _on_shader_task_failed(self, error_text: str) -> None:
        self._log(error_text)
        title = self._task_title or "任务失败"
        QMessageBox.warning(self, title, error_text)
        self.result_state_label.setText("任务失败")
        self._task_running = False
        self._task_title = ""
        self._task_on_success = None
        self._set_shader_task_busy(False, "任务失败")
        self._sync_state()

    def _set_shader_task_busy(self, busy: bool, text: str) -> None:
        self.shader_progress_label.setText(text)
        self.shader_progress_bar.setVisible(busy)
        self._status_label.setText(text)
        if busy:
            self._shader_progress_total = 1
            self.shader_progress_bar.setRange(0, 0)
            self.shader_progress_bar.setValue(0)
        else:
            self.shader_progress_bar.setRange(0, 1)
            self.shader_progress_bar.setValue(0)
        for button in self._shader_task_buttons:
            button.setEnabled(not busy)

    def _clear_shader_task_refs(self) -> None:
        self._task_worker = None
        self._task_thread = None

    def _on_package_selected(self, current: Optional[QListWidgetItem], _previous: Optional[QListWidgetItem]) -> None:
        if current is None:
            self.package_path_edit.clear()
            self._sync_state()
            return
        self.package_path_edit.setText(current.text())
        self._sync_state()

    def _on_rdc_selected(self, current: Optional[QListWidgetItem], _previous: Optional[QListWidgetItem]) -> None:
        if current is None:
            self._state.selected_rdc = None
            self._sync_state()
            return
        path = current.data(Qt.UserRole)
        if path:
            self._state.selected_rdc = Path(str(path))
        self._sync_state()

    def _filter_packages(self) -> None:
        needle = self.package_filter_edit.text().strip().lower()
        for index in range(self.package_list.count()):
            item = self.package_list.item(index)
            item.setHidden(bool(needle) and needle not in item.text().lower())

    def _current_package_name(self) -> Optional[str]:
        text = self.package_path_edit.text().strip()
        if not text:
            QMessageBox.information(self, "缺少包", "请先选择一个包。")
            return None
        return text

    def _read_path(self, text: str) -> Optional[Path]:
        value = text.strip()
        if not value:
            return None
        return Path(value).expanduser().resolve()

    def _parse_optional_int(self, text: str) -> Optional[int]:
        value = text.strip()
        if not value:
            return None
        try:
            return int(value)
        except ValueError:
            QMessageBox.warning(self, "事件 ID 无效", f"不是整数：{value}")
            return None

    def _format_summary(self, summary: AnalysisSummary) -> str:
        lines = [f"RDC: {summary.rdc_path}", f"Title: {summary.title}", ""]
        if summary.highlights:
            lines.append("要点：")
            lines.extend(f"- {line}" for line in summary.highlights)
        else:
            lines.append("暂无要点。")
        return "\n".join(lines)

    def _log(self, message: str) -> None:
        if hasattr(self, "log_view"):
            self.log_view.appendPlainText(message)

    def _sync_state(self) -> None:
        target_root = self._read_path(self.target_root_edit.text())
        output_root = self._read_path(self.output_root_edit.text())

        self._state.target_root = target_root
        self._state.package_name = self.package_path_edit.text().strip() or None
        self._state.selected_rdc = self._current_rdc_path(silent=True)

        self.target_state_label.setText(str(target_root) if target_root else "无")
        self.package_state_label.setText(self._state.package_name or "无")
        self.rdc_state_label.setText(str(self._state.selected_rdc) if self._state.selected_rdc else "无")
        self.output_state_label.setText(str(output_root or self._services.renderdoc.default_analysis_save_root()))
        self.renderdoc_state_label.setText("就绪")
        status_bits = []
        if target_root:
            status_bits.append("目标已设置")
        if self._state.selected_rdc:
            status_bits.append("已选 RDC")
        if self._current_summary:
            status_bits.append("分析已就绪")
        self._status_label.setText(" | ".join(status_bits) if status_bits else "空闲")

    def _current_rdc_path(self, silent: bool = False) -> Optional[Path]:
        current = self.rdc_list.currentItem()
        if current is None:
            if not silent:
                QMessageBox.information(self, "缺少 RDC", "请先选择一个 .rdc 文件。")
            return None
        path = current.data(Qt.UserRole)
        return Path(str(path)) if path else None
