"""Print the PETsARD global scores of one run folder. Usage: python summarize.py out_copula"""
import sys
from pathlib import Path

import pandas as pd

PREFIXES = ("fidelity_", "singling_out_", "linkability_", "inference_")


def main():
    folder = Path(sys.argv[1])
    report = pd.read_csv(folder / "report_privacy_fidelity_global.csv").iloc[0]
    for key in report.index:
        if key.startswith(PREFIXES) and not key.endswith("_err"):
            print(f"{key}: {report[key]}")
    utility = pd.read_csv(folder / "report_utility_global.csv")
    print(utility[[c for c in utility.columns if c.startswith("utility")]].to_string(index=False))


if __name__ == "__main__":
    main()
