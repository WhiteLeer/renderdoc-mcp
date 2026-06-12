# renderdoc-workbench 重构蓝图

## 1. 目标

将当前仓库从 `renderdoc-mcp` 重构为 `renderdoc-workbench`：

- 以窗口界面作为主入口
- 围绕 RenderDoc 调试工作流建立清晰的信息架构
- 拆开核心逻辑、RenderDoc 适配、GUI、CLI、实验脚本和运行时依赖
- 让仓库从“个人可跑通的试验目录”变成“可维护的工具工作台”

这份蓝图只定义方向、模块边界和迁移顺序，不要求一次性重写。

## 2. 产品定位

一句话定位：

`renderdoc-workbench` 是一个面向本地图形调试流程的 RenderDoc 工作台，负责目标选择、启动注入、RDC 管理、分析与报告导出。

更准确地说：

- 工具负责会话控制
- RenderDoc 负责实际抓帧
- 工具负责抓帧后的 RDC 发现、管理和分析

不再作为核心定位的内容：

- MCP 命名
- Agent 桥接仓库
- 以单个 server 文件为中心的工具集合

可保留但降级为可选适配层的内容：

- MCP 工具暴露
- 自动化脚本入口
- 面向批处理的 CLI

## 3. 主工作流

GUI 需要围绕下面的流程组织，而不是围绕零散按钮组织。

1. 选择目标环境
   - 选择 MUMU 或其他运行目录
   - 识别可注入目标和可选 package
   - 检查 RenderDoc 安装与连接状态

2. 启动与注入
   - 启动目标应用
   - 附加或注入 RenderDoc
   - 展示当前会话状态

3. 抓帧
   - 用户通过 RenderDoc 快捷键手动抓帧
   - 工具不重复实现抓帧按钮逻辑

4. RDC 刷新与载入
   - 刷新本地 RDC 目录
   - 列出本地 RDC
   - 打开已有 RDC

5. 分析
   - 分析当前 RDC
   - 浏览事件、Pass、资源、纹理、关键状态
   - 输出摘要与问题线索

6. 导出
   - 导出分析结果
   - 导出资源或纹理
   - 写入报告与证据目录

## 4. GUI 信息架构

建议主窗口采用三栏加底部日志区：

- 左栏：目标与输入
  - MUMU/本地目标目录
  - package 列表
  - 启动并注入
  - RDC 根目录
  - 刷新
  - 本地 RDC 列表

- 中栏：任务面板
  - 打开 RDC
  - 分析 RDC
  - 导出报告
  - 导出资源
  - 分析摘要预览

- 右栏：当前状态
  - RenderDoc 状态
  - 当前目标
  - 当前 package
  - 当前 RDC
  - 最近分析结果
  - 输出路径

- 底栏：日志与后台任务
  - 操作日志
  - 错误信息
  - 进度
  - 可点击产物路径

## 5. 目标目录结构

建议的目标结构如下：

```text
renderdoc-workbench/
  README.md
  LICENSE.md
  pyproject.toml

  src/renderdoc_workbench/
    core/
      models/
      errors/
      protocols/
      services/

    adapters/
      renderdoc/
      targets/
      filesystem/
      reporting/
      mcp/

    workflows/
      launch/
      rdc_browser/
      analysis/
      export/

    gui/
      app/
      windows/
      panels/
      widgets/
      viewmodels/

    cli/

    config/

  tests/
    unit/
    integration/
    fixtures/

  docs/
    architecture/
    gui/
    workflows/

  examples/
    sample-configs/
    sample-reports/

  tools/
    dev/
    packaging/

  runtime/
    renderdoc/
```

## 6. 模块职责

### `core`

只放与产品逻辑强相关、但不依赖具体外部系统的内容：

- capture 请求和结果模型
- 会话请求和结果模型
- RDC 分析上下文模型
- event / resource / texture / pipeline state 的统一结构
- 错误类型
- 工作流服务接口

这里不要直接访问：

- RenderDoc Python API
- 文件系统路径
- GUI 组件
- 报告模板

### `adapters/renderdoc`

负责和 RenderDoc 交互：

- 打开/关闭 RDC
- 附加目标
- 识别和读取已生成的抓帧结果
- 事件跳转
- 状态和资源读取
- 导出纹理/网格等资源

### `adapters/targets`

负责和本地目标环境交互：

- MUMU 路径识别
- package 枚举
- 目标启动
- 注入前环境准备
- RenderDoc 注入或附加时机控制

后续如果有更多目标类型，也统一接到这里，不要散落到 GUI 或 workflow 中。

### `adapters/filesystem`

负责：

- 路径管理
- 缓存目录
- 输出目录
- 会话目录
- 最近文件列表

### `adapters/reporting`

负责：

- 报告模板
- HTML/Markdown/JSON 输出
- 截图与证据目录结构

### `adapters/mcp`

可选适配层。

如果未来还需要保留 MCP，就只把内部工作流包一层工具接口，不再让 MCP 主导仓库命名和目录。

### `workflows`

这是面向用户能力的编排层。

- `launch`：目标检查、启动、附加、注入
- `rdc_browser`：刷新 RDC、列出 RDC、打开 RDC
- `analysis`：分析当前 RDC，返回摘要和结构化结果
- `export`：导出报告、资源和证据

workflow 不应该直接依赖 GUI 控件，只接受参数并返回结构化结果。

### `gui`

主产品入口。

职责：

- 组织界面布局
- 管理用户交互状态
- 展示任务进度和结果
- 调用 workflow

不负责：

- 直接写 RenderDoc 调用逻辑
- 直接处理复杂业务规则

## 7. 当前仓库到目标仓库的映射

按当前仓库形态，建议这样迁移：

- `mcp/renderdoc_mcp_server.py`
  - 拆成 `adapters/mcp`、`workflows`、`core` 三部分

- `analysis/`
  - 保留有效分析逻辑，迁入 `workflows/analysis` 或 `adapters/reporting`

- `captures/`
  - 改造成运行时输出目录，不作为核心源码目录

- `_ext_renderdoc_trial/`
  - 定义为历史试验运行时，逐步移出主源码区

- 根目录下 RenderDoc 二进制与 DLL
  - 后续统一收口到 `runtime/renderdoc/` 或外部依赖安装流程

- `README_MCP.md`
  - 保留为历史说明，不再作为主文档

## 8. 迁移顺序

建议分四阶段进行。

### 阶段 A：先收口，不重写

目标：

- 明确仓库新名字和新定位
- 新建蓝图文档
- 把历史 MCP 定位降级为兼容说明

产出：

- 新 README
- 重构蓝图
- 迁移待办清单

### 阶段 B：拆文档和目录边界

目标：

- 建立 `src/`、`docs/`、`tests/`、`runtime/` 的新骨架
- 清理临时日志、实验输出和运行时入库问题
- 让“源码”和“可执行依赖”先分家

### 阶段 C：拆业务逻辑

目标：

- 从 `renderdoc_mcp_server.py` 抽出 core / adapters / workflows
- 先保留旧入口，内部逐步改为调用新层

原则：

- 不先优化功能
- 不先改 UI
- 先把调用关系拉直

### 阶段 D：建立 GUI 主入口

目标：

- 根据本蓝图实现主窗口
- 先打通最短路径：
  - 选目标
  - 启动并注入
  - 用户用 RenderDoc 快捷键抓帧
  - 刷新 RDC 列表
  - 打开 RDC
  - 分析
  - 导出报告

## 9. 第一阶段功能范围

第一阶段只做下面这些能力：

- 选择 MUMU 或目标目录
- 枚举和选择 package
- 启动目标并注入/附加 RenderDoc
- 配置并识别 RDC 根目录
- 刷新并列出本地 RDC
- 打开选中的 RDC
- 分析当前 RDC
- 导出分析报告和资源

第一阶段不做：

- 自己触发抓帧
- 复杂自动诊断策略
- 插件系统
- 完整 GUI 美术打磨

## 10. 第一阶段明确不要做的事

- 不要一次性迁移所有功能
- 不要先重写分析算法
- 不要先设计复杂插件系统
- 不要先做视觉层细节打磨
- 不要让 GUI 直接复制旧 server 逻辑
- 不要重复实现 RenderDoc 已有的快捷键抓帧能力

第一阶段最重要的是边界，而不是功能数量。

## 11. 下一步建议

按当前状态，最合理的下一步是继续文档层设计，而不是立刻写实现：

1. 输出模块职责表
2. 输出 GUI 页面结构图
3. 输出迁移待办清单
4. 再决定 GUI 技术栈和第一批落地功能
