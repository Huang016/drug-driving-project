"""Print the PETsARD fidelity and privacy scores of one stage 3 run.

Usage: python summarize_stage3.py out_copula

Each anonymeter risk is printed with what it is made of, so a 0 can be read:
risk = (attack rate on the training rows - attack rate on the control rows) / (1 - control rate),
i.e. how much more often the attack hits people the synthesizer learned from than people it never saw.
"""
import sys
from pathlib import Path

import pandas as pd

RISK_GUIDE = 0.09  # PETsARD's recommended ceiling for an anonymeter risk score
FIDELITY = [
    ("保真度　總分", "fidelity_Score"),
    ("保真度　單欄分布", "fidelity_Column Shapes"),
    ("保真度　欄位間關係", "fidelity_Column Pair Trends"),
]
RISKS = [
    ("指認性風險", "singling_out"),
    ("連結性風險", "linkability"),
    ("推論性風險", "inference"),
]


def main():
    report = pd.read_csv(Path(sys.argv[1]) / "report_privacy_fidelity_global.csv").iloc[0]
    for label, key in FIDELITY:
        print(f"  {label}：{report[key]:.2f}")
    for label, key in RISKS:
        risk = report[f"{key}_risk"]
        verdict = "符合" if risk < RISK_GUIDE else "超過"
        print(f"  隱私　{label}：{risk:.2f}（95% 信賴區間 {report[f'{key}_risk_CI_btm']:.2f}–{report[f'{key}_risk_CI_top']:.2f}；"
              f"建議 < {RISK_GUIDE:.2f}，{verdict}）")
        print(f"        攻擊猜中率：合成器看過的人 {report[f'{key}_attack_rate']:.2f}、"
              f"沒看過的人（對照組） {report[f'{key}_control_rate']:.2f}、亂猜基準 {report[f'{key}_baseline_rate']:.2f}")
    print("  風險 =（看過的人猜中率 − 沒看過的人猜中率）÷（1 − 沒看過的人猜中率）；兩者相同代表合成資料沒有洩漏特定的人")
    print("  註：demo 只用 3 欄分析表、切分一次，信賴區間較寬；正式評測為 15 欄寬表、50/50 切分重複 10 次")


if __name__ == "__main__":
    main()
