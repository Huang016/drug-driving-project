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
| 一 | PSI：身分識別碼雜湊到 Ed25519 曲線上，三個機構各用自己的私鑰盲化一次，協調者比對三重盲化值得到交集 | `psi_pqc/psi.py`、`psi_pqc/institution.py` |
| 一、二的傳輸 | 每則訊息用 ML-KEM-768 封裝金鑰、AES-256-GCM 加密，並由寄件方用 ML-DSA-65 簽章；收件方先驗簽章再解密 | `psi_pqc/pqc_channel.py` |
| 二 | 各機構只送交集中的人的必要欄位：A 送縣市與鄉鎮，B 送 `severity_score`，C 送 `recidivism_count` | `psi_pqc/institution.py` |
| 三 | PETsARD 合成資料，並評估保真度與三種隱私攻擊（單挑、連結、推論） | `hotspot_pipeline/run_stage3.sh` |
| 四、五 | 依鄉鎮統計人數與平均嚴重度，加 Laplace 雜訊後才發布，並畫成地圖 | `hotspot_pipeline/hotspot_dp.py`、`hotspot_pipeline/make_hotspot_map.py` |

測試：`cd psi_pqc && ../.venv/Scripts/python test_psi_pqc.py`

## 階段一、二的兩種跑法

協定和加密完全相同，差別只在四個角色（警政 A、檢驗 B、監理 C、協調者 K）怎麼執行。

**一支程式扮演四個角色**（`run_demo.sh` 用的）：`python run_psi.py`。最快，適合反覆測試。四方的私鑰都在同一支程式裡，靠程式結構區隔。

**每個角色各自一支程式，透過網路連線**：`python node.py A`（B、C、K 同理）。每支程式只讀自己的資料檔和自己的私鑰，收到的訊息原樣存在 `psi_pqc/received/<角色>/`。

在一台電腦上，開四個終端機：

```bash
cd psi_pqc
../.venv/Scripts/python node.py K      # 四個視窗分別執行 K、A、B、C，順序不拘
```

或用 `bash run_nodes_local.sh` 一次啟動四支並印出各自的紀錄。

在三、四台電腦上：

1. 每台電腦都有這個專案，並放好自己那份資料檔（監理端必須有 `dataset_c_aligned.csv`）。
2. 各自執行 `python node.py <角色> --init` 產生金鑰。私鑰留在 `keys/private/`，不要傳給任何人。
3. 把四個 `keys/public/<角色>.json` 互相交換，放進每台電腦的 `keys/public/`。交換管道要可信（當面或用組內群組傳），否則有人可以冒充某一方。
4. 把 `network.json` 裡的 `host` 改成各台電腦的區網 IP，四台用同一份。四台要在同一個網路，Windows 防火牆第一次會跳出詢問，選允許。
5. 各自執行 `python node.py <角色>`。協調者可以另外用一台，或跟其中一個機構同一台。

協調者跑完會寫出 `hotspot_pipeline/analysis_table.csv`，之後的階段三到五在協調者那台執行。

## 資料

| 檔案 | 內容 |
|---|---|
| `PETsARD_Dataset_A.csv` | 警政端原始資料：114 年道安事故當事人 403,088 筆。`is_drug_related` 與 `prior_offense_flag` 是推算欄位，見下方假設 |
| `PETsARD_Dataset_A_aligned.csv` | 模擬母體中的 A：80,000 筆，其中 2,351 筆毒駕事件對應 B 的陽性個案 |
| `dataset_b_aligned.csv` | 模擬母體中的 B：50,000 人，陽性 11,756 人 |
| `handoff_master_population.csv` | B 交給 A、C 的共用名單 |
| `三方資料生成一致性規格書.md` | A、B、C 怎麼對齊。**先讀開頭的修訂段落** |

C 的檔案（`dataset_c_aligned.csv`）還沒有。放進專案根目錄後，流程會自動改用它的 ID 清單並把 `recidivism_count` 併進分析表；在那之前，C 的 ID 清單是依規格書的納入規則暫時產生的，不提供任何欄位。

## 哪些數字是假設

模擬資料裡沒有官方來源、需要在報告中標明的設定：

| 設定 | 值 | 位置 |
|---|---|---|
| 深夜（22–05 時）事故是毒駕的機率，相對白天的倍率 | 2.5–3 倍 | `rederive_drug_flag_A.py` |
| B 的陽性個案中，因駕駛被查獲而出現在 A 的比例 | 20% | `generate_aligned_three_party_datasets.py` |
| 毒駕事件中的累犯比例 | 約 89% | 原始 A 既有的推算結果 |
| 隱私預算 ε、最小顯示人數 | 1、5 人 | `hotspot_pipeline/hotspot_dp.py` |

有官方來源的：B 的陽性率、物質比例、年齡與性別分布（藥物濫用案件暨檢驗統計年報），各縣市毒駕比例，以及物質對應的法定毒品級別。

## 已知限制

- **B 放大後階段三到五還沒重跑。** 交集從 571 人變成 2,351 人，分布在 271 個鄉鎮（中位數 4 人）。交集只有 571 人時，PETsARD 的分數不穩定、單挑風險高於建議值 0.09、多數鄉鎮只有 0–3 人；放大後應會改善，尚未驗證。
- **各次執行的數字會有些微不同。** 盲化私鑰、合成資料都含隨機性；DP 雜訊則是固定種子。
- **平均嚴重度在地圖上沒有意義。** 模擬資料中 B 的嚴重度與 A 的地點無關，所以地圖只用人數。
- **PSI 假設各方誠實執行協定。** 協調者除了三方交集，也看得出任兩個機構的共同人數；每個機構會知道自己哪些人在交集裡。
- **PSI 的盲化本身不是後量子安全的。** 它用橢圓曲線，量子電腦可以破解。ML-KEM 保護的是傳輸，所以外部攔截者拿不到盲化值，但參與協定的各方看得到。
- **公鑰要靠可信管道交換。** 簽章只能證明訊息來自持有某把私鑰的人，公鑰如果一開始就被掉包，簽章也沒用。
- **`kyber-py` 和 `dilithium-py` 是純 Python 參考實作**，未防護時序側通道，適合示範，不適合正式部署。
- **PETsARD 1.10.1 的兩個問題已繞過，沒有改它的套件：** 預設的離群值移除會刪掉佔比小的類別，所以前處理關掉該步驟；合成與評測分開執行。

## 另一條線：原始 A 的合成資料評測

`petsard_run/` 是直接對 40 萬筆原始 A 做合成與 Privacy／Fidelity／Utility 評測的流程，用來比較不同合成方法，與上面的三方流程分開。
