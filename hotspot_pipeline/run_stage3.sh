#!/bin/bash
# usage: bash run_stage3.sh <name> <petsard synthesizer method>     (run from hotspot_pipeline/)
#   e.g. bash run_stage3.sh copula petsard-gaussian_copula
name=$1; method=$2
PY="${PETSARD_PY:-../../.venv/Scripts/python.exe}"
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8
"${PETSARD_PY:-python}" prepare_petsard_input.py || exit 1
# linkability attack: link location to whatever the other parties contributed
secret_cols=$(head -1 table_train.csv | tr ',' '\n' | grep -v -x "location" | paste -sd, - | sed 's/,/, /g')
# a singling-out query cannot use more columns than the table has
n_cols=$(head -1 table_train.csv | tr ',' '
' | wc -l); [ "$n_cols" -gt 3 ] && n_cols=3
rm -rf "out_$name" && mkdir -p "out_$name" && cd "out_$name" || exit 1
cp ../table_all.csv ../table_train.csv ../table_control.csv ../table_schema.yaml .
sed "s/__METHOD__/$method/" ../config_1_synthesize.yaml > config_1_synthesize.yaml
sed -e "s/__SECRET_COLS__/$secret_cols/" -e "s/__N_COLS__/$n_cols/" ../config_2_privacy_fidelity.yaml > config_2_privacy_fidelity.yaml

$PY ../../petsard_run/run_petsard.py config_1_synthesize.yaml > log_1_synthesize.txt 2>&1 || { echo "$name synthesize FAILED"; tail -5 log_1_synthesize.txt; exit 1; }
mv petsard_Loader*Postprocessor*.csv synthetic.csv
echo "$name synthesize: $(( $(wc -l < synthetic.csv) - 1 )) rows"
$PY ../../petsard_run/run_petsard.py config_2_privacy_fidelity.yaml > log_2_privacy_fidelity.txt 2>&1; echo "$name privacy+fidelity: exit=$?"
mv "petsard_Reporter[global].csv" report_privacy_fidelity_global.csv 2>/dev/null

# release version: same method, learned from the whole analysis table
sed -e "s/__METHOD__/$method/" -e "s/table_train.csv/table_all.csv/" ../config_1_synthesize.yaml > config_1_release.yaml
$PY ../../petsard_run/run_petsard.py config_1_release.yaml > log_1_release.txt 2>&1 || { echo "$name release synthesize FAILED"; tail -5 log_1_release.txt; exit 1; }
mv petsard_Loader*Postprocessor*.csv synthetic_release.csv
echo "$name release table: $(( $(wc -l < synthetic_release.csv) - 1 )) rows"
