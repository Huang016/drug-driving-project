"""Collapse the official 114年 A1/A2 party-level files (one row per party) to one row per accident,
keeping what we need to enrich Dataset A: deaths, injuries, accident class (A1/A2) and coordinates."""
import glob
import re
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
SRC = HERE / "source" / "official_114"
COLS = ["發生日期", "發生時間", "事故類別名稱", "發生地點", "死亡受傷人數", "經度", "緯度"]


def main():
    files = sorted(glob.glob(str(SRC / "*.csv")))
    files = [f for f in files if "A1" in f or "A2" in f or "U4ea4" in f]
    parts = [pd.read_csv(f, usecols=COLS, dtype=str, encoding="utf-8-sig") for f in files if pd.read_csv(f, nrows=0, encoding="utf-8-sig").columns.isin(COLS).sum() == len(COLS)]
    df = pd.concat(parts, ignore_index=True)
    df = df.dropna(subset=["死亡受傷人數", "經度", "緯度"])  # a handful of rows lack these fields
    acc = df.drop_duplicates(["發生日期", "發生時間", "發生地點", "經度", "緯度"]).copy()
    dead_inj = acc["死亡受傷人數"].str.extract(r"死亡(\d+);受傷(\d+)").astype(int)
    out = pd.DataFrame({
        "date": pd.to_datetime(acc["發生日期"], format="%Y%m%d"),
        "hour": acc["發生時間"].str.zfill(6).str[:2].astype(int),
        "lat": acc["緯度"].astype(float).round(6), "lon": acc["經度"].astype(float).round(6),
        "accident_class": acc["事故類別名稱"], "death_count": dead_inj[0], "injury_count": dead_inj[1],
    })
    out.to_parquet(HERE / "source" / "official_114_accidents.parquet", index=False)
    print(f"party rows {len(df):,} -> accidents {len(out):,}; A1 {(out.accident_class=='A1').sum():,}")


if __name__ == "__main__":
    main()
