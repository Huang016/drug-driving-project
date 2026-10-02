import hashlib
import random
from pathlib import Path

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
SALT = "innoserve2026-drugdriving"
SEED = 20261001
# A is this many times the size of B, so A grows with B (8000 rows for 5000 people)
A_TO_B_RATIO = 1.6
MAX_COORD_OFFSET = 0.3
POSITIVE_IN_A_RATE = 0.50
MOTOR_VEHICLE_KINDS = ["機車", "小客車(含客、貨兩用)", "小貨車", "大貨車", "大客車", "曳引車", "半聯結車", "全聯結車"]

AGE_ORDER = ["19歲以下", "20-29歲", "30-39歲", "40-49歲", "50-59歲", "60歲以上"]
MALE_AGE_WEIGHTS = [0.030, 0.088, 0.164, 0.344, 0.268, 0.107]
FEMALE_AGE_WEIGHTS = [0.021, 0.092, 0.177, 0.413, 0.225, 0.072]
MALE_AGE_PROBS = [w / sum(MALE_AGE_WEIGHTS) for w in MALE_AGE_WEIGHTS]
FEMALE_AGE_PROBS = [w / sum(FEMALE_AGE_WEIGHTS) for w in FEMALE_AGE_WEIGHTS]
DRUG_CLASSES = ["一級", "二級", "三級", "無"]
SUBSTANCE_CLASS = {"嗎啡": "一級", "安非他命": "二級", "甲基安非他命": "二級", "愷他命": "三級"}
CLASS_SEVERITY = {"一級": 3.0, "二級": 2.0, "三級": 1.0, "無": 0.0}


def to_shared_hashed_id(master_id: int, salt: str = SALT) -> str:
    master_id_str = f"{master_id:06d}"
    raw = f"{salt}:{master_id_str}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def make_b_consistent(b_raw: pd.DataFrame) -> pd.DataFrame:
    # test_result is kept as-is; substance, class and severity are made to follow it
    b = b_raw.copy()
    positive = b["test_result"] == "陽性"
    detected = b["positive_substance"] != "未檢出"
    weights = b.loc[detected, "positive_substance"].value_counts(normalize=True).sort_index()

    rng = np.random.default_rng(SEED)
    missing = positive & ~detected
    b.loc[missing, "positive_substance"] = rng.choice(weights.index, size=missing.sum(), p=weights.values)
    b.loc[~positive, "positive_substance"] = "未檢出"
    b["drug_class"] = b["positive_substance"].map(SUBSTANCE_CLASS).fillna("無")
    b["severity_score"] = b["drug_class"].map(CLASS_SEVERITY)
    return b


def build_handoff_master_population(b_raw: pd.DataFrame) -> pd.DataFrame:
    handoff = b_raw[["test_result", "drug_class", "age_group", "gender"]].copy()
    handoff.insert(0, "master_id", list(range(1, len(b_raw) + 1)))
    handoff.insert(1, "hashed_id", [to_shared_hashed_id(m) for m in handoff["master_id"]])
    return handoff[["master_id", "hashed_id", "age_group", "gender", "drug_class", "test_result"]].copy()


def make_b_aligned(b_raw: pd.DataFrame) -> pd.DataFrame:
    b_aligned = b_raw.copy()
    b_aligned.insert(0, "master_id", list(range(1, len(b_aligned) + 1)))
    b_aligned.insert(1, "hashed_id", [to_shared_hashed_id(m) for m in b_aligned["master_id"]])
    return b_aligned[[
        "master_id",
        "hashed_id",
        "test_date",
        "specimen_type",
        "test_result",
        "positive_substance",
        "drug_class",
        "age_group",
        "gender",
        "severity_score",
    ]].copy()


def sample_age_for_gender(gender: str) -> str:
    probs = MALE_AGE_PROBS if gender == "男" else FEMALE_AGE_PROBS
    return np.random.choice(AGE_ORDER, p=probs)


def load_a_source() -> pd.DataFrame:
    a_raw = pd.read_csv(BASE_DIR / "PETsARD_Dataset_A.csv", low_memory=False)
    a_raw = a_raw.dropna(subset=["district"])
    median = a_raw.groupby(["county", "district"])[["lat_raw", "lon_raw"]].transform("median")
    near_district = (
        ((a_raw["lat_raw"] - median["lat_raw"]).abs() <= MAX_COORD_OFFSET)
        & ((a_raw["lon_raw"] - median["lon_raw"]).abs() <= MAX_COORD_OFFSET)
    )
    return a_raw[near_district].reset_index(drop=True)


def build_a_aligned(handoff: pd.DataFrame, a_raw: pd.DataFrame) -> pd.DataFrame:
    # assumed share of B positives caught while driving; only these people exist in both A and B
    positive = handoff[handoff["test_result"] == "陽性"]
    linked = positive.sample(frac=POSITIVE_IN_A_RATE, random_state=SEED).sort_values("master_id")
    n_a_only = round(len(handoff) * A_TO_B_RATIO) - len(linked)

    # drug driving only applies to someone driving a motor vehicle, not pedestrians, passengers or bicycles
    is_motor_vehicle = a_raw["vehicle_type"].str.split("-").str[0].isin(MOTOR_VEHICLE_KINDS)
    drug_events = a_raw[(a_raw["is_drug_related"] == 1) & is_motor_vehicle].sample(n=len(linked), random_state=SEED)
    other_events = a_raw[a_raw["is_drug_related"] == 0].sample(n=n_a_only, random_state=SEED)
    a_df = pd.concat([drug_events, other_events], ignore_index=True)

    a_only_ids = range(len(handoff) + 1, len(handoff) + 1 + n_a_only)
    a_df.insert(0, "master_id", list(linked["master_id"]) + list(a_only_ids))
    a_df.insert(1, "hashed_id", [to_shared_hashed_id(m) for m in a_df["master_id"]])

    a_only_gender = random.choices(["男", "女"], weights=[0.829, 0.171], k=n_a_only)
    a_only_age = [sample_age_for_gender(g) for g in a_only_gender]
    a_df["suspect_age_group"] = list(linked["age_group"]) + a_only_age
    a_df["suspect_gender"] = list(linked["gender"]) + a_only_gender

    return a_df[[
        "master_id",
        "hashed_id",
        "event_date",
        "event_hour",
        "county",
        "district",
        "lat_raw",
        "lon_raw",
        "vehicle_type",
        "is_drug_related",
        "suspect_age_group",
        "suspect_gender",
        "casualty_count",
        "prior_offense_flag",
    ]].copy()


def main():
    random.seed(SEED)
    np.random.seed(SEED)

    b_raw = make_b_consistent(pd.read_csv(BASE_DIR / "dataset_b_final.csv"))
    handoff = build_handoff_master_population(b_raw)
    b_aligned = make_b_aligned(b_raw)
    a_aligned = build_a_aligned(handoff, load_a_source())

    handoff.to_csv(BASE_DIR / "handoff_master_population.csv", index=False)
    b_aligned.to_csv(BASE_DIR / "dataset_b_aligned.csv", index=False)
    a_aligned.to_csv(BASE_DIR / "PETsARD_Dataset_A_aligned.csv", index=False)

    print("Generated aligned files:")
    print(f"- handoff_master_population.csv: {len(handoff)} rows")
    print(f"- dataset_b_aligned.csv: {len(b_aligned)} rows")
    print(f"- PETsARD_Dataset_A_aligned.csv: {len(a_aligned)} rows")
    print("- hash formula: SHA256(salt:id_6digit)[:16]")
    in_both = a_aligned.merge(handoff, on="hashed_id")
    print(f"- A rows also in B: {len(in_both)} (all B positive: {bool((in_both['test_result'] == '陽性').all())})")


if __name__ == "__main__":
    main()
