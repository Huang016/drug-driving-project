"""Step 0: simulate three institutions, each holding only its own table.

Each party keeps a local `person_id` (stands in for the real national ID number).
The old `hashed_id` (unkeyed SHA-256 with a public salt over a 6-digit id) is dropped:
the id space has only 10^6 values, so anyone with the salt can reverse it in seconds.
"""
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent
INPUT = BASE / "input"
PARTIES = BASE / "parties"

SOURCES = {
    "A": "PETsARD_Dataset_A_aligned.csv",  # police: traffic events
    "B": "dataset_b_aligned.csv",          # lab: drug tests
    "C": "dataset_c_aligned.csv",          # motor vehicle office: sanctions
}


def main():
    for party, name in SOURCES.items():
        df = pd.read_csv(INPUT / name, encoding="utf-8-sig")
        df.insert(0, "person_id", df["master_id"].map(lambda m: f"P{m:06d}"))
        df = df.drop(columns=["master_id", "hashed_id"])
        out = PARTIES / party
        out.mkdir(parents=True, exist_ok=True)
        df.to_csv(out / "local_records.csv", index=False)
        print(f"party {party}: {len(df)} rows -> parties/{party}/local_records.csv")


if __name__ == "__main__":
    main()
