"""Stage 3 input: split the analysis table into the part the synthesizer learns
from and a control part it never sees (needed by the privacy attacks).

anon_id is dropped: an identifier has nothing to synthesize. county and district
are joined into one "location" value so that a synthetic row can never pair a
district with the wrong county; they are split back after synthesis.
"""
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
SEED = 20261002
TRAIN_RATIO = 0.8
LOCATION_SEP = "|"


def main():
    table = pd.read_csv(BASE_DIR / "analysis_table.csv")
    table.insert(0, "location", table["county"] + LOCATION_SEP + table["district"])
    table = table.drop(columns=["anon_id", "county", "district"])

    # the released synthetic table is learned from everyone; the split is only for evaluation
    table.to_csv(BASE_DIR / "table_all.csv", index=False)
    train = table.sample(frac=TRAIN_RATIO, random_state=SEED)
    control = table.drop(train.index)
    train.to_csv(BASE_DIR / "table_train.csv", index=False)
    control.to_csv(BASE_DIR / "table_control.csv", index=False)
    print(f"train {len(train)} rows, control {len(control)} rows, columns {list(table.columns)}")


if __name__ == "__main__":
    main()
