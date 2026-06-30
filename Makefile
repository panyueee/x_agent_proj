# Makefile —— 统一用项目虚拟环境的解释器，避免误用系统/anaconda 的坏 Python
#
# 关键约束：本机裸 `python` / `python3` 指向一个损坏的 anaconda Python 3.8
# （pip 报 InvalidVersion，且缺 pymupdf / Vision / jieba 等依赖）。
# 真正的项目环境是 .venv/bin/python（Python 3.14，由 uv 管理，依赖齐全）。
# 因此所有目标一律通过 $(PY) 调用解释器，绝不要写裸 python。

PY := .venv/bin/python

.DEFAULT_GOAL := help

.PHONY: help install test run pipeline digest \
        rag-stats rag-query rag-embed ingest-books

help:  ## 列出所有可用目标
	@echo "可用目标（解释器固定为 $(PY)）："
	@echo "  make install            创建/同步虚拟环境（uv venv + uv pip install）"
	@echo "  make test               运行测试 (pytest tests/)"
	@echo "  make run                运行主流程 (main.py --source all)"
	@echo "  make pipeline           X→产业链→研报 联动 (main.py --source pipeline)"
	@echo "  make digest             生成 output/digest.md（由主流程写出）"
	@echo "  make rag-stats          RAG 知识库统计"
	@echo "  make rag-query q=\"问题\"  RAG 检索"
	@echo "  make rag-embed          为入库内容补跑向量 (embed-all)"
	@echo "  make ingest-books       批量入库投资书籍 PDF"

install:  ## 用 uv 创建 .venv (Python 3.14) 并安装 requirements.txt
	uv venv --python 3.14 .venv
	uv pip install -r requirements.txt

test:  ## 运行单元测试
	$(PY) -m pytest tests/ -q

run:  ## 运行主流程（默认抓取全部数据源）
	$(PY) main.py

pipeline:  ## X 抓取后自动触发产业链→研报联动
	$(PY) main.py --source pipeline

digest:  ## 跑一次主流程以生成 output/digest.md
	$(PY) main.py

rag-stats:  ## 打印 RAG 知识库统计信息
	$(PY) -m x_agent.rag stats

rag-query:  ## RAG 检索，用法：make rag-query q="什么是动量因子"
	$(PY) -m x_agent.rag query "$(q)"

rag-embed:  ## 为已入库内容补跑 embedding
	$(PY) -m x_agent.rag embed-all

ingest-books:  ## 批量入库投资书籍 PDF
	$(PY) scripts/batch_ingest_books.py
