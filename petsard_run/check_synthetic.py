"""Compare a PETsARD synthetic table with the generalized original on the things
the project actually uses: drug-related rate by county and by time band.

Usage: python check_synthetic.py <generalized original csv> <synthetic csv from PETsARD>
"""
import sys

import pandas as pd

from prepare_petsard_input import LOCATION_SEP

NIGHT_HOURS = [22, 23, 0, 1, 2, 3, 4, 5]
EVENING_HOURS = [18, 19, 20, 21]


def add_groups(d: pd.DataFrame) -> pd.DataFrame:
    d = d.copy()
    d["county"] = d["location"].str.split(LOCATION_SEP, regex=False).str[0]
    d["time_band"] = "白天"
    d.loc[d["event_hour"].isin(EVENING_HOURS), "time_band"] = "傍晚"
    d.loc[d["event_hour"].isin(NIGHT_HOURS), "time_band"] = "深夜"
    return d


def main():
    ori, syn = add_groups(pd.read_csv(sys.argv[1])), add_groups(pd.read_csv(sys.argv[2]))
    print(f"rows: ori {len(ori)}, syn {len(syn)}")
    print(f"drug-related rate: ori {ori['is_drug_related'].mean():.2%}, syn {syn['is_drug_related'].mean():.2%}")
    print(f"locations in syn that exist in ori: {syn['location'].isin(set(ori['location'])).mean():.1%}")

    for keys in (["time_band"], ["county"], ["county", "time_band"], ["suspect_gender"], ["suspect_age_group"], ["vehicle_kind"]):
        rate = pd.DataFrame({
            "ori": ori.groupby(keys)["is_drug_related"].mean(),
            "syn": syn.groupby(keys)["is_drug_related"].mean(),
            "n_ori": ori.groupby(keys).size(),
        }).dropna()
        rate = rate[rate["n_ori"] >= 200]
        corr = rate["ori"].corr(rate["syn"])
        gap = (rate["ori"] - rate["syn"]).abs().mean()
        print(f"\ndrug rate by {'+'.join(keys)}: {len(rate)} groups, correlation {corr:.3f}, mean abs gap {gap:.2%}")
        if len(rate) <= 25:
            print((rate[["ori", "syn"]] * 100).round(1).sort_values("ori", ascending=False).to_string())


if __name__ == "__main__":
    main()
