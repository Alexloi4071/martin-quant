# Changelog

All notable changes to **martin-quant** are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [0.18.0] — 2026-03-12

### Added
- **GitHub Actions CI** (`.github/workflows/ci.yml`)
  - pytest on Python 3.11 & 3.12
  - ruff lint + black format check
  - Auto-skip IBKR live tests
- **GitHub Actions Weekly Report** (`.github/workflows/weekly_report.yml`)
  - Runs every Sunday 21:00 CST
  - Sends Telegram weekly report automatically
- **CHANGELOG.md** — this file
- **.env.example** — 完整環境變數樣本
- **Makefile** — 常用指令快速入口

---

## [0.17.0] — 2026-03-12

### Added
- **TradeReviewer** (`review/trade_reviewer.py`)
  - Win rate, Avg R, Expectancy, Profit Factor, Max Drawdown
  - by setup / by sector / by regime 分組統計
  - 自動產生改進建議
- **WeeklyReport** (`review/weekly_report.py`)
  - stdout / Markdown / Telegram 三種輸出
- **DailyScannerV2** (`scanner/daily_scan_v2.py`)
  - AVWAP 支撑 + Sector Filter + ORB 15m Trigger 整合
- **DataPipeline** (`pipeline/data_pipeline.py`)
  - ThreadPoolExecutor 並行下載，支援 retry
- **run_daily_scan_v2.py** — V2 一鍵執行腳本
- **CLI v2** (`cli/main.py`) — 7 個子指令：scan / scan-v2 / review / report / regime / sectors / orb
- `pyproject.toml` 更新：`martin-scan` + `martin-scan-v2` entry points

---

## [0.16.0] — 2026-03-10

### Added
- **run_daily_scan.py** — V1 一鍵掃描腳本
- **AlertManager** (`utils/alert_manager.py`) — Telegram 通知
- **TradeLogger** (`utils/trade_logger.py`) — trades.csv 持久化
- pytest fixtures (`tests/conftest.py`)
- README.md 完整安裝說明

---

## [0.15.0] — 2026-03-08

### Added
- **ORBTrigger** (`timing/orb_15m_trigger.py`) — 15 分鐘 ORB 開盤區間
- **AVWAPAnchorManager** (`anchors/avwap_anchor_manager.py`) — AVWAP 支撑镶點
- **SectorRegimeFilter** (`regime/sector_regime_filter.py`) — Sector x Regime 第二層的過濾

---

## [0.14.0] — 2026-03-06

### Added
- **ExitManager** (`risk/exit_manager.py`) — 5 種出場規則
  - EMA9 two-bar confirm, Trailing stop, Target hit, Time stop, Reversal bar
- **MarketRegimeDetector** (`regime/regime_detector.py`) — BULL / WEAK_BULL / CHOPPY / BEAR

---

## [0.10.0] — 2026-02-28

### Added
- 初始框架建立：core / data / features / setups / filters / scanners
- `DailyScanner` V1 基礎實現
- yfinance / IBKR data provider
- `pyproject.toml` 基礎配置
