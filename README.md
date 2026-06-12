# renderdoc-workbench

以 RenderDoc 为核心的图形调试工作台。

当前仓库已从历史 `renderdoc-mcp` 命名切换为 `renderdoc-workbench`：

- 提供一个以 GUI 为主入口的 RenderDoc 工作台
- 围绕目标选择、启动注入、RDC 管理、分析和报告导出构建统一工作流
- 将脚本、分析逻辑、GUI、运行时依赖与实验产物彻底解耦

## 当前定位

这个仓库不再以 MCP 作为核心交付形态。

后续重构将以 `renderdoc-workbench` 为产品名，GUI 工作台为主入口，CLI 为辅助入口。MCP 如果未来保留，也只应作为可选适配层，而不是仓库命名和架构中心。

工作边界已经明确为：

- 工具负责 MUMU / 目标环境选择、package 选择、目标启动、RenderDoc 注入或附加
- RenderDoc 自己负责通过快捷键完成实际抓帧
- 工具在抓帧之后负责 RDC 刷新、打开、分析与导出

## 规划文档

- 重构蓝图：[REFACTOR_BLUEPRINT.md](C:/Users/wepie/Desktop/RenderDoc-mcp/REFACTOR_BLUEPRINT.md)
- GUI 结构：[GUI_WIREFRAME.md](C:/Users/wepie/Desktop/RenderDoc-mcp/GUI_WIREFRAME.md)
- 迁移清单：[MIGRATION_CHECKLIST.md](C:/Users/wepie/Desktop/RenderDoc-mcp/MIGRATION_CHECKLIST.md)
- 模块职责：[MODULE_RESPONSIBILITIES.md](C:/Users/wepie/Desktop/RenderDoc-mcp/MODULE_RESPONSIBILITIES.md)
- 技术栈建议：[TECH_STACK_DECISION.md](C:/Users/wepie/Desktop/RenderDoc-mcp/TECH_STACK_DECISION.md)
- 历史 MCP 说明：[README_MCP.md](C:/Users/wepie/Desktop/RenderDoc-mcp/README_MCP.md)

## 说明

- 本仓库关注 RenderDoc 自动化与调试工作流本身，不承载具体项目的渲染实验代码。
- 具体的 Unity 效果验证与算法对照，应分别放在相关项目仓库中处理。
