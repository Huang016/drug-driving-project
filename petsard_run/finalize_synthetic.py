"""Turn a PETsARD synthetic table back into the layout of Dataset A.

  location -> county, district
  lat_raw / lon_raw are drawn inside the district's usual extent (10th-90th
  percentile box of the real events there). They are new random points, not the
  coordinates of any real event.

Usage: python finalize_synthetic.py <synthetic csv from PETsARD> <output csv>
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from prepare_petsard_input import LOCATION_SEP, SEED, SOURCE


def district_boxes() -> pd.DataFrame:
    a = pd.read_csv(SOURCE, low_memory=False).dropna(subset=["district"])
    grouped = a.groupby(["county", "district"])[["lat_raw", "lon_raw"]]
    low, high = grouped.quantile(0.10), grouped.quantile(0.90)
    return low.join(high, lsuffix="_low", rsuffix="_high")


def main():
    synthetic_path, output_path = Path(sys.argv[1]), Path(sys.argv[2])
    syn = pd.read_csv(synthetic_path)
    rng = np.random.default_rng(SEED)

    syn[["county", "district"]] = syn["location"].str.split(LOCATION_SEP, n=1, expand=True, regex=False)
    box = syn.join(district_boxes(), on=["county", "district"])
    syn["lat_raw"] = rng.uniform(box["lat_raw_low"], box["lat_raw_high"]).round(6)
    syn["lon_raw"] = rng.uniform(box["lon_raw_low"], box["lon_raw_high"]).round(6)

    columns = [
        "event_month", "event_weekday", "event_hour", "county", "district", "lat_raw", "lon_raw",
        "vehicle_kind", "suspect_age_group", "suspect_gender", "casualty_count", "is_drug_related",
    ]
    syn[columns].to_csv(output_path, index=False, encoding="utf-8-sig")
    print(f"{output_path.name}: {len(syn)} rows")


if __name__ == "__main__":
    main()
