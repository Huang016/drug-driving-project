"""Stage 7: prevention suggestions for the "預防建議" view of the site (web/data/prevention.json).

Usage: python build_prevention.py   (after build_web_data.py, which writes cells.json)

The places are the drug-driving 500 m cells already published with differential
privacy (cells.json); nothing here reads the drug-driving rows again, so the privacy
budget is unchanged. Each cell is then described with public data only:

  - the public 114-year casualty accidents inside the cell (Dataset A positions,
    cleaned as in build_web_data.py): count, deaths, injuries, night share, peak hours
  - its busiest junction: accidents pooled within SPOT_RADIUS_M (junction_clusters)
  - the fixed speed cameras inside the cell (NPA open data 7320)

A cell where most accidents sit on placeholder positions (city-centre points that
collect accidents from many districts, see build_web_data.py) is left out: its count
says where accidents were registered, not where they happened.

Each remaining cell gets the measures its numbers point to (codes, worded on the site):

  camera    no fixed speed camera in the cell               -> speed camera / speed reduction
  junction  a junction with >= JUNCTION_MIN accidents       -> red-light camera, signal and junction redesign
  night     night share >= NIGHT_FACTOR x the national one  -> night lighting, abnormal-driving monitoring
  fatal     at least one death in the cell                  -> speed reduction first
  patrol    always                                          -> checkpoints in the peak hours

These are places to survey first, not proven fixes.
"""
import json

import numpy as np
import pandas as pd

from build_web_data import (ACCIDENTS, CELL_LAT, CELL_LON, MIXED_DISTRICTS, ROOT, SPOT_LAT, SPOT_LON, STACKED_MIN,
                            WEB_DATA, fix_districts, junction_clusters, located, town_rings)

CAMERAS = ROOT / "data" / "external" / "speed_cameras.csv"  # 內政部警政署「測速執法設置點」(data.gov.tw 7320)

JUNCTION_MIN = 15
NIGHT_FACTOR = 1.25
NIGHT_MIN_ACCIDENTS = 30  # below this a night share is too unstable to flag
NIGHT_HOURS = (22, 5)  # 22:00-05:59
MIN_LOCATED_SHARE = 0.5  # below this the cell is mostly placeholder positions


def is_night(hour: pd.Series) -> pd.Series:
    return (hour >= NIGHT_HOURS[0]) | (hour <= NIGHT_HOURS[1])


def clean_positions(acc: pd.DataFrame) -> pd.DataFrame:
    rings = town_rings(json.loads((WEB_DATA / "towns.topo.json").read_text(encoding="utf-8")))
    cell = pd.DataFrame({"si": np.floor(acc["lat_raw"] / SPOT_LAT).astype(int), "sj": np.floor(acc["lon_raw"] / SPOT_LON).astype(int),
                         "district": acc["district"]})
    mixed = cell.groupby(["si", "sj"])["district"].transform("nunique") >= MIXED_DISTRICTS
    stacked = acc.groupby(["lat_raw", "lon_raw"])["county"].transform("size") >= STACKED_MIN
    return acc[located(acc, rings) & ~mixed & ~stacked].reset_index(drop=True)


def in_cell(lat, lng, lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    return (np.abs(lats - lat) <= CELL_LAT / 2) & (np.abs(lons - lng) <= CELL_LON / 2)


def describe(raw: pd.DataFrame, acc: pd.DataFrame, cams: pd.DataFrame, night_avg: float) -> tuple:
    published = json.loads((WEB_DATA / "cells.json").read_text(encoding="utf-8"))
    raw_lats, raw_lons = raw["lat_raw"].to_numpy(), raw["lon_raw"].to_numpy()
    lats, lons = acc["lat_raw"].to_numpy(), acc["lon_raw"].to_numpy()
    cam_lat, cam_lon = cams["Latitude"].to_numpy(), cams["Longitude"].to_numpy()
    out, dropped = [], []
    for key, rows in published.items():
        county, district = key.split("|")
        for lat, lng, n in rows:
            sub = acc[in_cell(lat, lng, lats, lons)]
            registered = int(in_cell(lat, lng, raw_lats, raw_lons).sum())
            if registered and len(sub) / registered < MIN_LOCATED_SHARE:
                dropped.append(f"{county}{district} ({lat}, {lng}) n={n}: {len(sub)}/{registered} accidents located")
                continue
            cameras = int(in_cell(lat, lng, cam_lat, cam_lon).sum())
            night = float(is_night(sub["event_hour"]).mean()) if len(sub) else 0.0
            junction = None
            if len(sub):
                spot = junction_clusters(sub["lat_raw"].to_numpy(), sub["lon_raw"].to_numpy())
                best = pd.Series(spot).value_counts()
                top = sub[spot == best.index[0]]
                junction = [round(top["lat_raw"].median(), 5), round(top["lon_raw"].median(), 5), int(best.iat[0])]
            deaths = int(sub["death_count"].sum())
            measures = []
            if cameras == 0:
                measures.append("camera")
            if junction and junction[2] >= JUNCTION_MIN:
                measures.append("junction")
            if len(sub) >= NIGHT_MIN_ACCIDENTS and night >= NIGHT_FACTOR * night_avg:
                measures.append("night")
            if deaths:
                measures.append("fatal")
            measures.append("patrol")
            out.append({"county": county, "district": district, "lat": lat, "lng": lng, "n": n,
                        "accidents": int(len(sub)), "deaths": deaths, "injuries": int(sub["injury_count"].sum()),
                        "night": round(night, 2), "peakHours": [int(h) for h in sub["event_hour"].value_counts().index[:3]],
                        "cameras": cameras, "junction": junction, "measures": measures})
    return sorted(out, key=lambda r: (-r["n"], -r["deaths"], -r["accidents"])), dropped


def main():
    raw = fix_districts(pd.read_csv(ACCIDENTS, usecols=[
        "event_hour", "county", "district", "lat_raw", "lon_raw", "accident_class", "death_count", "injury_count"]))
    raw = raw.dropna(subset=["district", "lat_raw", "lon_raw"]).reset_index(drop=True)
    acc = clean_positions(raw)
    cams = pd.read_csv(CAMERAS, skiprows=[1])  # the second row repeats the header in Chinese
    cams = cams[cams["Latitude"].between(21.8, 25.5) & cams["Longitude"].between(118, 122.1)].reset_index(drop=True)
    night_avg = float(is_night(acc["event_hour"]).mean())
    cells, dropped = describe(raw, acc, cams, night_avg)
    data = {
        "meta": {"accidents": int(len(acc)), "cameraCount": int(len(cams)), "nightAvg": round(night_avg, 3),
                 "nightFactor": NIGHT_FACTOR, "nightHours": list(NIGHT_HOURS), "junctionMin": JUNCTION_MIN,
                 "placeholderCells": len(dropped)},
        "cells": cells,
        "cameras": [[round(a, 5), round(b, 5)] for a, b in zip(cams["Latitude"], cams["Longitude"])],
    }
    (WEB_DATA / "prevention.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    counts = pd.Series([m for c in cells for m in c["measures"]]).value_counts().to_dict()
    print(f"accidents with a usable position: {len(acc):,}; fixed cameras: {len(cams):,}; night share {night_avg:.3f}")
    print(f"{len(cells)} published drug-driving cells; measures: {counts}")
    print(f"left out {len(dropped)} cells made mostly of placeholder positions:", *dropped, sep="\n  ")


if __name__ == "__main__":
    main()
