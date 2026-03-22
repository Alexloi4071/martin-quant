# Martin Luk 全流程使用說明（中文版）2026-03-19

## 1. 這套流程的核心概念

這個專案不是要讓 TradingView 自己去全市場找股票。

正確分工是：

1. Python 掃描先決定今天該看哪些股票
2. TradingView A/B/C 指標再去判斷：
   - 有沒有開始動
   - 結構是否還有效
   - 是否出現真正的交易 trigger

一句話：

- Python 決定看誰
- A 決定今天有沒有活性
- B 決定高時間框架結構對不對
- C 決定能不能打

## 2. 每天實際流程

### 開盤前

1. 更新資料與候選池
2. 執行 `scan-v2`
3. 看輸出的 candidate 檔案
4. 把今日候選股放進 TradingView watchlist

建議 watchlist：

- `ML_SHORT`
- `ML_LONG`

### 盤中

1. 用 Script A 看哪一檔開始有動作
2. 用 Script B 看這檔是否仍符合 Martin 結構
3. 用 Script C 看是否出現真正交易信號

### 盤後

1. 回看今天有沒有 A / B / C 對齊的股票
2. 記錄有 signal 的股票
3. 記錄沒有 signal 但很接近的 near miss
4. 下次再調整 universe 與 trigger 條件

## 3. 先跑什麼命令

目前最基本命令是：

```powershell
$env:PYTHONPATH='src'
python -m martin_quant.cli.main scan-v2 --no-alerts
```

如果你要強制市場環境測試，例如偏空研究：

```powershell
$env:PYTHONPATH='src'
python -m martin_quant.cli.main scan-v2 --regime BEAR --no-alerts
```

輸出重點檔案：

- `outputs/signals/candidates/candidates_YYYY-MM-DD.json`
- `outputs/signals/candidates_YYYY-MM-DD.csv`
- `outputs/signals/long_symbols_YYYY-MM-DD.txt`
- `outputs/signals/short_symbols_YYYY-MM-DD.txt`

## 4. 如果掃描結果沒有 base setup，要怎麼看

這是最重要的判讀問題。

### 先講正確解讀

如果掃描結果是：

- `base_setup_count = 0`

意思不是：

- 今天市場一定沒有機會

真正意思是：

- 目前這批股票沒有通過現在的 base setup 規則
- 或者現在的股票池與當前市場問題不匹配
- 或者目前條件太嚴格

### 這種情況下不要做的事

不要：

- 硬把今天這批股票塞進 C 去等交易信號
- 因為沒有 base setup，C 的意義會變很差
- 也不要把 A 單獨當交易依據

### 這種情況下應該做的事

1. 先看今天 candidate universe 是不是偏掉了
2. 檢查市場 regime 是不是和股票池方向不一致
3. 如果是弱市或空頭，優先看 short-side screens 是否需要重新刷新
4. 今天可以只保留流程測試，不做正式交易候選

### 沒有 base setup 時，A/B/C 怎樣用

#### A

可以看，但只當：

- 市場熱點提醒
- 某檔今天有沒有活性

不能當交易信號。

#### B

如果連 base setup 都沒有，B 通常也不該成為交易依據。

最多只能當成：

- 研究觀察
- 看哪些股票差一點就成形

#### C

不建議啟用為正式交易流程。

因為：

- C 是 execution trigger
- 它應該建立在「先有 setup」的前提上

如果沒有 base setup 還硬看 C，通常會把很多雜訊誤當 signal。

### 簡單結論

如果沒有 base setup：

- 今天不進正式 A/B/C 交易流程
- 最多只做流程驗證和觀察
- 重點應該放在 universe 與條件調整

## 5. 如果掃描結果有 base setup，要怎麼用 A/B/C

這才是正式流程。

### 第一步：先挑股票

不是所有過 setup 的股票都要盯。

建議：

- 空頭或弱市：優先挑 `5~10` 檔 short
- 多頭：優先挑 `5~10` 檔 long
- 震盪：兩邊都縮小，只留 A 級 setup

只有這些股票才放進 TradingView watchlist。

### 第二步：看 A

A 的用途：

- 今天有沒有開始動
- 有沒有值得你立即看圖

#### A 亮了代表什麼

- 這檔今天有活性
- 可以開始注意

#### A 沒亮代表什麼

- 結構可能還在，但今天還沒發動
- 不必一直盯盤

### 第三步：看 B

B 的用途：

- 確認高時間框架結構仍有效
- 避免把隨機 intraday 波動誤判成 setup

#### B 亮了代表什麼

- 這檔目前仍符合方向結構
- 有資格進入 C 的觀察

#### B 沒亮代表什麼

- 即使今天很活，也可能不是你要的 Martin 類型 setup

### 第四步：看 C

C 才是真正的進場 trigger。

目前第一個版本是：

- `ML_SHORT_PREV_HOUR_LOW_BREAK`

未來還會有：

- `SHORT_VWAP_FAIL`
- `LONG_RECLAIM`

## 6. A/B/C 的實戰判讀表

### 情況 1：只有 A

意思：

- 今天有活性
- 但高時間框架未必對

處理：

- 只觀察
- 不當正式交易信號

### 情況 2：只有 B

意思：

- 結構是對的
- 但今天還沒發動

處理：

- 保留在 watchlist
- 不急著盯盤

### 情況 3：A + B

意思：

- 今天有活性
- 高時間框架也對

處理：

- 這才值得等 C
- 屬於高優先觀察股

### 情況 4：A + B + C

意思：

- 候選股正確
- 結構正確
- 盤中 trigger 也出現

處理：

- 這才是最接近 Martin Luk 的正式交易信號

## 7. 一句話記住整個流程

### 沒有 base setup

- 不進正式交易流程
- 只做觀察與流程驗證

### 有 base setup

- 先選股票
- 再看 A/B
- 最後等 C

### 真正值得重視的情況

只有這種：

- 股票在今日 candidate list 裡
- 有 base setup
- A 亮
- B 亮
- C 出 trigger

## 8. 現在的實務建議

### 如果今天沒有 base setup

你今天應該做的是：

1. 不要強行交易
2. 不要硬看 C
3. 檢查 universe 是否太小或太偏 long
4. 如果市場偏弱，優先刷新 short-side 候選池

### 如果之後有 base setup

你應該做的是：

1. 只把最好的幾檔放進 `ML_SHORT` / `ML_LONG`
2. A 用來提醒你哪一檔開始動
3. B 用來確認結構仍正確
4. C 才用來判斷能不能交易

## 9. IBKR 是否需要登入

目前這個流程：

- scan
- A/B/C
- webhook
- Telegram

都不需要登入 IBKR。

只有要進入：

- paper trade
- live trade
- 自動下單

才需要 IBKR。

## 10. 下一步建議

現在最合理的順序是：

1. 用新的 short-side screens 重新刷新 universe
2. 再跑 `scan-v2`
3. 如果有 base setup，再把股票丟進 A/B/C 流程
4. 如果仍然沒有，就檢查條件是否太嚴或市場是否真的沒型
