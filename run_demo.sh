#!/bin/bash
# Runs the whole pipeline once, stage by stage.
#   bash run_demo.sh            run straight through
#   bash run_demo.sh --pause    wait for Enter between stages (for screen recording)
#   bash run_demo.sh --pause 2  use another privacy budget epsilon (default 1)
cd "$(dirname "$0")" || exit 1
PY="$PWD/.venv/Scripts/python.exe"
[ -x "$PY" ] || PY="$PWD/.venv/bin/python"
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8
pause=0; [ "$1" = "--pause" ] && { pause=1; shift; }
epsilon=${1:-1}
METHOD_NAME=copula
METHOD=petsard-gaussian_copula

stage() {
  [ "$pause" = 1 ] && read -r -p "（按 Enter 進入下一階段）"
  printf '\n════════════════════════════════════════════════════════════\n %s\n════════════════════════════════════════════════════════════\n' "$1"
}

stage "為什麼需要這套流程：原始資料的重新識別風險"
"$PY" hotspot_pipeline/reidentification_risk.py || exit 1

stage "階段一、二：PSI 找交集（ECDH 盲化）＋ 只傳必要欄位，全程 ML-KEM 加密"
(cd psi_pqc && "$PY" run_psi.py) || exit 1

stage "階段三：PETsARD 產生合成資料並評估"
(cd hotspot_pipeline && PETSARD_PY="$PY" bash run_stage3.sh "$METHOD_NAME" "$METHOD" && "$PY" summarize_stage3.py "out_$METHOD_NAME") || exit 1

stage "階段四、五：鄉鎮熱區統計，發布前加差分隱私（ε = $epsilon）"
(cd hotspot_pipeline && "$PY" hotspot_dp.py "out_$METHOD_NAME/synthetic_release.csv" "$epsilon" \
  && "$PY" make_hotspot_map.py "out_$METHOD_NAME/hotspot_eps$epsilon.csv" \
  && mkdir -p results && cp "out_$METHOD_NAME/hotspot_eps$epsilon.csv" "out_$METHOD_NAME/hotspot_map.html" results/) || exit 1

printf '\n完成。熱區地圖：hotspot_pipeline/results/hotspot_map.html\n'
