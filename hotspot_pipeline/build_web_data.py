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

General accidents (the "一般事故" view)
  Every casualty accident in A is public open data, so these figures carry no
  noise. Per county and district: accident count, A1 count, deaths, injuries,
  the busiest hours and the vehicle mix. Spots: accidents are pooled within
  SPOT_RADIUS_M of the densest points (about one junction, see
  junction_clusters) and the top SPOTS_PER_AREA spots per county and per
  district are published for each ranking (accidents, deaths, injuries),
  placed at the median accident position.
  Some accidents carry a placeholder position (whole-minute coordinates such as
  24.0, 121.0, or a city-centre point shared by accidents from far-away
  districts); an accident is used for spots only if it lies inside, or within
  SPOT_TOLERANCE_M of, the district it is registered in, and its 100 m cell
  (SPOT_M) does not hold accidents from MIXED_DISTRICTS or more districts.
"""
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

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

SPOT_RADIUS_M = 30  # about one junction; 50 m already merges neighbouring junctions in Taipei
SPOT_M = 100  # cell used only to find placeholder points (see MIXED_DISTRICTS)
SPOT_LAT = SPOT_M / 111_320
SPOT_LON = SPOT_M / (111_320 * math.cos(math.radians(23.7)))
SPOT_TOLERANCE_M = 300  # the town boundaries are simplified, so a real position can fall just outside
MIXED_DISTRICTS = 5
STACKED_MIN, STACKED_POINTS = 15, 3  # >= 15 accidents on <= 3 exact positions
SPOTS_PER_AREA = 10
SPOT_RANKINGS = {"n": "件數", "deaths": "死亡人數", "injuries": "受傷人數"}

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


def town_rings(topo: dict) -> dict:
    """(county, town) -> list of rings, each an (n, 2) array of lon/lat, decoded from the topojson."""
    sx, sy = topo["transform"]["scale"]
    tx, ty = topo["transform"]["translate"]
    arcs = []
    for arc in topo["arcs"]:
        q = np.cumsum(np.array(arc, dtype=float), axis=0)
        arcs.append(np.column_stack([q[:, 0] * sx + tx, q[:, 1] * sy + ty]))

    def ring(ids):
        return np.vstack([arcs[i] if i >= 0 else arcs[~i][::-1] for i in ids])

    out = {}
    for g in topo["objects"]["towns"]["geometries"]:
        polygons = g["arcs"] if g["type"] == "MultiPolygon" else [g["arcs"]]
        key = (norm(g["properties"]["COUNTYNAME"]), norm(g["properties"]["TOWNNAME"]))
        out.setdefault(key, []).extend(ring(r) for poly in polygons for r in poly)
    return out


def near_own_town(lon: np.ndarray, lat: np.ndarray, rings: list) -> np.ndarray:
    """Inside the rings (even-odd rule), or within SPOT_TOLERANCE_M of their edge."""
    inside = np.zeros(len(lon), dtype=bool)
    dist = np.full(len(lon), np.inf)
    kx = 111_320 * math.cos(math.radians(23.7))
    for r in rings:
        x1, y1, x2, y2 = r[:-1, 0], r[:-1, 1], r[1:, 0], r[1:, 1]
        for a in range(0, len(lon), 2000):  # chunks keep the point x edge matrix small
            px, py = lon[a:a + 2000, None], lat[a:a + 2000, None]
            crosses = ((y1 > py) != (y2 > py)) & (px < (x2 - x1) * (py - y1) / np.where(y2 == y1, 1e-12, y2 - y1) + x1)
            inside[a:a + 2000] ^= (crosses.sum(axis=1) % 2).astype(bool)
            dx, dy = (x2 - x1) * kx, (y2 - y1) * 111_320
            qx, qy = (px - x1) * kx, (py - y1) * 111_320
            t = np.clip((qx * dx + qy * dy) / np.where(dx * dx + dy * dy == 0, 1, dx * dx + dy * dy), 0, 1)
            d = np.hypot(qx - t * dx, qy - t * dy).min(axis=1)
            dist[a:a + 2000] = np.minimum(dist[a:a + 2000], d)
    return inside | (dist <= SPOT_TOLERANCE_M)


def located(acc: pd.DataFrame, rings: dict) -> pd.Series:
    ok = pd.Series(False, index=acc.index)
    for (c, d), idx in acc.groupby(["county", "district"]).groups.items():
        if (c, d) in rings:
            sub = acc.loc[idx]
            ok.loc[idx] = near_own_town(sub["lon_raw"].to_numpy(), sub["lat_raw"].to_numpy(), rings[(c, d)])
    return ok


def vehicle_group(vehicle_type: pd.Series) -> pd.Series:
    kind = vehicle_type.str.split("-").str[0]
    return kind.replace({"小客車(含客、貨兩用)": "小客車", "人": "行人", "慢車": "自行車等慢車",
                         "曳引車": "大型車", "半聯結車": "大型車", "全聯結車": "大型車", "大貨車": "大型車", "大客車": "大型車"})


def area_profile(g: pd.DataFrame) -> dict:
    hours = g["event_hour"].value_counts()
    vehicles = vehicle_group(g["vehicle_type"]).value_counts(normalize=True)
    return {
        "total": int(len(g)),
        "a1": int((g["accident_class"] == "A1").sum()),
        "deaths": int(g["death_count"].sum()),
        "injuries": int(g["injury_count"].sum()),
        "peakHours": [int(h) for h in hours.nlargest(3).index],
        "vehicles": [[k, round(float(v), 3)] for k, v in vehicles.head(3).items()],
    }


def top_spots(cells: pd.DataFrame) -> dict:
    """The SPOTS_PER_AREA cells for each ranking, as [lat, lng, accidents, deaths, injuries, stacked 0/1]."""
    out = {}
    for key in SPOT_RANKINGS:
        order = [key] + [k for k in ("n", "injuries", "deaths") if k != key]
        best = cells[cells[key] > 0].sort_values(order, ascending=False, kind="stable").head(SPOTS_PER_AREA)
        out[key] = [[round(r.lat, 5), round(r.lng, 5), int(r.n), int(r.deaths), int(r.injuries), int(r.stacked)]
                    for r in best.itertuples()]
    return out


def junction_clusters(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """Spot id per accident. The densest accident not yet taken becomes a spot and takes every
    untaken accident within SPOT_RADIUS_M; repeat. Spots so centre on junctions instead of
    being split by grid lines."""
    xy = np.column_stack([lon * 111_320 * math.cos(math.radians(23.7)), lat * 111_320])
    tree = cKDTree(xy)
    density = tree.query_ball_point(xy, SPOT_RADIUS_M, return_length=True)
    spot = np.full(len(xy), -1)
    k = 0
    for i in np.argsort(-density, kind="stable"):
        if spot[i] >= 0:
            continue
        near = np.asarray(tree.query_ball_point(xy[i], SPOT_RADIUS_M))
        spot[near[spot[near] < 0]] = k
        k += 1
    return spot


def general_release(acc: pd.DataFrame, rings: dict):
    profiles = {"nation": area_profile(acc), "counties": {}}
    for c, g in acc.groupby("county"):
        profiles["counties"][c] = {"profile": area_profile(g),
                                   "towns": {d: area_profile(t) for d, t in g.groupby("district")}}

    acc = acc.assign(si=np.floor(acc["lat_raw"] / SPOT_LAT).astype(int), sj=np.floor(acc["lon_raw"] / SPOT_LON).astype(int))
    # a placeholder point collects accidents registered all over the city; a real junction on a border has two or three
    mixed = acc.groupby(["si", "sj"])["district"].transform("nunique") >= MIXED_DISTRICTS
    ok = located(acc.dropna(subset=["district"]), rings).reindex(acc.index, fill_value=False) & ~mixed
    pos = acc[ok].reset_index(drop=True)
    pos["spot"] = junction_clusters(pos["lat_raw"].to_numpy(), pos["lon_raw"].to_numpy())
    cells = (pos.groupby("spot")
             .agg(n=("lat_raw", "size"), deaths=("death_count", "sum"), injuries=("injury_count", "sum"),
                  lat=("lat_raw", "median"), lng=("lon_raw", "median"),
                  county=("county", lambda s: s.mode().iat[0]), district=("district", lambda s: s.mode().iat[0]))
             .reset_index())
    points = pos.drop_duplicates(["spot", "lat_raw", "lon_raw"]).groupby("spot").size().rename("points")
    cells = cells.join(points, on="spot")
    # many accidents on one or two exact points: probably a recorded address (a station, a landmark), not where it happened
    cells["stacked"] = (cells["n"] >= STACKED_MIN) & (cells["points"] <= STACKED_POINTS)
    spots = {"county": {c: top_spots(g) for c, g in cells.groupby("county")},
             "town": {f"{c}|{d}": top_spots(g) for (c, d), g in cells.groupby(["county", "district"])}}
    report = {"accidents": int(len(acc)), "located": int(ok.sum()), "placeholder": int(len(acc) - ok.sum()),
              "cells": int(len(cells)), "top": cells.nlargest(5, "n")[["county", "district", "n"]].values.tolist()}
    return profiles, spots, report


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

    accidents = fix_districts(pd.read_csv(ACCIDENTS, usecols=[
        "county", "district", "lat_raw", "lon_raw", "event_hour", "vehicle_type", "accident_class", "death_count", "injury_count"]))
    totals = accidents.groupby(["county", "district"]).size()

    dist, real, source = district_release(eps_d)
    cells, grid = grid_release(accidents, eps_g)
    atlas_towns = write_atlas()
    general, spots, spot_report = general_release(accidents, town_rings(json.loads((WEB_DATA / "towns.topo.json").read_text(encoding="utf-8"))))

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
    general_meta = {
        "source": "114 年（2025）傷亡道路交通事故（公開資料，未加雜訊）",
        "spotRadius": SPOT_RADIUS_M,
        "spotsPerArea": SPOTS_PER_AREA,
        "rankings": SPOT_RANKINGS,
        "placeholder": spot_report["placeholder"],
    }
    WEB_DATA.mkdir(parents=True, exist_ok=True)
    dump = lambda name, obj: (WEB_DATA / name).write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    dump("regions.json", {"meta": meta, "counties": counties})
    dump("cells.json", out_cells)
    dump("general.json", {"meta": general_meta, "profiles": general, "spots": spots})

    data_towns = {(c, t) for c, v in counties.items() for t in v["towns"]}
    print(f"web/data: epsilon {eps_d:g} (district, from {source}) + {eps_g:g} (grid) = {eps_d + eps_g:g}")
    print(f"  districts: {len(data_towns)}, shown {int(dist['shown'].sum())}; not on the map: {sorted(data_towns - atlas_towns)}")
    print(f"  grid: {grid['candidates']} candidate cells, {grid['real_cells']} with a real case, {grid['real_ge_min']} with >= {GRID_MIN_COUNT}")
    print(f"        published {grid['published']}: {grid['published_real_ge_min']} really >= {GRID_MIN_COUNT}, {grid['published_no_case']} with no real case")
    print(f"  general: {spot_report['accidents']} accidents, {spot_report['located']} placed in their own district, "
          f"{spot_report['placeholder']} left out of the spots (placeholder position)")
    print(f"           {spot_report['cells']} spots of {SPOT_RADIUS_M} m radius; busiest: {spot_report['top']}")


if __name__ == "__main__":
    main()
