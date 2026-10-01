# 毒駕熱區：跨機構隱私保護流程

三個機構（警政 A、檢驗 B、監理 C）在原始資料不離開機構的前提下，找出共同有紀錄的人，合併必要欄位，做出鄉鎮層級的毒駕熱區地圖。

流程：PSI 找交集 → 只傳交集需要的欄位（全程 PQC 加密）→ PETsARD 產生合成資料 → 鄉鎮熱區統計 → 發布前加差分隱私。

## 一次跑完

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt     # 需要 Python 3.10 或 3.11
bash run_demo.sh            # 直接跑完，約 2 分鐘
bash run_demo.sh --pause    # 每個階段停下來等 Enter，錄影用
bash run_demo.sh --pause 2  # 換一個隱私預算 ε（預設 1）
```

跑完後用瀏覽器打開 `hotspot_pipeline/results/hotspot_map.html`。

## 各階段在哪裡

| 階段 | 做什麼 | 程式 |
|---|---|---|
| 一 | PSI：身分識別碼雜湊到 Ed25519 曲線上，三個機構各用自己的私鑰盲化一次，協調者比對三重盲化值得到交集 | `psi_pqc/psi.py`、`psi_pqc/run_psi.py` |
| 一、二的傳輸 | 每則訊息用 ML-KEM-768 封裝金鑰、AES-256-GCM 加密；密文會存在 `psi_pqc/wire/` | `psi_pqc/pqc_channel.py` |
| 二 | 各機構只送交集中的人的必要欄位：A 送縣市與鄉鎮，B 送 `severity_score`，C 送 `recidivism_count` | `psi_pqc/run_psi.py` |
| 三 | PETsARD 合成資料，並評估保真度與三種隱私攻擊（單挑、連結、推論） | `hotspot_pipeline/run_stage3.sh` |
| 四、五 | 依鄉鎮統計人數與平均嚴重度，加 Laplace 雜訊後才發布，並畫成地圖 | `hotspot_pipeline/hotspot_dp.py`、`hotspot_pipeline/make_hotspot_map.py` |

測試：`cd psi_pqc && ../.venv/Scripts/python test_psi_pqc.py`

## 資料

| 檔案 | 內容 |
|---|---|
| `PETsARD_Dataset_A.csv` | 警政端原始資料：114 年道安事故當事人 403,088 筆。`is_drug_related` 與 `prior_offense_flag` 是推算欄位，見下方假設 |
| `PETsARD_Dataset_A_aligned.csv` | 模擬母體中的 A：8,000 筆，其中 571 筆毒駕事件對應 B 的陽性個案 |
| `dataset_b_aligned.csv` | 模擬母體中的 B：5,000 人，陽性 1,142 人 |
| `handoff_master_population.csv` | B 交給 A、C 的共用名單 |
| `三方資料生成一致性規格書.md` | A、B、C 怎麼對齊。**先讀開頭的修訂段落** |

C 的檔案（`dataset_c_aligned.csv`）還沒有。放進專案根目錄後，流程會自動改用它的 ID 清單並把 `recidivism_count` 併進分析表；在那之前，C 的 ID 清單是依規格書的納入規則暫時產生的，不提供任何欄位。

## 哪些數字是假設

模擬資料裡沒有官方來源、需要在報告中標明的設定：

| 設定 | 值 | 位置 |
|---|---|---|
| 深夜（22–05 時）事故是毒駕的機率，相對白天的倍率 | 2.5–3 倍 | `rederive_drug_flag_A.py` |
| B 的陽性個案中，因駕駛被查獲而出現在 A 的比例 | 50% | `generate_aligned_three_party_datasets.py` |
| 毒駕事件中的累犯比例 | 約 89% | 原始 A 既有的推算結果 |
| 隱私預算 ε、最小顯示人數 | 1、5 人 | `hotspot_pipeline/hotspot_dp.py` |

有官方來源的：B 的陽性率、物質比例、年齡與性別分布（藥物濫用案件暨檢驗統計年報），各縣市毒駕比例，以及物質對應的法定毒品級別。

## 已知限制

- **交集只有 571 人。** PETsARD 的評測要再切出對照組，剩四百多筆，分數不穩定；單挑風險目前高於建議值 0.09。鄉鎮有 367 個，多數鄉鎮只有 0–3 人，加了 DP 雜訊後只有人數較多的鄉鎮排名可信。B 放大後應會改善，尚未驗證。
- **各次執行的數字會有些微不同。** 盲化私鑰、合成資料都含隨機性；DP 雜訊則是固定種子。
- **平均嚴重度在地圖上沒有意義。** 模擬資料中 B 的嚴重度與 A 的地點無關，所以地圖只用人數。
- **PSI 假設各方誠實執行協定。** 協調者除了三方交集，也看得出任兩個機構的共同人數；每個機構會知道自己哪些人在交集裡。
- **加密通道只保密、不驗證寄件者。** 正式部署要再加後量子簽章（ML-DSA）。`kyber-py` 是純 Python 參考實作，未防護時序側通道。
- **PETsARD 1.10.1 的兩個問題已繞過，沒有改它的套件：** 預設的離群值移除會刪掉佔比小的類別，所以前處理關掉該步驟；合成與評測分開執行。

## 另一條線：原始 A 的合成資料評測

`petsard_run/` 是直接對 40 萬筆原始 A 做合成與 Privacy／Fidelity／Utility 評測的流程，用來比較不同合成方法，與上面的三方流程分開。
