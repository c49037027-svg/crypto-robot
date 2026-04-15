# AI 量化交易機器人

## 快速啟動

```bash
# 1. 安裝依賴
pip install -r requirements.txt

# 2. 設定環境變數
cp .env.example .env
# 編輯 .env，填入你的 ANTHROPIC_API_KEY

# 3. 檢查配置
python main.py --check

# 4. 單次測試
python main.py --test

# 5. 啟動機器人 (模擬模式)
python main.py
```

## 代理人架構

```
OrchestratorAgent (總指揮)
├─ MarketAnalystAgent    → 技術分析 + 趨勢判斷 (Claude AI)
├─ SignalGeneratorAgent  → 買賣信號 + 信心分數 (Claude AI)
├─ RiskManagerAgent      → SL/TP 計算 + 盈虧比驗證
├─ ExecutionAgent        → 下單執行
└─ PositionMonitorAgent  → 持倉監控 + 移動止損
```

## 風險管理

- **止損**: entry ± 2x ATR (自動計算)
- **止盈**: entry ± 3x ATR (確保最低 1:1.5 盈虧比)
- **移動止損**: 啟動後自動跟隨價格
- **每筆風險**: 帳戶 1%
- **日損限額**: 帳戶 3%
- **最大回撤**: 10%
