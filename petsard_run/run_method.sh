#!/bin/bash
# usage: bash run_method.sh <name> <petsard synthesizer method>      (run from petsard_run/)
#   e.g. bash run_method.sh tvae sdv-single_table-tvae
name=$1; method=$2
PY="../../.venv/Scripts/python.exe"
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8
rm -rf "out_$name" && mkdir -p "out_$name" && cd "out_$name" || exit 1
cp ../A_train.csv ../A_control.csv ../A_train_label.csv ../A_control_label.csv ../A_generalized_schema.yaml .
sed "s/__METHOD__/$method/" ../config_1_synthesize.yaml > config_1_synthesize.yaml

START=$(date +%s)
$PY ../run_petsard.py config_1_synthesize.yaml > log_1_synthesize.txt 2>&1 || { echo "$name step 1 FAILED"; tail -5 log_1_synthesize.txt; exit 1; }
mv petsard_Loader*Postprocessor*.csv synthetic.csv
echo "$name step 1 synthesize: $(( $(date +%s) - START ))s, $(( $(wc -l < synthetic.csv) - 1 )) rows"
python -c "import pandas as pd; d = pd.read_csv('synthetic.csv'); d['is_drug_related'] = d['is_drug_related'].map({1: '是', 0: '否'}); d.to_csv('synthetic_label.csv', index=False)"

START=$(date +%s)
$PY ../run_petsard.py ../config_2_privacy_fidelity.yaml > log_2_privacy_fidelity.txt 2>&1; echo "$name step 2 privacy+fidelity: exit=$? $(( $(date +%s) - START ))s"
mv "petsard_Reporter[global].csv" report_privacy_fidelity_global.csv 2>/dev/null
START=$(date +%s)
$PY ../run_petsard.py ../config_3_utility.yaml > log_3_utility.txt 2>&1; echo "$name step 3 utility: exit=$? $(( $(date +%s) - START ))s"
mv "petsard_Reporter[global].csv" report_utility_global.csv 2>/dev/null
ls
