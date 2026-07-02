# Obsidian 连接（progressive-investment-research skill 的 promote 落点）

## 连接对象
- **Vault**：`/Users/pany19/Documents/Obsidian Vault`（本机唯一 vault，obsidian.json 里 open=true）
- **落点文件夹**：`投研Research/`，按板块分子文件夹：
  - 根：`投研主索引.MOC.md`（promote 挂载点/Map of Content）、`_连接说明.md`
  - `人物模型/`（如 张瑜 华创宏观）· `方法与框架/`（Persona方法、Aladdin风险方法论）· `系统与数据/`（数据库表设计、淘股吧抓取逻辑、政策事件库设计）· `行业与产业链/` · `公司Watchlist/` · `信号与假设/`
  - 双链按文件名全库解析，跨文件夹不影响跳转；promote 新笔记时放对应板块子文件夹并挂 MOC
- **obsidian CLI**：本机**未安装**。vault 就是普通 markdown 文件夹，promote 直接写文件即可；
  校验回链/标签/搜索用文件读取 + grep 兜底（符合 skill 的 Public Fallback Rule）。

## 分工（关键）
- **dossier = 工作区**：活在本仓库（研究对象的 Current Model / 证据行 / 开放问题），持续演进、变动频繁。
- **Obsidian 投研Research = 长期库**：只放少量、稳定、**经独立 subagent 审计**的高价值成果。

## promote 流程（skill workflows.md 的约定 + 本项目落地）
1. 在项目里选候选成果（稳定判断 / 概念定义 / 精炼模块 / 分析框架 / 已审计开放问题）
2. 主 agent 整理 promote 草案
3. **独立 subagent 审计**（反对点 / 证据缺口 / 建议修改）
4. 处理审计冲突，标明剩余不确定性
5. 写入 `投研Research/`：挂 MOC、补 frontmatter、建 `[[双链]]`
6. 校验（读文件 + grep：回链是否成立、标签一致、无第二套事实源）
7. 在来源 dossier 的 `update-log.md` 记录 promote 结果

## 触发方式
promote 是**人工触发 + 审计**动作（会话里明说"把 X promote 到 Obsidian"），
**不做后台自动同步**——防止未审计内容污染长期库。

## 首批 MOC 板块
人物模型 Persona / 行业与产业链 / 公司 Watchlist / 方法与框架 / 信号与假设。
张瑜数据接地画像、Aladdin 风险方法论、persona 多方法对照 已在 MOC 建了占位入口。
