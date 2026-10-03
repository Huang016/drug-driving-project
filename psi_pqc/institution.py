"""What one institution does with its own data during PSI and the field exchange.

Shared by the single-process run (run_psi.py) and the networked nodes (node.py).
An institution only ever touches its own ID list, its own columns and its own
PSI secret.
"""
import json
import random
from pathlib import Path

import pandas as pd

import psi

DATA_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = DATA_DIR / "data" / "raw"
A_PATH = RAW_DIR / "Dataset_A_.csv"
B_PATH = RAW_DIR / "Dataset_B_.csv"
C_PATH = RAW_DIR / "Dataset_C_raw.csv"

LABELS = {"A": "警政A", "B": "檢驗B", "C": "監理C", "K": "協調者"}
INSTITUTION_CODES = ["A", "B", "C"]
C_STAND_IN_NOTICE = "注意：Dataset_C_raw.csv 尚未提供，監理C 先用規格書的納入規則產生 ID 清單，不提供任何欄位"


def person_id(master_id: int) -> str:
    # stand-in for the national ID number each institution holds; the simulated data has none
    return f"SIM{int(master_id):07d}"


class Institution:
    def __init__(self, code: str, ids: list[str], fields: pd.DataFrame):
        self.code = code
        self.label = LABELS[code]
        self._ids = ids
        self._fields = fields.reset_index(drop=True)
        self._secret = psi.new_secret()
        self._order = None
        self._anon_ids = None

    def __len__(self) -> int:
        return len(self._ids)

    def first_blinding(self) -> bytes:
        # the shuffle is remembered here and nowhere else, so only this institution
        # can tell which returned value belongs to which of its records
        self._order = list(range(len(self._ids)))
        random.SystemRandom().shuffle(self._order)
        points = [psi.hash_to_point(self._ids[i]) for i in self._order]
        return psi.pack_points(psi.blind(points, self._secret))

    def reblind(self, data: bytes) -> bytes:
        return psi.pack_points(psi.blind(psi.unpack_points(data), self._secret))

    def receive_own_anon_ids(self, data: bytes) -> None:
        anon_ids = [point.hex() for point in psi.unpack_points(data)]
        self._anon_ids = pd.Series(anon_ids, index=self._order).sort_index()

    def anon_id_list(self) -> bytes:
        # sorted, so the order says nothing about the institution's own records
        return json.dumps(sorted(self._anon_ids)).encode("utf-8")

    def fields_for(self, intersection: set[str]) -> bytes:
        table = self._fields.copy()
        table.insert(0, "anon_id", self._anon_ids)
        return table[table["anon_id"].isin(intersection)].to_csv(index=False).encode("utf-8")


def load_institution(code: str) -> Institution:
    if code == "A":
        a = pd.read_csv(A_PATH)
        # A generalizes before anything leaves: exact coordinates and event time stay behind
        return Institution("A", [person_id(m) for m in a["master_id"]], a[["county", "district"]])

    if code == "B":
        b = pd.read_csv(B_PATH)
        # B stores the same IDs in lower case with stray spaces; normalization has to absorb that
        return Institution("B", [f" {person_id(m).lower()} " for m in b["master_id"]], b[["severity_score"]].astype(int))

    if C_PATH.exists():
        c = pd.read_csv(C_PATH)
        return Institution("C", [person_id(m) for m in c["master_id"]], c[["recidivism_count"]].astype(int))

    # C's table has not been delivered. Its ID list follows the agreed inclusion rule
    # (B positive, or prior offense in A); it contributes no columns until the real file exists.
    # This needs A's and B's files, so it only works where all three are on one machine.
    if not (A_PATH.exists() and B_PATH.exists()):
        raise FileNotFoundError(f"監理C 需要 {C_PATH.name}")
    a, b = pd.read_csv(A_PATH), pd.read_csv(B_PATH)
    c_ids = set(b.loc[b["test_result"] == "陽性", "master_id"]) | set(a.loc[a["prior_offense_flag"] == 1, "master_id"])
    print(f"  {C_STAND_IN_NOTICE}")
    return Institution("C", [person_id(m) for m in sorted(c_ids)], pd.DataFrame(index=range(len(c_ids))))


def merge_fields(tables: list[pd.DataFrame]) -> pd.DataFrame:
    merged = tables[0]
    for table in tables[1:]:
        merged = merged.merge(table, on="anon_id")
    return merged
