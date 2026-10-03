"""Consistency checks for the v3 A/B/C datasets."""
from pathlib import Path

import pandas as pd

IN = Path(__file__).resolve().parent.parent / "input"
a = pd.read_csv(IN / "PETsARD_Dataset_A_aligned.csv", low_memory=False)
b = pd.read_csv(IN / "dataset_b_aligned.csv")
c = pd.read_csv(IN / "dataset_c_aligned.csv", encoding="utf-8-sig")

pos = b[b.test_result == "陽性"]
ab = a.merge(b, on="master_id", suffixes=("_a", "_b"))
assert a.master_id.is_unique and b.master_id.is_unique and c.master_id.is_unique
assert len(ab) == round(len(pos) * 0.5) and (ab.test_result == "陽性").all()
assert (ab.suspect_age_group == ab.age_group).all() and (ab.suspect_gender == ab.gender).all()
assert (a.is_drug_related == a.master_id.isin(ab.master_id).astype(int)).all()
lag = (pd.to_datetime(ab.test_date) - pd.to_datetime(ab.event_date)).dt.days
assert lag.between(0, 3).all()

# formats (review fixes)
B_AGES = {"19歲以下", "20-29歲", "30-39歲", "40-49歲", "50-59歲", "60歲以上"}
assert set(a.suspect_age_group) <= B_AGES | {"未知"} and set(b.age_group) <= B_AGES
assert a.event_date.str.fullmatch(r"\d{4}-\d{2}-\d{2}").all()
for g, shares in {"男": [3.0, 8.8, 16.4, 34.4, 26.8, 10.7], "女": [2.1, 9.2, 17.7, 41.3, 22.5, 7.2]}.items():
    got = b[b.gender == g].age_group.value_counts(normalize=True).reindex(sorted(B_AGES, key=["19歲以下", "20-29歲", "30-39歲", "40-49歲", "50-59歲", "60歲以上"].index)) * 100
    assert (got.round(1) - pd.Series(shares, index=got.index)).abs().max() <= 0.1, (g, got.round(1).tolist())

# A casualties (official data)
assert (a.casualty_count == a.death_count + a.injury_count).all()
assert ((a.accident_class == "A1") == (a.death_count > 0)).all(), "A1 must mean at least one death"

# C population and detection source
expected_c = set(pos.master_id) | set(a.loc[a.prior_offense_flag == 1, "master_id"])
assert set(c.master_id) == expected_c
assert ((c.detection_source == "事故") == c.master_id.isin(a.master_id)).all()
cb = c.merge(b[["master_id", "age_group", "gender"]], on="master_id", suffixes=("", "_b"))
assert (cb.age_group == cb.age_group_b).all() and (cb.gender == cb.gender_b).all()

# C sanctions follow §35 (2025 version)
ca = c.merge(a[["master_id", "event_date", "death_count", "prior_offense_flag"]], on="master_id", how="left")
death = ca.death_count.fillna(0) > 0
repeat = ca.recidivism_count >= 1
accident = ca.detection_source == "事故"
assert ((ca.lifetime_ban == "是") == death).all() and (ca.loc[death, "restore_method"] == "終身不得考領").all()
assert ((ca.sanction_type == "吊銷") == (death | repeat)).all()
assert (ca.loc[repeat & ~death, "sanction_years"] == 3).all()
assert ca.loc[~death & ~repeat & accident, "sanction_years"].between(2, 4).all()
assert ca.loc[~death & ~repeat & ~accident, "sanction_years"].between(1, 2).all()
assert ((ca.loc[accident, "prior_offense_flag"] == 1) == (ca.loc[accident, "recidivism_count"] >= 1)).all()
assert (pd.to_datetime(ca.loc[accident, "violation_date"]) == pd.to_datetime(ca.loc[accident, "event_date"])).all()

print("v3 validation OK")
print(f"A={len(a):,} (A1 {int((a.accident_class == 'A1').sum()):,})  B={len(b):,} (positive {len(pos):,})  "
      f"C={len(c):,} (事故 {int(accident.sum()):,} / 路檢攔查 {int((~accident).sum()):,})  A∩B∩C={int(accident.sum()):,}")
print(c.groupby(["sanction_basis", "sanction_years"], dropna=False).size().to_string())
