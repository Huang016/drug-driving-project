"""Print the PETsARD fidelity and privacy scores of one stage 3 run.

Usage: python summarize_stage3.py out_copula
"""
import sys
from pathlib import Path

import pandas as pd

RISK_GUIDE = 0.09  # PETsARD's recommended ceiling for an anonymeter risk score
SCORES = [
    ("保真度　總分", "fidelity_Score", None),
    ("保真度　單欄分布", "fidelity_Column Shapes", None),
    ("保真度　欄位間關係", "fidelity_Column Pair Trends", None),
    ("隱私　單挑風險", "singling_out_risk", RISK_GUIDE),
    ("隱私　連結風險", "linkability_risk", RISK_GUIDE),
    ("隱私　推論風險", "inference_risk", RISK_GUIDE),
]


def main():
    report = pd.read_csv(Path(sys.argv[1]) / "report_privacy_fidelity_global.csv").iloc[0]
    for label, key, ceiling in SCORES:
        note = "" if ceiling is None else ("　（建議 < %.2f，符合）" % ceiling if report[key] < ceiling else "　（建議 < %.2f，超過）" % ceiling)
        print(f"  {label}：{report[key]:.2f}{note}")


if __name__ == "__main__":
    main()
