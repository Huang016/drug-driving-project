# 外部公開資料（預防建議用）

`hotspot_pipeline/build_prevention.py` 讀這兩個檔案，產生 `web/data/prevention.json`。

| 檔案 | 來源 | 授權 | 下載日期 |
|---|---|---|---|
| `speed_cameras.csv` | 內政部警政署「測速執法設置點」，政府資料開放平臺 dataset 7320 | 政府資料開放授權條款第 1 版 | 2026-11-02 |
| `schools_osm.csv` | OpenStreetMap（Overpass API，台灣境內 `amenity=school`），只留名稱含國小、國中的學校 | © OpenStreetMap contributors，ODbL | 2026-11-02 |

`speed_cameras.csv` 只有固定式測速照相，不含移動式、區間測速與國道。OpenStreetMap 的學校名單可能有缺漏。
