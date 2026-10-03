"""Step 5: finalize the synthetic wide table produced by PETsARD (no split into A'/B'/C').

- rule violations produced by the synthesizer are repaired and counted in logs/synthetic_repairs.json
- fields dropped from the synthesis input because other fields fully determine them are re-derived
  (drug_class, severity_score, sanction_type, sanction_years, lifetime_ban, sanction_basis,
  restore_method, prior_offense_flag, death_flag)
- sanction rules = 道路交通管理處罰條例 §35 for 2025 violations (same as datagen/generate_abc_v3.py):
  A1 (death) -> 吊銷終身; recidivism >= 1 -> 吊銷3年; otherwise (accident with injuries) -> 吊扣2-4年
- output: synthetic_output/synthetic_wide.csv, one row per synthetic person
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent
OUT = BASE / "synthetic_output"
SUBSTANCE_CLASS = {"嗎啡": "一級", "安非他命": "二級", "甲基安非他命": "二級", "MDMA": "二級", "愷他命": "三級"}
CLASS_SEVERITY = {"一級": 3.0, "二級": 2.0, "三級": 1.0}
SEED = 20261002


def load_synthetic() -> pd.DataFrame:
    # PETsARD runs 10 splits for stable risk estimates; split 1 is the released dataset
    # (fixed in advance, not picked by score, to avoid cherry-picking a lucky run)
    files = list((BASE / "petsard_output").glob("*holdout_[[]10-01[]]*Postprocessor*.csv"))
    assert len(files) == 1, files
    return pd.read_csv(files[0])


def repair(s: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    s = s.copy()
    rng = np.random.default_rng(SEED)
    death = s["accident_class"] == "A1"
    repeat = s["recidivism_count"] >= 1
    first = ~death & ~repeat
    want_lifetime = death & (s["sanction"] != "吊銷終身")
    want_revoke = ~death & repeat & (s["sanction"] != "吊銷3年")
    want_suspend = first & ~s["sanction"].isin(["吊扣2年", "吊扣3年", "吊扣4年"])
    s.loc[want_lifetime, "sanction"] = "吊銷終身"
    s.loc[want_revoke, "sanction"] = "吊銷3年"
    s.loc[want_suspend, "sanction"] = rng.choice(["吊扣2年", "吊扣3年", "吊扣4年"], size=int(want_suspend.sum()))
    repairs = {
        "A1 (death) but not 吊銷終身 -> 吊銷終身": int(want_lifetime.sum()),
        "repeat offence but not 吊銷3年 -> 吊銷3年": int(want_revoke.sum()),
        "first offence with injuries but not 吊扣2-4年 -> 吊扣2-4年": int(want_suspend.sum()),
    }
    return s, repairs


def finalize(s: pd.DataFrame) -> pd.DataFrame:
    s = s.reset_index(drop=True)
    death = s["accident_class"] == "A1"
    repeat = s["recidivism_count"] >= 1
    revoked = s["sanction"].str.startswith("吊銷")
    drug_class = s["positive_substance"].map(SUBSTANCE_CLASS)
    years = s["sanction"].str.extract(r"(\d)年")[0].astype("Int64")
    return pd.DataFrame({
        "synth_id": [f"S{i:05d}" for i in range(1, len(s) + 1)],
        # from A (police; casualties from the official accident data)
        "event_month": s["event_month"], "weekday": s["weekday"], "event_hour": s["event_hour"], "county": s["county"],
        "vehicle_group": s["vehicle_group"], "age_group": s["age_group"], "gender": s["gender"],
        "accident_class": s["accident_class"], "death_flag": death.astype(int), "injury_count": s["injury_count"],
        "prior_offense_flag": repeat.astype(int),
        # from B (lab)
        "test_offset_days_from_event": s["test_offset_days"], "specimen_type": s["specimen_type"],
        "positive_substance": s["positive_substance"], "drug_class": drug_class,
        "severity_score": drug_class.map(CLASS_SEVERITY),
        # from C (motor vehicle office)
        "recidivism_count": s["recidivism_count"], "sanction_lag_days_from_event": s["sanction_lag"],
        "sanction_basis": np.select([death, repeat], ["§35 致人死亡：吊銷並終身不得考領", "§35 III 十年內再犯：吊銷"],
                                    "§35 I 肇事致人受傷：吊扣2-4年"),
        "sanction_type": np.where(revoked, "吊銷", "吊扣"), "sanction_years": years,
        "lifetime_ban": np.where(s["sanction"] == "吊銷終身", "是", "否"),
        "restore_method": np.select([s["sanction"] == "吊銷終身", revoked], ["終身不得考領", "重新考領"], "期滿發還"),
    })


def main():
    syn, repairs = repair(load_synthetic())
    wide = finalize(syn)
    OUT.mkdir(exist_ok=True)
    wide.to_csv(OUT / "synthetic_wide.csv", index=False, encoding="utf-8-sig")
    print(f"synthetic_wide.csv: {wide.shape}; repairs: {repairs}")
    (BASE / "logs").mkdir(exist_ok=True)
    (BASE / "logs" / "synthetic_repairs.json").write_text(json.dumps({"rows": len(syn), "repairs": repairs}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
