#!/bin/bash
# 把四个 feature 分支合并进 main
# 用法：bash agents/scripts/merge_all.sh

set -e
cd "$(dirname "$0")/../.."

git checkout main

for branch in feature/x feature/xhs feature/tgb feature/finance; do
    if git show-ref --verify --quiet "refs/heads/$branch"; then
        echo "=== merge $branch ==="
        git merge "$branch" --no-edit
    else
        echo "跳过 $branch（不存在）"
    fi
done

echo ""
echo "=== 最新 git log ==="
git log --oneline -6
