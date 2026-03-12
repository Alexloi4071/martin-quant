.PHONY: install test lint format scan scan-v2 review report regime clean help

PYTHON = python
PIP    = pip

## 安裝開發環境
install:
	$(PIP) install -e ".[dev]"

## 執行所有測試
test:
	pytest tests/ -v --tb=short

## 執行測試 + 覆蓋率
test-cov:
	pytest tests/ -v --cov=src/martin_quant --cov-report=term-missing

## Ruff lint
lint:
	ruff check src/ tests/ --select E,F,W --ignore E501

## Black 格式化
format:
	black src/ tests/ --line-length 100

## V1 每日掃描
scan:
	martin-scan scan

## V2 每日掃描
scan-v2:
	martin-scan scan-v2 --regime BULL

## 週報分析
review:
	martin-scan review --weeks 1

## 生成 Markdown 週報
report:
	martin-scan report --weeks 1

## 送週報到 Telegram
report-tg:
	martin-scan report --weeks 1 --telegram

## 顯示當前 regime
regime:
	martin-scan regime

## 顯示 BULL regime 推薦 sector
sectors:
	martin-scan sectors --regime BULL

## 清除快取和編譯檔案
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete
	rm -rf .pytest_cache dist build *.egg-info

## 幫助
help:
	@echo ""
	@echo "  Martin Quant — Makefile指令"
	@echo "  ================================"
	@grep -E '^##' Makefile | sed 's/## /  /'
	@echo ""
