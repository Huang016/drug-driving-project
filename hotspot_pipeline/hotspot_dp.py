"""Stage 4 + 5: district-level hotspot statistics, with differential privacy
applied before anything is published.

Usage: python hotspot_dp.py <synthetic release csv> [epsilon]

Per district the release contains a noisy count and a noisy mean severity
(and a noisy recidivist share once C's column is in the table). The noise is
added to the real analysis table, inside the coordinator's controlled
environment; the synthetic table is only compared against it.

Privacy accounting
  One person appears in exactly one district, so the districts are disjoint and
  the whole release costs the per-district budget once (parallel composition).
  Each published statistic gets an equal share of epsilon:
    count              Laplace noise, sensitivity 1
    sum of severity    Laplace noise, sensitivity MAX_SEVERITY
    recidivist count   Laplace noise, sensitivity 1
  The district list is the public administrative list, including districts with
  nobody in the table; leaving empty districts out would itself reveal who is absent.
  Districts whose noisy count is below MIN_PUBLISHED_COUNT are not shown.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
DISTRICT_SOURCE = BASE_DIR.parent / "PETsARD_Dataset_A.csv"
LOCATION_SEP = "|"
SEED = 20261002
DEFAULT_EPSILON = 1.0
MAX_SEVERITY = 3
MIN_PUBLISHED_COUNT = 5


def public_districts() -> pd.DataFrame:
    a = pd.read_csv(DISTRICT_SOURCE, usecols=["county", "district", "lat_raw", "lon_raw"]).dropna(subset=["district"])
    centre = a.groupby(["county", "district"])[["lat_raw", "lon_raw"]].median().round(4)
    return centre.rename(columns={"lat_raw": "lat", "lon_raw": "lon"})


def district_stats(table: pd.DataFrame, districts: pd.DataFrame) -> pd.DataFrame:
    table = table.copy()
    if "location" in table.columns:
        table[["county", "district"]] = table["location"].str.split(LOCATION_SEP, n=1, expand=True, regex=False)
    grouped = table.groupby(["county", "district"])
    stats = pd.DataFrame({"n": grouped.size(), "severity_sum": grouped["severity_score"].sum()})
    if "recidivism_count" in table.columns:
        stats["recidivist_n"] = grouped["recidivism_count"].apply(lambda s: (s > 0).sum())
    return stats.reindex(districts.index, fill_value=0)


def add_noise(stats: pd.DataFrame, epsilon: float, rng: np.random.Generator) -> pd.DataFrame:
    sensitivity = {"n": 1, "severity_sum": MAX_SEVERITY, "recidivist_n": 1}
    share = epsilon / len(stats.columns)
    noisy = pd.DataFrame(index=stats.index)
    for col in stats.columns:
        noisy[col] = stats[col] + rng.laplace(0, sensitivity[col] / share, len(stats))
    return noisy


def published(noisy: pd.DataFrame) -> pd.DataFrame:
    # everything below is post-processing of the noisy values and costs no extra budget
    out = pd.DataFrame(index=noisy.index)
    out["n_published"] = noisy["n"].round().clip(lower=0).astype(int)
    shown = out["n_published"] >= MIN_PUBLISHED_COUNT
    out["severity_mean_published"] = (noisy["severity_sum"] / noisy["n"]).clip(1, MAX_SEVERITY).round(2).where(shown)
    if "recidivist_n" in noisy.columns:
        out["recidivist_share_published"] = (noisy["recidivist_n"] / noisy["n"]).clip(0, 1).round(2).where(shown)
    out["n_published"] = out["n_published"].where(shown)
    return out


def main():
    synthetic_path = Path(sys.argv[1])
    epsilon = float(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_EPSILON
    districts = public_districts()

    real = district_stats(pd.read_csv(BASE_DIR / "analysis_table.csv"), districts)
    synthetic = district_stats(pd.read_csv(synthetic_path), districts)
    # the noise goes on the real table: the synthesizer is not differentially private, so one
    # person's effect on a synthetic count is not bounded by the sensitivities above
    release = published(add_noise(real, epsilon, np.random.default_rng(SEED)))

    result = districts.join(real["n"].rename("n_real")).join(synthetic["n"].rename("n_synthetic")).join(release)
    output = synthetic_path.parent / f"hotspot_eps{epsilon:g}.csv"
    result.reset_index().to_csv(output, index=False, encoding="utf-8-sig")

    shown = result["n_published"].notna()
    top_real = set(result["n_real"].nlargest(10).index)
    top_published = set(result["n_published"].fillna(0).nlargest(10).index)
    print(f"{output.name}: epsilon {epsilon:g}, {len(result)} districts, {shown.sum()} shown (noisy count >= {MIN_PUBLISHED_COUNT})")
    print(f"  real vs synthetic count correlation:  {result['n_real'].corr(result['n_synthetic']):.3f}")
    print(f"  real vs published count correlation:  {result['n_real'].corr(result['n_published'].fillna(0)):.3f}")
    print(f"  top-10 districts kept after DP: {len(top_real & top_published)} of 10")
    print(f"  districts shown that have nobody in the real table: {(shown & (result['n_real'] == 0)).sum()}")


if __name__ == "__main__":
    main()
