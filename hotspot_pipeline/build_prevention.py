"""Stage 7: prevention suggestions for the "預防建議" view of the site (web/data/prevention.json).

Usage: python build_prevention.py   (after build_web_data.py, which writes cells.json)

Two candidate lists, each a place to survey first, not a proven fix:

  camera  Road segments (accidents pooled within SEGMENT_R m of the densest points)
          with at least one fatal (A1) accident and no fixed speed camera within
          CAMERA_R m, ranked by fatal accidents, deaths, then injuries. Cameras: NPA open data 7320,
          fixed cameras only (no mobile or average-speed cameras, no freeways).
  patrol  The drug-driving 500 m cells already published with differential privacy
          in cells.json. Nothing here reads the drug-driving rows again, so the
          privacy budget is unchanged; the busiest hours come from all public accidents
          in the cell. The site ranks counties and districts by the published
          regions.json counts and uses these cells inside a district.

The accident positions are the public 114-year casualty accidents in Dataset A, cleaned
as in build_web_data.py (placeholder and stacked positions are left out).
"""
import json
import math

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from build_web_data import (ACCIDENTS, MIXED_DISTRICTS, ROOT, SPOT_LAT, SPOT_LON, STACKED_MIN, WEB_DATA,
                            fix_districts, located, town_rings)

EXTERNAL = ROOT / "data" / "external"
CAMERAS = EXTERNAL / "speed_cameras.csv"  # 內政部警政署「測速執法設置點」(data.gov.tw 7320)

SEGMENT_R = 150
CAMERA_R = 500
CELL_R = 354  # radius around a 500 m cell centre that covers the cell
KX = 111_320 * math.cos(math.radians(23.7))


def xy(lat, lon) -> np.ndarray:
    return np.column_stack([np.asarray(lon, dtype=float) * KX, np.asarray(lat, dtype=float) * 111_320])


def clean_positions() -> pd.DataFrame:
    acc = fix_districts(pd.read_csv(ACCIDENTS, usecols=[
        "event_hour", "county", "district", "lat_raw", "lon_raw", "accident_class", "death_count", "injury_count"]))
    acc = acc.dropna(subset=["district", "lat_raw", "lon_raw"]).reset_index(drop=True)
    rings = town_rings(json.loads((WEB_DATA / "towns.topo.json").read_text(encoding="utf-8")))
    cell = pd.DataFrame({"si": np.floor(acc["lat_raw"] / SPOT_LAT).astype(int), "sj": np.floor(acc["lon_raw"] / SPOT_LON).astype(int),
                         "district": acc["district"]})
    mixed = cell.groupby(["si", "sj"])["district"].transform("nunique") >= MIXED_DISTRICTS
    stacked = acc.groupby(["lat_raw", "lon_raw"])["county"].transform("size") >= STACKED_MIN
    acc = acc[located(acc, rings) & ~mixed & ~stacked].reset_index(drop=True)
    return acc


def segments(acc: pd.DataFrame) -> np.ndarray:
    """Same pooling as junction_clusters in build_web_data.py, at road-segment size."""
    pts = xy(acc["lat_raw"], acc["lon_raw"])
    tree = cKDTree(pts)
    density = tree.query_ball_point(pts, SEGMENT_R, return_length=True)
    seg = np.full(len(pts), -1)
    k = 0
    for i in np.argsort(-density, kind="stable"):
        if seg[i] >= 0:
            continue
        near = np.asarray(tree.query_ball_point(pts[i], SEGMENT_R))
        seg[near[seg[near] < 0]] = k
        k += 1
    return seg


def camera_list(acc: pd.DataFrame, cam_tree: cKDTree) -> list:
    acc = acc.assign(seg=segments(acc), a1=(acc["accident_class"] == "A1").astype(int),
                     night=((acc["event_hour"] >= 22) | (acc["event_hour"] <= 5)).astype(int))
    seg = (acc.groupby("seg").agg(n=("a1", "size"), a1=("a1", "sum"), deaths=("death_count", "sum"),
                                  injuries=("injury_count", "sum"), night=("night", "mean"),
                                  lat=("lat_raw", "median"), lng=("lon_raw", "median"),
                                  county=("county", lambda s: s.mode().iat[0]),
                                  district=("district", lambda s: s.mode().iat[0])))
    seg = seg[seg["a1"] >= 1]
    dist, _ = cam_tree.query(xy(seg["lat"], seg["lng"]))
    seg = seg.assign(camera_m=dist.round().astype(int))
    seg = seg[seg["camera_m"] > CAMERA_R].sort_values(["a1", "deaths", "injuries"], ascending=False)
    return [{"county": r.county, "district": r.district, "lat": round(r.lat, 5), "lng": round(r.lng, 5),
             "n": int(r.n), "a1": int(r.a1), "deaths": int(r.deaths), "injuries": int(r.injuries),
             "night": round(float(r.night), 2), "cameraM": int(r.camera_m)} for r in seg.itertuples()]


def patrol_list(acc: pd.DataFrame, tree: cKDTree) -> list:
    cells = json.loads((WEB_DATA / "cells.json").read_text(encoding="utf-8"))
    hours = acc["event_hour"].to_numpy()
    out = []
    for key, rows in cells.items():
        county, district = key.split("|")
        for lat, lng, n in rows:
            h = pd.Series(hours[tree.query_ball_point(xy([lat], [lng])[0], CELL_R)])
            out.append({"county": county, "district": district, "lat": lat, "lng": lng, "n": n,
                        "peakHours": [int(x) for x in h.value_counts().index[:3]],
                        "night": round(float(((h >= 22) | (h <= 5)).mean()), 2) if len(h) else None})
    return sorted(out, key=lambda r: -r["n"])


def main():
    acc = clean_positions()
    tree = cKDTree(xy(acc["lat_raw"], acc["lon_raw"]))
    cams = pd.read_csv(CAMERAS, skiprows=[1])  # the second row repeats the header in Chinese
    cams = cams[cams["Latitude"].between(21.8, 25.5) & cams["Longitude"].between(118, 122.1)].reset_index(drop=True)
    cam_tree = cKDTree(xy(cams["Latitude"], cams["Longitude"]))

    data = {
        "meta": {"segmentRadius": SEGMENT_R, "cameraRadius": CAMERA_R, "cameraCount": int(len(cams)),
                 "accidents": int(len(acc))},
        "camera": camera_list(acc, cam_tree),
        "patrol": patrol_list(acc, tree),
        "cameras": [[round(a, 5), round(b, 5)] for a, b in zip(cams["Latitude"], cams["Longitude"])],
    }
    (WEB_DATA / "prevention.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"accidents with a usable position: {len(acc):,}; fixed cameras: {len(cams):,}")
    for k in ("camera", "patrol"):
        top = data[k][0]
        print(f"{k:7s} {len(data[k]):5d} candidates; first: {top['county']}{top['district']} n={top['n']}")


if __name__ == "__main__":
    main()
