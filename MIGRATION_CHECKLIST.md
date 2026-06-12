# renderdoc-workbench 第一阶段迁移清单

## 1. 目的

这份清单只覆盖“准备开工前必须确认”和“第一阶段落地必须完成”的内容。

范围严格限制为：

- 会话控制
- RDC 刷新与选择
- RDC 分析与导出

不在本阶段范围内的内容，不提前动。

## 2. 开工前确认项

### 产品边界

- [x] 仓库主定位从 `renderdoc-mcp` 调整为 `renderdoc-workbench`
- [x] GUI 是主入口，CLI 是辅助入口
- [x] 工具负责会话控制
- [x] RenderDoc 负责快捷键抓帧
- [x] 工具负责抓帧后的 RDC 管理与分析

### 文档边界

- [x] 已有总蓝图文档
- [x] 已有 GUI 线框说明
- [ ] 需要你最终确认第一期范围
- [ ] 需要你最终确认 GUI 交互是否按三栏结构推进

## 3. 第一阶段目录动作

这些是“真正开工”时建议的首批目录动作。

### 新增骨架

- [ ] 新建 `src/renderdoc_workbench/`
- [ ] 新建 `src/renderdoc_workbench/core/`
- [ ] 新建 `src/renderdoc_workbench/adapters/`
- [ ] 新建 `src/renderdoc_workbench/workflows/`
- [ ] 新建 `src/renderdoc_workbench/gui/`
- [ ] 新建 `src/renderdoc_workbench/cli/`
- [ ] 新建 `tests/`
- [ ] 新建 `docs/`

### 运行时边界

- [ ] 规划 `runtime/renderdoc/` 目录
- [ ] 确定哪些 RenderDoc 二进制继续随仓库保留
- [ ] 确定哪些试验运行时内容不再放在主源码边界内
- [ ] 确认统一绑定仓库内置 Python 运行时
- [ ] 确认固定 `python313 + PySide6` 作为 GUI 运行时组合

### 历史内容标记

- [ ] 将 `README_MCP.md` 明确标记为历史说明
- [ ] 将 `_ext_renderdoc_trial/` 标记为历史试验运行时
- [ ] 将根目录临时日志和临时脚本列入待清理列表

## 4. 第一阶段代码拆分顺序

### 第一步：保留旧入口，先建新层

- [ ] 保留 `mcp/renderdoc_mcp_server.py` 不直接删
- [ ] 先建立 `core` / `adapters` / `workflows` 新骨架
- [ ] 用新骨架承接旧逻辑，而不是一开始就重写

### 第二步：先拆会话控制

优先抽出：

- [ ] MUMU 路径识别
- [ ] package 枚举
- [ ] 目标启动
- [ ] RenderDoc 注入或附加

这些应该进入：

- `adapters/targets/`
- `workflows/launch/`

### 第三步：再拆 RDC 浏览

优先抽出：

- [ ] RDC 根目录配置
- [ ] RDC 刷新
- [ ] RDC 列表
- [ ] RDC 选中与打开

这些应该进入：

- `adapters/filesystem/`
- `workflows/rdc_browser/`

### 第四步：最后接分析与导出

优先抽出：

- [ ] 当前 RDC 分析入口
- [ ] 分析摘要结果模型
- [ ] 导出报告
- [ ] 导出资源

这些应该进入：

- `adapters/renderdoc/`
- `adapters/reporting/`
- `workflows/analysis/`
- `workflows/export/`

## 5. GUI 第一阶段落地顺序

### 最小可用界面

- [ ] 先做单窗口，不做多页面
- [ ] 先做三栏布局和底部日志区
- [ ] 先做静态区块与空状态

### 第一批接线控件

- [ ] MUMU 路径输入
- [ ] package 列表
- [ ] `启动并注入`
- [ ] RDC 根目录输入
- [ ] `刷新`
- [ ] RDC 列表
- [ ] `分析 RDC`

### 第二批接线控件

- [ ] `打开 RDC`
- [ ] `导出报告`
- [ ] `导出资源`
- [ ] 右栏状态面板
- [ ] 底栏日志

## 6. 第一阶段明确不做

- [ ] 不自己实现抓帧按钮
- [ ] 不做复杂插件系统
- [ ] 不做自动诊断引擎
- [ ] 不做完整视觉精修
- [ ] 不先重写所有历史分析逻辑
- [ ] 不先清空旧目录再重建

## 7. 开工门槛

只有下面条件满足后，才建议开始真正动代码：

- [ ] 你确认第一期范围不再扩
- [ ] 你确认三栏 GUI 结构可接受
- [ ] 你确认第一期仍保留 RenderDoc 手动快捷键抓帧
- [ ] 你确认先保留旧入口文件做渐进迁移

## 8. 开工后的第一提交建议

真正开工时，第一批提交最好只做结构，不做功能扩展。

建议第一提交内容：

- 新建目录骨架
- 增加基础 README / docs 引用
- 增加空模块和占位接口
- 不引入行为变化

这样回退成本最低，也最容易验证方向没有跑偏。
