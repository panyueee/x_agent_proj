# 内存优化方案

## 现象
- 内存使用: 47GB / 48GB (97.9%)
- 可用: 仅 23MB
- 风险: 14-worker可能触发OOM

## 解决方案对比

| 方案 | Worker数 | 内存占用 | 耗时 | 安全性 | 推荐度 |
|------|---------|---------|------|--------|--------|
| A1 | 6 | 45.5GB | 2.5h | ✅ 安全 | ⭐⭐⭐⭐⭐ |
| A2 | 8 | 45.8GB | 1.5h | ✅ 安全 | ⭐⭐⭐⭐ |
| B1 | 14 | 48.5GB | 1.5h | ❌ 危险 | ❌ |
| C1 | 6 + 清理 | 42GB | 2.5h | ✅ 最安全 | ⭐⭐⭐⭐⭐ |

## 立即行动

### 1️⃣ 降低Worker数量 (快速)
```bash
# 停止当前进程
pkill -f transcribe_asr_m5_parallel.py

# 用6个worker重启 (安全且快速)
.venv/bin/python scripts/transcribe_asr_m5_parallel.py \
    --course "70天共读" --workers 6

# 预计耗时: 2.5小时，内存占用: ~45.5GB ✅
```

### 2️⃣ 清理系统 (可选但推荐)
```bash
# 关闭不必要应用
pkill Claude
pkill Safari
pkill "Google Chrome"
pkill "Baidu NetDisk"

# 清理Python缓存 (释放8.3GB)
rm -rf ~/.cache/pip
rm -rf ~/.cache/huggingface
rm -rf /var/folders/mx/xrw1v03n7d3dn10zvshtykl40000gn/T/jieba.cache

# 清理Xcode缓存 (释放5-10GB, 如果有)
rm -rf ~/Library/Developer/Xcode/DerivedData/*

# 运行缓存清理
purge  # 释放压缩缓存

# 查看效果
top -l 1 | grep "PhysMem:"
```

### 3️⃣ 降低Worker + 清理 (最推荐)
```bash
# 步骤1: 清理应用和缓存 (释放8-10GB)
pkill Claude && pkill Safari && pkill "Google Chrome" && pkill "Baidu NetDisk"
rm -rf ~/.cache/pip ~/.cache/huggingface
sleep 3

# 步骤2: 验证内存释放
top -l 1 | grep "PhysMem:"

# 步骤3: 用8-10个worker启动
.venv/bin/python scripts/transcribe_asr_m5_parallel.py \
    --course "70天共读" --workers 8

# 耗时: 1.5-2小时，内存占用: ~43GB ✅ 非常安全
```

## 监控建议

```bash
# 实时监控内存 (每10秒)
while true; do
    echo "[$(date '+%H:%M:%S')] $(top -l 1 | grep PhysMem:)"
    sleep 10
done
```

## 何时升级到14个Worker

✅ 满足以下条件时:
- 内存使用 < 42GB
- 关闭所有非必要应用
- Xcode缓存已清理
- Python缓存已清理

否则建议保持6-8个worker。
