"""Stage 6: the data files behind the Google Maps site in web/.

Usage: python build_web_data.py [epsilon_district] [epsilon_grid]

Writes web/data/regions.json, web/data/cells.json and web/data/towns.topo.json.
Nothing written there contains a person-level row or an exact coordinate.

District level
  Reuses hotspot_dp.py unchanged (same seed, same epsilon), so the published
  district counts are identical to results/hotspot_eps<epsilon>.csv. County
  figures are sums of the noisy district counts: post-processing, no extra budget.
  The "ratio" is drug-driving accidents per 10,000 casualty accidents. The
  denominator is the public 114-year accident count per district, so it needs no noise.

500 m grid (for the street-view level)
  Spatial generalisation from the PDF: an exact accident location becomes the
  500 m x 500 m cell it falls in. The candidate cells are every cell with at
  least one accident in the public accident data (A) - not only cells with a
  drug-driving accident, which would itself reveal where those happened. Each
  candidate gets Laplace noise (sensitivity 1) and only cells whose noisy count
  reaches GRID_MIN_COUNT are published.
  One person is counted once in the district release and once in the grid, so
  the two compose sequentially: total epsilon = epsilon_district + epsilon_grid.

The grid needs each person's cell. In the PSI flow A only sends county and
district (psi_pqc/institution.py); until A also sends the cell id, the grid is
built from data/wide/wide_table_full.csv, the coordinator's merged table.
"""
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from hotspot_dp import DEFAULT_EPSILON, MIN_PUBLISHED_COUNT, SEED, add_noise, district_stats, public_districts

BASE_DIR = Path(__file__).resolve().parent
ROOT = BASE_DIR.parent
WEB_DATA = ROOT / "web" / "data"
ACCIDENTS = ROOT / "data" / "raw" / "Dataset_A_.csv"
WIDE_TABLE = ROOT / "data" / "wide" / "wide_table_full.csv"
ATLAS = WEB_DATA / "towns-10t.json"  # taiwan-atlas (npm), 10% simplified town boundaries

GRID_SEED = 20261003
DEFAULT_GRID_EPSILON = 2.0
GRID_MIN_COUNT = 3
CELL_M = 500
CELL_LAT = CELL_M / 111_320
CELL_LON = CELL_M / (111_320 * math.cos(math.radians(23.7)))  # one width for all of Taiwan

# Dataset A cuts these four names at the first 鎮/市 (e.g. 平鎮區 -> 平鎮)
DISTRICT_FIX = {("桃園市", "平鎮"): "平鎮區", ("臺南市", "左鎮"): "左鎮區",
                ("臺南市", "新市"): "新市區", ("高雄市", "前鎮"): "前鎮區"}


def norm(name: str) -> str:
    return name.replace("台", "臺")


def fix_districts(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["county"] = df["county"].map(norm)
    df["district"] = [DISTRICT_FIX.get((c, d), norm(d)) if isinstance(d, str) else d
                      for c, d in zip(df["county"], df["district"])]
    return df


def cell_index(lat: pd.Series, lon: pd.Series) -> pd.DataFrame:
    return pd.DataFrame({"ci": np.floor(lat / CELL_LAT).astype("Int64"), "cj": np.floor(lon / CELL_LON).astype("Int64")})


def district_release(epsilon: float):
    districts = public_districts()
    analysis = BASE_DIR / "analysis_table.csv"
    source = analysis if analysis.exists() else WIDE_TABLE  # same county/district/severity/recidivism per person
    table = pd.read_csv(source)
    real = district_stats(table, districts)
    noisy = add_noise(real, epsilon, np.random.default_rng(SEED))

    out = districts.join(noisy).reset_index()
    out = fix_districts(out)
    out["n_noisy"] = out["n"].clip(lower=0)
    out["shown"] = out["n_noisy"].round() >= MIN_PUBLISHED_COUNT
    out["severity"] = (out["severity_sum"] / out["n"]).clip(1, 3).round(2)
    if "recidivist_n" in out.columns:
        out["recidivist_share"] = (out["recidivist_n"] / out["n"]).clip(0, 1).round(2)
    return out, real, source.name


def grid_release(accidents: pd.DataFrame, epsilon: float):
    acc = accidents.dropna(subset=["lat_raw", "lon_raw", "district"])
    acc = acc.join(cell_index(acc["lat_raw"], acc["lon_raw"]))
    # each cell belongs to the district where most of its (public) accidents happened
    owner = (acc.groupby(["ci", "cj", "county", "district"]).size().rename("k").reset_index()
             .sort_values("k", ascending=False).drop_duplicates(["ci", "cj"]).set_index(["ci", "cj"]))

    wide = pd.read_csv(WIDE_TABLE, usecols=["lat_raw", "lon_raw"]).dropna()
    real = wide.join(cell_index(wide["lat_raw"], wide["lon_raw"])).groupby(["ci", "cj"]).size()
    real = real.reindex(owner.index, fill_value=0)

    noisy = real + np.random.default_rng(GRID_SEED).laplace(0, 1 / epsilon, len(real))
    keep = noisy.round() >= GRID_MIN_COUNT
    cells = owner[keep].assign(n=noisy[keep].round().astype(int)).reset_index()
    cells["lat"] = ((cells["ci"].astype(float) + 0.5) * CELL_LAT).round(5)
    cells["lng"] = ((cells["cj"].astype(float) + 0.5) * CELL_LON).round(5)

    report = {
        "candidates": int(len(real)),
        "real_cells": int((real > 0).sum()),
        "published": int(keep.sum()),
        "published_real_ge_min": int((keep & (real >= GRID_MIN_COUNT)).sum()),
        "published_no_case": int((keep & (real == 0)).sum()),
        "real_ge_min": int((real >= GRID_MIN_COUNT).sum()),
    }
    return cells, report


def write_atlas():
    topo = json.loads(ATLAS.read_text(encoding="utf-8"))
    for layer in ("towns", "counties"):
        for g in topo["objects"][layer]["geometries"]:
            p = g["properties"]
            g["properties"] = {k: norm(p[k]) for k in ("COUNTYNAME", "TOWNNAME") if k in p}
    (WEB_DATA / "towns.topo.json").write_text(json.dumps(topo, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return {(g["properties"]["COUNTYNAME"], g["properties"]["TOWNNAME"]) for g in topo["objects"]["towns"]["geometries"]}


def main():
    eps_d = float(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EPSILON
    eps_g = float(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_GRID_EPSILON

    accidents = fix_districts(pd.read_csv(ACCIDENTS, usecols=["county", "district", "lat_raw", "lon_raw"]))
    totals = accidents.groupby(["county", "district"]).size()

    dist, real, source = district_release(eps_d)
    cells, grid = grid_release(accidents, eps_g)
    atlas_towns = write_atlas()

    counties = {}
    for c, g in dist.groupby("county"):
        total = int(totals.loc[c].sum())
        drug = float(g["n_noisy"].sum())
        towns = {}
        for r in g.itertuples():
            t_total = int(totals.get((c, r.district), 0))
            towns[r.district] = {
                "total": t_total,
                # below MIN_PUBLISHED_COUNT the count is withheld, as in hotspot_map.html
                "drug": int(round(r.n_noisy)) if r.shown else None,
                "per10k": round(r.n_noisy / t_total * 1e4, 1) if r.shown and t_total else None,
                "severity": r.severity if r.shown else None,
                "recidivistShare": getattr(r, "recidivist_share", None) if r.shown else None,
            }
        shown = round(drug) >= MIN_PUBLISHED_COUNT
        counties[c] = {"total": total, "drug": int(round(drug)) if shown else None,
                       "per10k": round(drug / total * 1e4, 1) if shown else None, "towns": towns}

    out_cells = {}
    for r in cells.itertuples():
        out_cells.setdefault(f"{r.county}|{r.district}", []).append([r.lat, r.lng, r.n])

    meta = {
        "title": "毒駕熱區地圖",
        "source": "114 年（2025）傷亡道路交通事故 × 三方 PSI 交集（模擬資料）",
        "denominator": "114 年傷亡事故件數（公開資料）",
        "epsilonDistrict": eps_d,
        "epsilonGrid": eps_g,
        "epsilonTotal": eps_d + eps_g,
        "minDistrictCount": MIN_PUBLISHED_COUNT,
        "minCellCount": GRID_MIN_COUNT,
        "cellMeters": CELL_M,
        "cellDeg": [CELL_LAT, CELL_LON],
    }
    WEB_DATA.mkdir(parents=True, exist_ok=True)
    dump = lambda name, obj: (WEB_DATA / name).write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    dump("regions.json", {"meta": meta, "counties": counties})
    dump("cells.json", out_cells)

    data_towns = {(c, t) for c, v in counties.items() for t in v["towns"]}
    print(f"web/data: epsilon {eps_d:g} (district, from {source}) + {eps_g:g} (grid) = {eps_d + eps_g:g}")
    print(f"  districts: {len(data_towns)}, shown {int(dist['shown'].sum())}; not on the map: {sorted(data_towns - atlas_towns)}")
    print(f"  grid: {grid['candidates']} candidate cells, {grid['real_cells']} with a real case, {grid['real_ge_min']} with >= {GRID_MIN_COUNT}")
    print(f"        published {grid['published']}: {grid['published_real_ge_min']} really >= {GRID_MIN_COUNT}, {grid['published_no_case']} with no real case")


if __name__ == "__main__":
    main()
