# renderdoc-workbench 模块职责表

## 1. 目的

这份文档比总蓝图更细，专门回答一个问题：

真正开工后，每类逻辑应该落在哪一层，哪些层绝不能越界。

如果这张表不先定，最容易出现的问题就是：

- GUI 直接写业务逻辑
- workflow 直接操作文件和进程
- RenderDoc 调用散落在多个模块

## 2. 分层总表

| 层 | 职责 | 允许依赖 | 禁止依赖 |
|---|---|---|---|
| `core` | 领域模型、结果结构、错误类型、服务协议 | Python 标准库、小型纯数据依赖 | Qt、RenderDoc API、进程启动、路径扫描 |
| `adapters/targets` | MUMU 路径、package 枚举、启动目标、注入/附加时机 | 标准库、平台进程调用 | Qt 视图状态、分析结果格式化 |
| `adapters/renderdoc` | 打开 RDC、读取事件/资源状态、导出资源 | RenderDoc 相关调用 | GUI 控件、页面状态 |
| `adapters/filesystem` | RDC 目录、输出目录、最近文件、路径规范 | 标准库路径 API | GUI、RenderDoc API |
| `adapters/reporting` | 报告模板、HTML/Markdown/JSON 产物输出 | `core` 结果模型、filesystem | GUI、目标启动逻辑 |
| `workflows/launch` | 目标检查、启动、注入、会话状态整理 | `core` + `adapters/targets` | Qt 控件、报告模板 |
| `workflows/rdc_browser` | RDC 刷新、枚举、选中、打开 | `core` + `adapters/filesystem` + `adapters/renderdoc` | GUI 控件 |
| `workflows/analysis` | 当前 RDC 分析编排 | `core` + `adapters/renderdoc` | 进程启动、路径输入控件 |
| `workflows/export` | 导出报告与资源编排 | `core` + `adapters/reporting` + `adapters/renderdoc` | GUI 控件 |
| `gui` | 展示、交互、状态绑定、命令触发 | `workflows` | 直接写 RenderDoc 调用和复杂业务逻辑 |
| `cli` | 参数入口、批处理入口 | `workflows` | GUI 状态、页面控件 |

## 3. `core`

### 应该放什么

- `LaunchRequest`
- `LaunchResult`
- `RdcEntry`
- `RdcSelection`
- `AnalysisSummary`
- `ExportRequest`
- `ExportResult`
- `WorkbenchError`
- 统一状态枚举

### 不该放什么

- `subprocess.Popen(...)`
- Qt signal / slot
- `renderdoc` 模块调用
- 真正的目录扫描实现

## 4. `adapters/targets`

### 应该放什么

- MUMU 根目录定位
- package 枚举和过滤
- 目标启动
- 附加/注入 RenderDoc 的具体实现
- 会话进程探测

### 对外暴露的能力

- `discover_packages(...)`
- `launch_target(...)`
- `attach_renderdoc(...)`
- `query_session_state(...)`

### 不该放什么

- 分析当前 RDC
- 导出报告
- GUI 按钮启用禁用规则

## 5. `adapters/renderdoc`

### 应该放什么

- 打开/关闭某个 RDC
- 读取事件树
- 读取资源信息
- 读取管线状态
- 导出纹理和网格

### 对外暴露的能力

- `open_rdc(...)`
- `close_rdc(...)`
- `read_event_index(...)`
- `read_resource_summary(...)`
- `export_texture(...)`

### 不该放什么

- MUMU 路径逻辑
- package 列表
- 报告模板拼接

## 6. `adapters/filesystem`

### 应该放什么

- RDC 根目录配置
- RDC 列表扫描
- 最近打开的 RDC
- 输出目录生成
- 证据目录命名

### 对外暴露的能力

- `scan_rdc_entries(...)`
- `get_latest_rdc(...)`
- `ensure_output_dir(...)`
- `normalize_path(...)`

### 不该放什么

- RenderDoc 事件读取
- GUI 交互状态

## 7. `adapters/reporting`

### 应该放什么

- 报告模板
- 将 `AnalysisSummary` 变成 HTML/Markdown/JSON
- 组织导出目录结构

### 对外暴露的能力

- `build_html_report(...)`
- `build_markdown_report(...)`
- `write_report_bundle(...)`

### 不该放什么

- 打开 RDC
- 目标启动
- GUI 日志直接写入

## 8. `workflows`

### `launch`

负责把这些动作编成一条业务链：

- 校验目标目录
- 识别 package
- 启动目标
- 注入或附加 RenderDoc
- 整理可显示的会话状态

### `rdc_browser`

负责：

- 刷新 RDC 列表
- 排序
- 过滤
- 选择当前 RDC
- 调用 `adapters/renderdoc` 打开选中项

### `analysis`

负责：

- 调度 RenderDoc 读取
- 聚合事件、资源、Pass 摘要
- 产出 `AnalysisSummary`

### `export`

负责：

- 调度报告输出
- 调度资源导出
- 整理导出结果

## 9. `gui`

### GUI 应该做的事

- 收集用户输入
- 展示状态
- 触发 workflow
- 展示日志和错误
- 处理按钮启用禁用

### GUI 不应该做的事

- 直接读取 RenderDoc 事件
- 直接遍历磁盘找 RDC
- 直接启动目标进程
- 直接拼 HTML 报告

## 10. 判断规则

开工后如果遇到“不知道某段代码该放哪”的情况，用下面三条判断：

1. 这段代码是不是面向用户界面展示？
   - 是：先考虑 `gui`

2. 这段代码是不是在和外部系统打交道？
   - 是：先考虑 `adapters`

3. 这段代码是不是在把多个外部能力串成业务动作？
   - 是：先考虑 `workflows`

如果以上都不是，多半应该回到 `core`。
