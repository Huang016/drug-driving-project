"""Re-derive is_drug_related / prior_offense_flag in PETsARD_Dataset_A.csv.

The number of drug-related events per county is kept exactly as it was (that part
came from the real county-level rates). Only which rows inside each county carry
the flag changes, so that the flag also depends on driver type, age, gender and hour.
"""
from pathlib import Path

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
A_PATH = BASE_DIR / "PETsARD_Dataset_A.csv"
SEED = 20261002

MOTOR_VEHICLE_KINDS = ["機車", "小客車(含客、貨兩用)", "小貨車", "大貨車", "大客車", "曳引車", "半聯結車", "全聯結車"]

# official: 藥物濫用案件暨檢驗統計年報 (113), gender share and age share by gender
GENDER_SHARE = {"男": 0.829, "女": 0.171}
AGE_SHARE = {
    "男": {"19歲以下": 0.030, "20-29歲": 0.088, "30-39歲": 0.164, "40-49歲": 0.344, "50-59歲": 0.268, "60歲以上": 0.107},
    "女": {"19歲以下": 0.021, "20-29歲": 0.092, "30-39歲": 0.177, "40-49歲": 0.413, "50-59歲": 0.225, "60歲以上": 0.072},
}
# A's age groups mapped onto the report's groups; 0-9 and 未知 cannot be a drug-driving suspect
AGE_TO_REPORT_GROUP = {
    "10-19": "19歲以下", "20-29": "20-29歲", "30-39": "30-39歲", "40-49": "40-49歲", "50-59": "50-59歲",
    "60-69": "60歲以上", "70-79": "60歲以上", "80+": "60歲以上",
}

# ASSUMPTION, no official source: how much more likely a late-night event is drug-related than a daytime one
HOUR_MULTIPLIER = {**{h: 1.0 for h in range(6, 18)}, **{h: 1.5 for h in range(18, 22)}, 22: 2.5, 23: 2.5, **{h: 3.0 for h in range(0, 6)}}


def row_weights(a: pd.DataFrame) -> pd.Series:
    report_group = a["suspect_age_group"].map(AGE_TO_REPORT_GROUP)
    eligible = (
        a["vehicle_type"].str.split("-").str[0].isin(MOTOR_VEHICLE_KINDS)
        & report_group.notna()
        & a["suspect_gender"].isin(GENDER_SHARE)
    )

    # importance weight: target share of a (gender, age) cell over its share among eligible rows
    cell = pd.DataFrame({"gender": a["suspect_gender"], "age": report_group})[eligible]
    population_share = cell.value_counts(normalize=True)
    target = {(g, age): GENDER_SHARE[g] * share for g in AGE_SHARE for age, share in AGE_SHARE[g].items()}
    demographic = pd.Series(
        [target[key] / population_share[key] for key in zip(cell["gender"], cell["age"])], index=cell.index
    )

    weights = pd.Series(0.0, index=a.index)
    weights[eligible] = demographic * a.loc[eligible, "event_hour"].map(HOUR_MULTIPLIER)
    return weights


def main():
    a = pd.read_csv(A_PATH, low_memory=False)
    rng = np.random.default_rng(SEED)
    prior_rate = a.loc[a["is_drug_related"] == 1, "prior_offense_flag"].mean()
    weights = row_weights(a)

    is_drug = np.zeros(len(a), dtype=int)
    for county, n_drug in a.groupby("county")["is_drug_related"].sum().items():
        if n_drug == 0:
            continue
        rows = np.flatnonzero((a["county"] == county).to_numpy())
        w = weights.to_numpy()[rows]
        is_drug[rows[rng.choice(len(rows), size=int(n_drug), replace=False, p=w / w.sum())]] = 1
    prior = ((is_drug == 1) & (rng.random(len(a)) < prior_rate)).astype(int)

    # only the last two fields of each line are rewritten, everything else stays byte-identical
    lines = A_PATH.read_bytes().split(b"\r\n")
    header, body, tail = lines[0], lines[1:len(a) + 1], lines[len(a) + 1:]
    assert header.endswith(b"is_drug_related,prior_offense_flag") and len(body) == len(a)
    body = [line.rsplit(b",", 2)[0] + b",%d,%d" % (d, p) for line, d, p in zip(body, is_drug, prior)]
    A_PATH.write_bytes(b"\r\n".join([header, *body, *tail]))

    print(f"drug-related: {is_drug.sum()}  prior offense: {prior.sum()}  (prior rate among drug-related {prior_rate:.3f})")


if __name__ == "__main__":
    main()
