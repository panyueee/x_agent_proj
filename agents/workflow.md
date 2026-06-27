# 并行开发工作流

## 标准流程

```
你描述需求
  ↓
Claude 拆分任务 + 写防御性 prompt
  ↓
并行启动 Agent（各自在独立 worktree）
  ↓
Agent 完成后自动通知
  ↓
Claude 收集结果、处理依赖冲突、git commit + merge 进 main
  ↓
你直接跑验收
```

## Worktree 目录

| 分支 | 目录 | 负责模块 |
|---|---|---|
| feature/x | `.claude/worktrees/feature-x` | X (Twitter) |
| feature/xhs | `.claude/worktrees/feature-xhs` | 小红书 |
| feature/tgb | `.claude/worktrees/feature-tgb` | 淘股吧 |
| feature/finance | `.claude/worktrees/feature-finance` | 金融行情 |

## 重建 Worktree（每轮开发前执行）

```bash
# 删旧的
git worktree remove .claude/worktrees/feature-x --force
git worktree remove .claude/worktrees/feature-xhs --force
git worktree remove .claude/worktrees/feature-tgb --force
git worktree remove .claude/worktrees/feature-finance --force
git branch -D feature/x feature/xhs feature/tgb feature/finance 2>/dev/null

# 从 main 重建
git worktree add .claude/worktrees/feature-x -b feature/x
git worktree add .claude/worktrees/feature-xhs -b feature/xhs
git worktree add .claude/worktrees/feature-tgb -b feature/tgb
git worktree add .claude/worktrees/feature-finance -b feature/finance
```

## Merge 回 main

```bash
git merge feature/x --no-edit
git merge feature/xhs --no-edit
git merge feature/tgb --no-edit
git merge feature/finance --no-edit
```

## Agent Prompt 编写原则

1. **明确 worktree 路径**：每个 agent 只操作自己的 worktree
2. **预埋容错决策**：`如果 X 失败，换 Y；如果 Y 也失败，跳过并注明`
3. **Python 3.8 兼容提醒**：不用 `list[str]`、`dict[str,int]` 等新式类型注解
4. **结尾要求 git commit**：`git -C <worktree_path> add ... && git commit -m "..."`
5. **不要产生测试文件或文档**：只改生产代码

## 注意事项

- Agent 默认没有 Bash 权限，commit 步骤由 Claude 主会话统一执行
- 依赖安装统一用 `uv pip install`（项目使用 uv 管理 .venv）
- Python 版本：3.8（`.venv/bin/python`）
- 系统 python3（3.14）用于运行 Playwright（淘股吧爬虫）
