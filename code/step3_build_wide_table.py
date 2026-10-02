"""Step 3 (inside the TTP's controlled environment): denormalize A+B+C into one wide table,
following PETsARD's multi-table guidance (one row per person, 1:1 here).

Outputs
  ttp/wide_real.csv     every matched field + psi_token; used for the DP map, never released
  ttp/wide_petsard.csv  synthesis input: no ids, constant/derivable columns removed,
                        dates turned into day offsets, high-cardinality fields coarsened
"""
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent
INBOX = BASE / "ttp" / "inbox"
TTP = BASE / "ttp"
YEAR_START = pd.Timestamp("2025-01-01")

VEHICLE_GROUP = {  # vehicle_type prefix -> synthesis group
    "機車": "機車", "小客車(含客、貨兩用)": "小客車", "小貨車": "貨車", "大貨車": "貨車",
    "曳引車": "貨車", "半聯結車": "貨車", "全聯結車": "貨車", "大客車": "大客車",
}


def load(name: str) -> pd.DataFrame:
    return pd.read_csv(INBOX / f"{name}.csv", encoding="utf-8-sig")


def build_real() -> pd.DataFrame:
    a = load("A")
    b = load("B").drop(columns=["age_group", "gender"])       # same values as A (checked below)
    c = load("C").rename(columns={"age_group": "c_age_group", "gender": "c_gender"})
    wide = a.merge(b, on="psi_token", how="inner", validate="1:1").merge(c, on="psi_token", how="inner", validate="1:1")
    assert (wide["suspect_age_group"] == wide["c_age_group"]).all() and (wide["suspect_gender"] == wide["c_gender"]).all()
    return wide.drop(columns=["c_age_group", "c_gender"])


REGION = {
    "臺北市": "北部", "新北市": "北部", "基隆市": "北部", "桃園市": "北部", "新竹市": "北部", "新竹縣": "北部", "宜蘭縣": "北部",
    "苗栗縣": "中部", "臺中市": "中部", "彰化縣": "中部", "南投縣": "中部", "雲林縣": "中部",
    "嘉義市": "南部", "嘉義縣": "南部", "臺南市": "南部", "高雄市": "南部", "屏東縣": "南部",
    "花蓮縣": "東部", "臺東縣": "東部", "澎湖縣": "離島", "金門縣": "離島", "連江縣": "離島",
}
SANCTION_LAG_BINS = [-1, 30, 60, 120, 500]
SANCTION_LAG_LABELS = ["0~30", "31~60", "61~120", "121~500"]
WEEKDAY = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]


def sanction_label(w: pd.DataFrame) -> pd.Series:
    years = w["sanction_years"].astype("Int64").astype(str)
    return pd.Series(np.where(w["lifetime_ban"] == "是", "吊銷終身", w["sanction_type"] + years + "年"), index=w.index)


def build_synthesis_input(w: pd.DataFrame) -> pd.DataFrame:
    """v3 granularity (A∩B∩C = 5,878 people): county, weekday and exact hour are kept.
    v3 adds accident_class (A1 = someone died) and injury_count from the official accident data, and
    the §35 sanction as one label (吊扣2年/3年/4年, 吊銷3年, 吊銷終身).
    Still coarsened: date -> month + weekday, district/coordinates dropped, vehicle -> 4 groups,
    sanction lag -> day ranges."""
    event = pd.to_datetime(w["event_date"])
    unknown_region = set(w["county"]) - set(REGION)
    assert not unknown_region, unknown_region
    return pd.DataFrame({
        "event_month": event.dt.month,
        "weekday": event.dt.dayofweek.map(lambda i: WEEKDAY[i]),
        "event_hour": w["event_hour"],
        "county": w["county"],
        "vehicle_group": w["vehicle_type"].str.split("-").str[0].map(VEHICLE_GROUP),
        "age_group": w["suspect_age_group"],
        "gender": w["suspect_gender"],
        "accident_class": w["accident_class"],                        # A1 = at least one death
        "injury_count": w["injury_count"].clip(upper=4),              # 4 = 4 or more
        "specimen_type": w["specimen_type"],
        "positive_substance": w["positive_substance"],
        "test_offset_days": (pd.to_datetime(w["test_date"]) - event).dt.days,  # 0-3
        "recidivism_count": w["recidivism_count"],
        "sanction_lag": pd.cut((pd.to_datetime(w["sanction_date"]) - event).dt.days,
                               SANCTION_LAG_BINS, labels=SANCTION_LAG_LABELS).astype(str),
        "sanction": sanction_label(w),
    })


def main():
    wide = build_real()
    wide.to_csv(TTP / "wide_real.csv", index=False)
    syn_in = build_synthesis_input(wide)
    assert syn_in.notna().all().all() and not (syn_in == "nan").any().any()
    syn_in.to_csv(TTP / "wide_petsard.csv", index=False)
    print(f"wide_real.csv: {wide.shape}, wide_petsard.csv: {syn_in.shape}")
    print(syn_in.nunique().to_string())


if __name__ == "__main__":
    main()
