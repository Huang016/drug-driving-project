#!/bin/bash
# Starts the four parties as four separate programs on this machine and waits for them.
# For a recording, open four terminals instead and run `python node.py A` (B, C, K) in each.
cd "$(dirname "$0")" || exit 1
PY="${PSI_PY:-../.venv/Scripts/python.exe}"
[ -x "$PY" ] || PY="../.venv/bin/python"
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8
mkdir -p node_logs
pids=()
for party in K A B C; do
  "$PY" node.py "$party" > "node_logs/$party.log" 2>&1 &
  pids+=($!)
done
status=0
for pid in "${pids[@]}"; do wait "$pid" || status=1; done
for party in A B C K; do echo "──────── node_logs/$party.log"; cat "node_logs/$party.log"; done
exit $status
