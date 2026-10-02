"""Step 4b: supplementary fidelity check.

sdmetrics' "Column Pair Trends" only scores column pairs whose real association
(Cramér's V) passes a threshold. In this simulated data most columns are nearly
independent, so almost every pair is skipped and PETsARD's fidelity score ends up
being the Column Shapes score alone. To report pair-level fidelity honestly, this step
computes contingency similarity (1 - total variation distance of the joint
distribution) for EVERY column pair, real vs synthetic (split 1 after the step5 rule repair = released data).
"""
import itertools
import json
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent


def main():
    real = pd.read_csv(BASE / "ttp" / "wide_petsard.csv").astype(str)
    syn_file = next((BASE / "petsard_output").glob("*holdout_[[]10-01[]]*Postprocessor*.csv"))
    from step5_finalize_synthetic import repair  # measure the released data, i.e. after the §35 repair
    syn = repair(pd.read_csv(syn_file))[0][real.columns].astype(str)

    def similarity(a, b):
        joint = pd.concat([real.groupby([a, b]).size() / len(real), syn.groupby([a, b]).size() / len(syn)], axis=1).fillna(0)
        return 1 - 0.5 * (joint[0] - joint[1]).abs().sum()

    pairs = pd.Series({f"{a} × {b}": similarity(a, b) for a, b in itertools.combinations(real.columns, 2)})
    result = {"pairs": len(pairs), "mean": round(pairs.mean(), 3), "min": round(pairs.min(), 3),
              "worst_5": pairs.nsmallest(5).round(3).to_dict()}
    (BASE / "logs").mkdir(exist_ok=True)
    (BASE / "logs" / "pair_fidelity.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
