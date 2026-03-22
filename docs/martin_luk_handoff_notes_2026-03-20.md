# Martin Luk 接力紀錄與問題清單（2026-03-20）

## 本輪目標

延續 2026-03-19 的接力紀錄，確認為什麼 expanded universe 仍然沒有補齊本地 OHLCV，並把資料更新腳本的 fallback 行為補強。

## 本輪已完成事項

### 1. 確認 `scan-v2` 本身不是主因

已確認：

- `src/martin_quant/scripts/run_daily_scan_v2.py`
- `src/martin_quant/pipeline/data_pipeline.py`

`scan-v2` 只是讀取 `data/us_equities/{1d,1h,15m}` 既有 parquet。

因此昨日的 `Step 1: daily=11 intraday=11` 並不是掃描器額外過濾，而是本地資料目錄真的只有 11 檔完整 parquet。

### 2. 補強 `update_universe_and_data.py` 的 OHLCV fallback

已修改：

- `scripts/update_universe_and_data.py`
- `tests/test_update_universe_and_data.py`

本輪修正：

- `fetch_market_frames()` 新增 `FinnhubProvider` 作為第三層 OHLCV fallback
- `fetch_market_frames()` 遇到 provider 例外時，不再整個 symbol 立即中止，而是繼續嘗試下一層 fallback
- missing frame error 訊息現在會帶出 `available` frame summary
- 新增 / 更新測試，確認：
  - Finnhub fallback 會補上缺少的 timeframe
  - provider 丟出例外時，流程仍會繼續 fallback
  - resume 行為不被這次修改破壞

測試結果：

- `pytest -q tests\test_update_universe_and_data.py`
- `3 passed`

### 3. 實跑小範圍 refresh 驗證 fallback 行為

已執行：

- `$env:PYTHONPATH='src'; python scripts\update_universe_and_data.py --resume --max-symbols 6`

結果：

- 既有 5 檔維持 `resume_skip`
- 新嘗試的 `XEL`、`WULF` 仍失敗
- 但這次失敗已不再是 provider exception 直接中止，而是完整跑完 fallback 後仍然 `available: -`

## 本輪關鍵診斷結果

### 1. `Polygon` 對失敗 symbol 回空資料

已直接 probe：

- `XEL` daily/hour/15m -> 全部 `rows 0`

也就是：

- 至少在目前 key / 環境下，Polygon 對這批 symbol 沒有提供可寫入的 candles

### 2. `yfinance` 在目前環境對失敗 symbol 回異常空資料

在小範圍 refresh 中可見：

- `Failed to get ticker 'XEL'`
- `possibly delisted; No price data found`
- `Failed to get ticker 'WULF'`

這對 `XEL`、`WULF` 這類正常 ticker 並不合理，代表目前這條 fallback 在本環境不可靠。

### 3. `Finnhub` candle endpoint 對失敗 symbol 明確回 `403`

已直接 probe：

- `XEL` -> `HTTP Error 403: Forbidden`
- `WULF` -> `HTTP Error 403: Forbidden`

注意：

- 同一把 Finnhub key 仍可成功讀 `metadata` / `earnings`
- 但 `stock/candle` 對這些 symbol 沒權限或無 entitlement

## 本輪結論

目前 bottleneck 已從「程式流程 bug」縮小成「外部 OHLCV provider 能力 / 權限不足」：

- `scan-v2` 沒問題
- local parquet reader 沒問題
- refresh script 的 fallback 鏈已補強並通過測試
- 但目前可用的三層 OHLCV 來源對新 universe 仍無法提供可寫入資料：
  - Polygon -> 空
  - yfinance -> 空 / 異常
  - Finnhub candle -> 403

因此截至 2026-03-20，本地完整 OHLCV 仍然停留在原本 11 檔。

## 下一步接力建議

優先順序：

1. 確認 `POLYGON_API_KEY` 的 plan / entitlement 是否真的覆蓋這批 US equities candles
2. 確認 `FINNHUB_API_KEY` 是否有 `stock/candle` 權限，而不只是 metadata / earnings
3. 若上述權限無法補齊，應改接新的 OHLCV provider，而不是繼續調整掃描器
4. provider 問題解掉後，再重跑：
   - `python scripts\update_universe_and_data.py --resume`
   - `python -m martin_quant.cli.main scan-v2 --no-alerts`

## 本輪直接參考檔案

- `scripts/update_universe_and_data.py`
- `tests/test_update_universe_and_data.py`
- `outputs/universe_updates/refresh_state.csv`
- `outputs/universe_updates/symbol_progress.jsonl`
- `config/universe/auto_candidates.txt`
- `data/us_equities/1d`

## 2026-03-20 後續更新（OHLCV 問題已實際修復）

### 新增可用 fallback：Twelve Data

在 `scripts/update_universe_and_data.py` 已新增 Twelve Data OHLCV fallback，順序現在是：

- Polygon
- yfinance
- Finnhub
- Twelve Data

同時也補了 provider exception 容忍，因此即使 Polygon / Finnhub 回空或 403，流程仍會繼續往下補資料。

### 驗證結果

已驗證失敗樣本：

- `XEL`
- `WULF`

兩者現在都能成功寫出：

- `1d`
- `1h`
- `15m`

### refresh 後結果

完整 `--resume` 執行到 timeout 前，實際已把 candidate universe 的本地 OHLCV 補齊。

比對結果：

- `config/universe/auto_candidates.txt` unique symbols = `151`
- 本地 `data/us_equities/1d` unique symbols = `153`
- candidate 與本地 parquet 比對後，已無缺口

目前 `refresh_state.csv` 裡仍可看到失敗記錄：

- `NAN`
- `SLDE`
- `XPRO`

但它們不是目前 candidate universe 缺口，因此已不再阻塞 `scan-v2`。

### scan-v2 驗證

已執行：

- `$env:PYTHONPATH='src'; python -m martin_quant.cli.main scan-v2 --no-alerts`

結果：

- `Step 1: daily=153 intraday=153`
- 不再是先前的 `11 / 11`
- `base setup = 4`
- `final signals = 2`

代表原本「expanded universe 沒有真正進到本地 OHLCV」這個核心問題，已經被實際解除。
