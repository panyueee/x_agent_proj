#!/bin/bash
# 每轮并行开发前执行：删除旧 worktree，从最新 main 重建六个分支
# 用法：bash agents/scripts/new_round.sh

set -e
cd "$(dirname "$0")/../.."

echo "=== 删除旧 worktree 和分支 ==="
for branch in feature-x feature-xhs feature-tgb feature-finance feature-industry feature-research; do
    path=".claude/worktrees/$branch"
    if [ -d "$path" ]; then
        git worktree remove "$path" --force && echo "removed $path"
    fi
done

for branch in feature/x feature/xhs feature/tgb feature/finance feature/industry feature/research; do
    git branch -D "$branch" 2>/dev/null && echo "deleted $branch" || true
done

echo ""
echo "=== 从 main 重建 ==="
git checkout main
git worktree add .claude/worktrees/feature-x        -b feature/x
git worktree add .claude/worktrees/feature-xhs      -b feature/xhs
git worktree add .claude/worktrees/feature-tgb      -b feature/tgb
git worktree add .claude/worktrees/feature-finance  -b feature/finance
git worktree add .claude/worktrees/feature-industry -b feature/industry
git worktree add .claude/worktrees/feature-research -b feature/research

echo ""
echo "=== 当前 worktree 列表 ==="
git worktree list

echo ""
echo "✓ 准备好了，可以启动六路 agent"
