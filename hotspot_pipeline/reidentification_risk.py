"""Opening evidence: how many people in the raw police data are the only record
matching a handful of facts an outsider could plausibly know."""
from pathlib import Path

import pandas as pd

SOURCE = Path(__file__).resolve().parent.parent / "PETsARD_Dataset_A.csv"
KNOWN_FACTS = {
    "日期＋時段＋鄉鎮": ["event_date", "event_hour", "county", "district"],
    "日期＋時段＋鄉鎮＋年齡層＋性別": ["event_date", "event_hour", "county", "district", "suspect_age_group", "suspect_gender"],
}


def main():
    a = pd.read_csv(SOURCE, low_memory=False).dropna(subset=["district"])
    print(f"  原始警政資料 {len(a):,} 筆")
    for label, columns in KNOWN_FACTS.items():
        group_size = a.groupby(columns)[columns[0]].transform("size")
        print(f"  只知道「{label}」→ {(group_size == 1).mean():.1%} 的人是唯一的一筆")


if __name__ == "__main__":
    main()
