#!/usr/bin/env bash
# Full pipeline. Needs Python 3.10/3.11 (PETsARD 1.10.1 does not install on 3.12+).
#   python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt
set -euo pipefail
cd "$(dirname "$0")"
PY=${PY:-.venv/bin/python}

$PY datagen/build_official_accidents.py  # official 114年 A1/A2 data -> one row per accident
$PY datagen/generate_abc_v3.py   # simulated A/B/C (v3: official casualties, §35 sanctions)
$PY datagen/validate_v3.py
$PY step0_prepare_parties.py     # three institutions, each with only its own table
$PY step1_ecdh_psi.py            # three-party ECDH-PSI -> psi_token for A∩B∩C
$PY step2_pqc_transfer.py        # ML-KEM-768+X25519 / AES-GCM / ML-DSA-65 to the TTP
$PY step3_build_wide_table.py    # TTP: denormalize into one wide table
rm -rf petsard_output
$PY step4_run_petsard.py         # TTP: PETsARD synthesis + evaluation
$PY step4b_pair_fidelity.py       # TTP: contingency similarity for every column pair
$PY step5_finalize_synthetic.py  # TTP: repair rules, re-derive fields -> synthetic_wide.csv
$PY step6_dp_heatmap.py          # TTP: county x day/night hotspot map, Laplace eps = 1
$PY verify_pipeline.py
