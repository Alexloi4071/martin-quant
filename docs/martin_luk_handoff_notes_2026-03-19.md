# Martin Luk 接力紀錄與問題清單（2026-03-19）

## 目的

這份文件用來記錄本輪工作中：

- 已確認的問題
- 已完成的修正
- 目前仍存在的限制
- 下一步應如何接力

這不是策略文件，而是交接文件。

## 本輪已完成事項

### 1. 掃描與信號流程基礎已補齊

已完成：

- `scan-v2` 改為 direction-aware
- short-side setup enum 與資料結構已補齊
- 新增 signal export / journal / webhook receiver 基礎模組
- CLI 補上 `scan-v2` 與 `serve-webhook`
- TradingView Pine 指標已新增：
  - Script A
  - Script B
  - Script C1

相關檔案：

- `src/martin_quant/scripts/run_daily_scan_v2.py`
- `src/martin_quant/scanner/daily_scan_v2.py`
- `src/martin_quant/signals/*`
- `tradingview/ml_script_a_attention_router.pine`
- `tradingview/ml_script_b_setup_validator.pine`
- `tradingview/ml_script_c1_short_prev_hour_low_break.pine`

### 2. TradingView 文件已建立

已完成文件：

- `docs/tradingview_webhook_setup_2026-03-19.md`
- `docs/martin_luk_scan_to_tradingview_workflow_2026-03-19.md`
- `docs/pine_alert_layered_design_2026-03-19.md`
- `docs/pine_script_a_b_setup_notes_2026-03-19.md`
- `docs/pine_script_c1_setup_notes_2026-03-19.md`
- `docs/martin_luk_workflow_guide_zh_2026-03-19.md`

### 3. `scan-v2` runtime bug 已修掉

本輪實際修掉的 bug：

- Python 3.9 下 `@dataclass(slots=True)` 導致 import / runtime 失敗
- `breakout_setup.py` 有壞掉的 `f-string` 語法
- `martin_quant.cli` 的 eager import 造成 `runpy RuntimeWarning`
- `research.yaml` 檔案有亂碼位元組，導致 YAML 無法載入
- `FinvizProvider` 會把垃圾表頭誤判成 `NAN` ticker

## 本輪關鍵觀察

### 1. 原始 universe 太小且太偏 long

一開始的 `auto_candidates.txt` 只有：

- `YUM`
- `YOU`
- `XPO`
- `XOM`
- `XIFR`
- `WWD`
- `VRT`
- `VIRT`
- `VG`
- `TSSI`
- `SPY`

這不夠回答 Martin Luk 風格 short-first 的問題。

### 2. 已補上更接近 Martin Luk 的 short-side screens

`config/research.yaml` 已新增：

- `failed_breakout_shorts`
- `bounce_into_resistance`
- `broken_leaders`
- `parabolic_short_candidates`

這一步已完成。

### 3. 新的 Finviz candidate universe 已明顯擴大

目前 `finviz_candidates.csv` 已顯示新的 short-side 候選股，例如：

- `A`, `AAL`, `AAON`, `AAP`, `ABBV`, `ABM`, `ABT`
- `ZG`, `ZETA`, `Z`, `YELP`, `XYZ`, `WK`, `VERX`, `UBER`
- `AA`, `AAPL`, `ABCB`, `ACLS`, `ACMR`, `ADI`, `AES`

這代表新的 candidate generation 已經生效。

## 目前仍存在的核心問題

### 問題 1. `scan-v2` 實際讀到的本地 OHLCV 仍只有 11 檔

雖然新的 universe 已擴大，但實際執行 `scan-v2` 時，日誌仍顯示：

- `Step 1: daily=11 intraday=11`

也就是說：

- `auto_candidates.txt` 已變大
- `finviz_candidates.csv` 已變大
- 但本地實際可掃描的 OHLCV 資料沒有同步擴充完成

結果：

- `scan-v2` 仍只是在舊的 11 檔上運行
- 因此這次仍然得到：
  - `base_setup_count = 0`
  - `final_signal_count = 0`

### 問題 2. universe refresh 雖然跑了，但資料下載沒有完整跟上

refresh 已成功更新候選股名單，但完整下載 1d/1h/15m 資料的部分沒有真正補完新的 universe。

可見現象：

- `config/universe/auto_candidates.txt` 已有 40+ 檔
- `finviz_candidates.csv` 已有 8 組 screen 的資料
- 但 `scan-v2` 還是只看到 `11` 檔 daily / intraday 資料

### 問題 3. 目前 `0 signals` 不能視為市場沒有股票

目前更精確的說法是：

- 新 universe 已建立
- 但 scan 還沒真正吃到完整新 universe 的 OHLCV
- 所以現在的 `0 signals` 仍然不具備完全診斷意義

## 本輪結論

本輪最重要的結論不是「今天沒有 setup」，而是：

- 系統架構上已更接近 Martin Luk
- universe generation 已開始支援 short-side 候選
- 但資料層仍未完全跟上新的 universe
- 因此掃描結果仍然偏向舊 universe 的限制

## 下一步接力建議

接下來優先順序應該是：

### Step 1. 先補齊新 universe 的 OHLCV 資料

目標：

- 確保 `auto_candidates.txt` 裡的新股票真的有：
  - `1d`
  - `1h`
  - `15m`

需要確認：

- `update_universe_and_data.py` 是否真的把新 universe 全部送進資料抓取
- 是否有 `resume` / cache / progress state 讓新股票被錯誤略過
- 是否有 provider / timeout / symbol normalization 問題

### Step 2. 資料補齊後，重新跑 `scan-v2`

目標：

- 看新的 short-side screens 對 base setup 數量是否有改善
- 確認是否開始出現 short-side setup

### Step 3. 如果仍然沒有 base setup，再做 rejection diagnostics

目標：

- 不是只看 `0` 結果
- 而是看每一檔為什麼被刷掉

重點要回答：

- 是 universe 還不對
- 還是 setup 條件太嚴
- 還是 regime / sector filter 把它們刷掉
- 還是 short detector 本身還不夠像 Martin Luk

### Step 4. 之後再做 Script C2

等資料層和 setup 層更穩後，再做：

- `SHORT_VWAP_FAIL`

這樣比較合理。

## 如果下一輪要直接接手，建議從這裡開始

先做這個：

1. 檢查 `update_universe_and_data.py` 為什麼新 candidate 沒有完整進到本地 OHLCV
2. 補齊新 universe 的資料
3. 重新跑 `scan-v2`
4. 產生新的 candidate / base setup 結果

## 目前可直接參考的檔案

配置與流程：

- `config/research.yaml`
- `docs/martin_luk_workflow_guide_zh_2026-03-19.md`
- `docs/martin_luk_scan_to_tradingview_workflow_2026-03-19.md`

TradingView：

- `tradingview/ml_script_a_attention_router.pine`
- `tradingview/ml_script_b_setup_validator.pine`
- `tradingview/ml_script_c1_short_prev_hour_low_break.pine`

資料與輸出：

- `config/universe/auto_candidates.txt`
- `outputs/universe_updates/finviz_candidates.csv`
- `outputs/signals/candidates/candidates_2026-03-19.json`
- `scan_v2_2026-03-19.csv`
