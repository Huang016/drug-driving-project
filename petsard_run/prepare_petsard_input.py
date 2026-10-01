"""Build the PETsARD input table from PETsARD_Dataset_A.csv.

Generalization applied before synthesis:
  event_date        -> event_month, event_weekday
  lat_raw / lon_raw -> dropped (location kept at county + district level)
  county, district  -> one "location" column, so a synthetic row can never
                       pair a district with the wrong county
  vehicle_type      -> vehicle_kind (the part before the dash)
prior_offense_flag is left out: it is 1 only for drug-related rows, so it gives
the is_drug_related answer away to any model or attacker.
"""
import sys
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
SOURCE = BASE_DIR.parent / "PETsARD_Dataset_A.csv"
SEED = 20261002
LOCATION_SEP = "|"
TRAIN_RATIO = 0.8
# PETsARD 1.10.1 mlutility fails on a numeric classification target, anonymeter inference fails on a text one
DRUG_LABEL = {1: "是", 0: "否"}
WEEKDAYS = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]


def generalize(a: pd.DataFrame) -> pd.DataFrame:
    date = pd.to_datetime(a["event_date"], format="%Y/%m/%d")
    return pd.DataFrame({
        "event_month": date.dt.month,
        "event_weekday": date.dt.dayofweek.map(dict(enumerate(WEEKDAYS))),
        "event_hour": a["event_hour"],
        "location": a["county"] + LOCATION_SEP + a["district"],
        "vehicle_kind": a["vehicle_type"].str.split("-").str[0],
        "suspect_age_group": a["suspect_age_group"],
        "suspect_gender": a["suspect_gender"],
        "casualty_count": a["casualty_count"],
        "is_drug_related": a["is_drug_related"],
    })


def with_text_label(table: pd.DataFrame) -> pd.DataFrame:
    return table.assign(is_drug_related=table["is_drug_related"].map(DRUG_LABEL))


def main():
    n_rows = int(sys.argv[1]) if len(sys.argv) > 1 else None
    a = pd.read_csv(SOURCE, low_memory=False).dropna(subset=["district"])
    table = generalize(a)
    if n_rows:
        table = table.sample(n=n_rows, random_state=SEED)

    # fixed split: the synthesizer only ever sees the training part
    train = table.sample(frac=TRAIN_RATIO, random_state=SEED)
    control = table.drop(train.index)
    train.to_csv(BASE_DIR / "A_train.csv", index=False)
    control.to_csv(BASE_DIR / "A_control.csv", index=False)
    with_text_label(train).to_csv(BASE_DIR / "A_train_label.csv", index=False)
    with_text_label(control).to_csv(BASE_DIR / "A_control_label.csv", index=False)
    print(f"train {len(train)} rows, control {len(control)} rows, drug-related {table['is_drug_related'].mean():.3%}, locations {table['location'].nunique()}")


if __name__ == "__main__":
    main()
