"""v3: Dataset A gets real death / injury counts; Dataset C sanctions follow 道路交通管理處罰條例 §35.

Changes from v2
  A  NEW columns accident_class (A1/A2), death_count, injury_count, taken from the official
     114年傷亡道路交通事故資料 (police open data) by matching date + hour + coordinates.
     casualty_count is kept and set to death_count + injury_count (differed for 10 rows).
  C  NEW column detection_source: 事故 (person is in A) / 路檢攔查 (B positive, no accident record).
     Drug driving caught at a roadside stop is sanctioned under §35 too, so these people stay in C.
     NEW column sanction_basis: the legal reason for the sanction.
     Sanctions now follow §35 as in force for 2025 violations (REPEAT_RATE = 0.60               # share of drug drivers with a drug-driving offence in the past 10 years (ASSUMPTION, set by the team)
LAW = "114"), not the spec sheet:
        accident with death                  -> 吊銷, 終身不得考領        (§35: 致人重傷或死亡)
        10-year repeat offence (recidivism≥1) -> 吊銷, 3 years before re-applying (§35 III; years: VERIFY §67)
        first offence, accident with injuries -> 吊扣 2-4 years           (§35 I: 因而肇事致人受傷)
        first offence, roadside stop          -> 吊扣 1-2 years           (§35 I)
     重傷 (serious injury) cannot be told apart from ordinary injury in the official data, so only
     deaths trigger the lifetime ban (documented limitation).
     Prior offences (10-year drug-driving repeat, §35 III): REPEAT_RATE = 60% for everyone
     (ASSUMPTION set by the team; the raw A prior flag gave ~89%). v2 gave roadside-stop people none.
v3.1 fixes (review): B ages re-calibrated per gender to the official table; A's non-drug rows use
B's 6 age groups too (the age format used to reveal drug drivers); A event_date is YYYY-MM-DD.
Unchanged from v2: B as given (50,000), A = all 403,088 raw accidents, A∩B = 50% of B positives
with test_date 0-3 days after the event, license_class rule, sanction lag from v1 C.
"""
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
SRC = HERE / "source"
OUT = HERE.parent / "input"
SALT = "innoserve2026-drugdriving"
SEED = 20261003
LINK_RATE = 0.50
TEST_AFTER_EVENT_DAYS = (0, 3)
MOTOR = ["機車", "小客車(含客、貨兩用)", "小貨車", "大貨車", "大客車", "曳引車", "半聯結車", "全聯結車"]
EXTRA_RECID = ([0, 1, 2, 3], [318 / 503, 121 / 503, 41 / 503, 23 / 503])
REPEAT_RATE = 0.60               # share of drug drivers with a drug-driving offence in the past 10 years (ASSUMPTION, set by the team)
LAW = "114"                       # "114": §35 for 2025 violations; see README for the 2026 amendment
REVOKE_WAIT_YEARS = 3             # years before re-applying after 吊銷 for a repeat offence (VERIFY §67)


AGE_ORDER = ["19歲以下", "20-29歲", "30-39歲", "40-49歲", "50-59歲", "60歲以上"]
AGE_TARGET = {  # FDA 113年報 圖一, by gender (same table as the spec)
    "男": [0.030, 0.088, 0.164, 0.344, 0.268, 0.107],
    "女": [0.021, 0.092, 0.177, 0.413, 0.225, 0.072],
}
A_AGE_TO_B = {"0-9": "19歲以下", "10-19": "19歲以下", "20-29": "20-29歲", "30-39": "30-39歲", "40-49": "40-49歲",
              "50-59": "50-59歲", "60-69": "60歲以上", "70-79": "60歲以上", "80+": "60歲以上", "未知": "未知"}


def calibrate_b_ages(b: pd.DataFrame, rng) -> pd.DataFrame:
    """The given B gives women almost the men's age mix (40-49: 37.2% vs official 41.3%).
    Re-assign as few people as possible so each gender matches the official table.
    Positivity does not depend on age in B (22-24% in every group), so test results are untouched."""
    b = b.copy()
    moved = 0
    for g, shares in AGE_TARGET.items():
        idx = b.index[b.gender == g]
        n = len(idx)
        target = pd.Series(np.round(np.array(shares) / sum(shares) * n).astype(int), index=AGE_ORDER)
        target.iloc[target.values.argmax()] += n - target.sum()
        current = b.loc[idx, "age_group"].value_counts().reindex(AGE_ORDER, fill_value=0)
        surplus = []
        for age in AGE_ORDER:
            extra = current[age] - target[age]
            if extra > 0:
                surplus += list(rng.choice(b.index[(b.gender == g) & (b.age_group == age)], size=extra, replace=False))
        rng.shuffle(surplus)
        pos = 0
        for age in AGE_ORDER:
            need = target[age] - current[age]
            if need > 0:
                b.loc[surplus[pos:pos + need], "age_group"] = age
                pos += need
        moved += len(surplus)
    print(f"B age calibration: {moved:,} of {len(b):,} people re-assigned to match the official age table")
    return b


def hashed(m: int) -> str:
    return hashlib.sha256(f"{SALT}:{m:06d}".encode()).hexdigest()[:16]


def license_class(vehicle: str) -> str:
    kind, _, use = vehicle.partition("-")
    if kind == "機車":
        return "大型重型機車" if "大型重型" in use else "普通輕型機車" if "輕型" in use else "普通重型機車"
    if kind == "小客車(含客、貨兩用)":
        return "職業小型車" if use in ("計程車", "營業用") else "普通小型車"
    if kind == "小貨車":
        return "職業小型車" if use == "營業用" else "普通小型車"
    if kind == "大貨車":
        return "職業大貨車" if use == "營業用" else "普通大貨車"
    if kind == "大客車":
        return "職業大客車"
    return "職業聯結車"


def add_official_casualties(raw: pd.DataFrame) -> pd.DataFrame:
    """Attach accident_class / death_count / injury_count from the official open data."""
    off = pd.read_parquet(SRC / "official_114_accidents.parquet")
    key = ["date", "hour", "lat", "lon"]
    r = raw.copy()
    r["_row"] = np.arange(len(r))
    r["date"] = pd.to_datetime(r.event_date, format="%Y/%m/%d")
    r["hour"], r["lat"], r["lon"] = r.event_hour, r.lat_raw.round(6), r.lon_raw.round(6)
    m = r[["_row", "casualty_count"] + key].merge(off, on=key, how="left")
    # a few keys match several official accidents: prefer the one whose totals agree with casualty_count
    m["_agree"] = (m.death_count + m.injury_count) == m.casualty_count
    m = m.sort_values(["_row", "_agree"], ascending=[True, False]).drop_duplicates("_row")
    assert m.accident_class.notna().all(), "some raw A rows have no official match"
    r = r.merge(m[["_row", "accident_class", "death_count", "injury_count"]], on="_row").sort_values("_row")
    r.index = raw.index
    official_total = r.death_count + r.injury_count
    changed = int((official_total != r.casualty_count).sum())
    r["casualty_count"] = official_total  # official figures win where the simulated count differs
    print(f"official match: {len(r):,} rows, casualty_count corrected for {changed}, A1 {int((r.accident_class == 'A1').sum()):,}")
    return r.drop(columns=["_row", "date", "hour", "lat", "lon"])


def build_a(b: pd.DataFrame, raw: pd.DataFrame, rng) -> pd.DataFrame:
    positives = b[b.test_result == "陽性"]
    linked = positives.sample(frac=LINK_RATE, random_state=SEED).sort_values("master_id")

    pool = raw[(raw.is_drug_related == 1) & raw.vehicle_type.str.split("-").str[0].isin(MOTOR) & raw.district.notna()].copy()
    pool["date"] = pd.to_datetime(pool.event_date, format="%Y/%m/%d")
    by_date = {d: list(rng.permutation(idx)) for d, idx in pool.groupby("date").groups.items()}

    chosen = []
    for test in pd.to_datetime(linked.test_date):
        lags = list(range(TEST_AFTER_EVENT_DAYS[0], TEST_AFTER_EVENT_DAYS[1] + 1))
        rng.shuffle(lags)
        pick = next((by_date[d].pop() for d in (test - pd.Timedelta(days=l) for l in lags) if by_date.get(d)), None)
        assert pick is not None
        chosen.append(pick)

    drug = raw.loc[chosen].reset_index(drop=True)
    drug["suspect_age_group"] = linked.age_group.values
    drug["suspect_gender"] = linked.gender.values
    # the raw prior flag marked ~89% of drug events; replaced by the team's assumed 10-year repeat rate
    drug["prior_offense_flag"] = (rng.random(len(drug)) < REPEAT_RATE).astype(int)
    drug.insert(0, "master_id", linked.master_id.values)

    other = raw.drop(index=chosen).reset_index(drop=True)
    other["is_drug_related"] = 0
    other["prior_offense_flag"] = 0
    other.insert(0, "master_id", range(len(b) + 1, len(b) + 1 + len(other)))
    # same 6 age groups as B for every row, so the age format no longer reveals who is a drug driver
    other["suspect_age_group"] = other.suspect_age_group.map(A_AGE_TO_B)
    assert other.suspect_age_group.notna().all()
    a = pd.concat([drug, other], ignore_index=True)
    a["event_date"] = pd.to_datetime(a.event_date, format="%Y/%m/%d").dt.strftime("%Y-%m-%d")  # same format as B and C
    a.insert(1, "hashed_id", a.master_id.map(hashed))
    cols = ["master_id", "hashed_id", "event_date", "event_hour", "county", "district", "lat_raw", "lon_raw",
            "vehicle_type", "is_drug_related", "suspect_age_group", "suspect_gender",
            "accident_class", "death_count", "injury_count", "casualty_count", "prior_offense_flag"]
    print(f"A: {len(a):,} rows, linked {len(drug):,}, linked A1 (death) {int((drug.accident_class == 'A1').sum())}")
    return a[cols]


def sanction(c: pd.DataFrame, rng) -> pd.DataFrame:
    death = c.death_count.fillna(0) > 0
    repeat = c.recidivism_count >= 1
    accident = c.detection_source == "事故"
    basis = np.select([death, repeat, accident],
                      ["§35 致人死亡：吊銷並終身不得考領", "§35 III 十年內再犯：吊銷", "§35 I 肇事致人受傷：吊扣2-4年"],
                      "§35 I 初犯：吊扣1-2年")
    revoked = death | repeat
    years = np.select([death, repeat, accident],
                      [np.nan, REVOKE_WAIT_YEARS, rng.integers(2, 5, len(c))], rng.integers(1, 3, len(c)))
    c["sanction_basis"] = basis
    c["sanction_type"] = np.where(revoked, "吊銷", "吊扣")
    c["lifetime_ban"] = np.where(death, "是", "否")
    c["sanction_years"] = pd.Series(years, index=c.index).astype("Int64")
    c["restore_method"] = np.select([death, revoked], ["終身不得考領", "重新考領"], "期滿發還")
    return c


def build_c(a: pd.DataFrame, b: pd.DataFrame, old_c: pd.DataFrame, rng) -> pd.DataFrame:
    ids = set(b.loc[b.test_result == "陽性", "master_id"]) | set(a.loc[a.prior_offense_flag == 1, "master_id"])
    c = b[b.master_id.isin(ids)][["master_id", "hashed_id", "gender", "age_group", "test_date"]].copy()
    c = c.merge(a[["master_id", "event_date", "vehicle_type", "prior_offense_flag", "death_count"]], on="master_id", how="left")
    in_a = c.event_date.notna()
    c["detection_source"] = np.where(in_a, "事故", "路檢攔查")
    c["violation_date"] = np.where(in_a, c.event_date, c.test_date)

    prior_rate = c.loc[in_a, "prior_offense_flag"].mean()
    prior = np.where(in_a, c.prior_offense_flag.fillna(0), rng.random(len(c)) < prior_rate).astype(int)
    extra = rng.choice(EXTRA_RECID[0], size=len(c), p=EXTRA_RECID[1])
    c["recidivism_count"] = np.where(prior == 1, 1 + extra, 0)

    c["license_class"] = c.vehicle_type.map(license_class, na_action="ignore")
    c.loc[~in_a, "license_class"] = rng.choice(c.loc[in_a, "license_class"].values, size=(~in_a).sum())

    old_lag = (pd.to_datetime(old_c.sanction_date) - pd.to_datetime(old_c.violation_date)).dt.days.values
    c["sanction_date"] = pd.to_datetime(c.violation_date) + pd.to_timedelta(rng.choice(old_lag, size=len(c)), unit="D")
    c = sanction(c, rng)
    restore = [s + pd.DateOffset(years=int(y)) if pd.notna(y) else pd.NaT for s, y in zip(c.sanction_date, c.sanction_years)]
    c["restore_date"] = [d.strftime("%Y-%m-%d") if pd.notna(d) else "永久" for d in restore]
    c["sanction_date"] = c.sanction_date.dt.strftime("%Y-%m-%d")

    cols = ["master_id", "hashed_id", "gender", "age_group", "license_class", "detection_source", "violation_date",
            "sanction_date", "recidivism_count", "sanction_basis", "sanction_type", "sanction_years", "lifetime_ban",
            "restore_method", "restore_date"]
    print(f"C: {len(c):,} rows ({int(in_a.sum()):,} 事故, {int((~in_a).sum()):,} 路檢攔查), prior rate used {prior_rate:.1%}")
    print(c.sanction_basis.value_counts().to_string())
    return c[cols].sort_values("master_id")


def main():
    rng = np.random.default_rng(SEED)
    b = calibrate_b_ages(pd.read_csv(SRC / "dataset_b_aligned.csv", encoding="utf-8-sig"), rng)
    raw = add_official_casualties(pd.read_csv(SRC / "PETsARD_Dataset_A.csv", encoding="utf-8-sig", low_memory=False))
    old_c = pd.read_csv(SRC / "dataset_c_aligned_v1.csv", encoding="utf-8-sig")
    a = build_a(b, raw, rng)
    c = build_c(a, b, old_c, rng)
    OUT.mkdir(exist_ok=True)
    a.to_csv(OUT / "PETsARD_Dataset_A_aligned.csv", index=False)
    b.to_csv(OUT / "dataset_b_aligned.csv", index=False)
    c.to_csv(OUT / "dataset_c_aligned.csv", index=False)


if __name__ == "__main__":
    main()
