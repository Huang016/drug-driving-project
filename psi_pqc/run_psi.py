"""Stage 1 + 2: three-party private set intersection over the post-quantum
channel, then the minimal field exchange that gives the coordinator its
analysis table.

Stage 1 (PSI). For each institution in turn:
  it blinds its own identifiers with its secret and shuffles them,
  the other two institutions each blind the list again, keeping the order,
  the triple-blinded list comes back to the institution that started it.
Each institution then knows the anonymous ID of its own records and nothing
about anyone else's. The coordinator receives the three lists of anonymous IDs
and keeps the ones that appear in all three.

Stage 2. The coordinator tells each institution which anonymous IDs are in the
intersection. Each institution sends only the columns the hotspot analysis
needs, for those people only.

Everything that crosses between parties is sealed with ML-KEM-768 + AES-256-GCM
and written to wire/, which is exactly what an eavesdropper would see.

Known limits of this design (semi-honest parties assumed): the coordinator can
also see how many people any two institutions share, and each institution
learns which of its own people are in the intersection.
"""
import io
import json
import random
import re
import shutil
from pathlib import Path

import pandas as pd

import pqc_channel
import psi

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR.parent
A_PATH = DATA_DIR / "PETsARD_Dataset_A_aligned.csv"
B_PATH = DATA_DIR / "dataset_b_aligned.csv"
C_PATH = DATA_DIR / "dataset_c_aligned.csv"
WIRE_DIR = BASE_DIR / "wire"
OUTPUT = DATA_DIR / "hotspot_pipeline" / "analysis_table.csv"
RAW_ID_PATTERN = re.compile(rb"(?i)sim\d{7}")


def person_id(master_id: int) -> str:
    # stand-in for the national ID number each institution holds; the simulated data has none
    return f"SIM{int(master_id):07d}"


class Party:
    def __init__(self, code: str, label: str):
        self.code = code
        self.label = label
        self.keys = pqc_channel.generate_keypair()


class Institution(Party):
    def __init__(self, code: str, label: str, ids: list[str], fields: pd.DataFrame):
        super().__init__(code, label)
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


class Wire:
    """Carries sealed messages between parties and keeps a copy of what was on the wire."""

    def __init__(self, directory: Path):
        self.directory = directory
        shutil.rmtree(directory, ignore_errors=True)
        directory.mkdir(parents=True)
        self.count = 0
        self.total_bytes = 0

    def send(self, sender: Party, recipient: Party, step: str, payload: bytes) -> bytes:
        context = f"{sender.code}->{recipient.code}|{step}"
        envelope = pqc_channel.seal(recipient.keys.public, payload, context)
        self.count += 1
        self.total_bytes += len(envelope)
        (self.directory / f"{self.count:02d}_{sender.code}_to_{recipient.code}_{step}.bin").write_bytes(envelope)
        print(f"  {sender.label} → {recipient.label}  [{step}]  {len(envelope):>9,} bytes  {envelope[:12].hex()}…")
        return pqc_channel.unseal(recipient.keys.private, envelope, context)

    def raw_ids_on_wire(self) -> int:
        return sum(len(RAW_ID_PATTERN.findall(path.read_bytes())) for path in self.directory.glob("*.bin"))


def load_institutions() -> list[Institution]:
    a = pd.read_csv(A_PATH)
    b = pd.read_csv(B_PATH)

    # A generalizes before anything leaves: exact coordinates and event time stay behind
    police = Institution("A", "警政A", [person_id(m) for m in a["master_id"]], a[["county", "district"]])
    # B stores the same IDs in lower case with stray spaces; normalization has to absorb that
    lab = Institution("B", "檢驗B", [f" {person_id(m).lower()} " for m in b["master_id"]], b[["severity_score"]].astype(int))

    if C_PATH.exists():
        c = pd.read_csv(C_PATH)
        licensing = Institution("C", "監理C", [person_id(m) for m in c["master_id"]], c[["recidivism_count"]].astype(int))
    else:
        # C's table has not been delivered. Its ID list follows the agreed inclusion rule
        # (B positive, or prior offense in A); it contributes no columns until the real file exists.
        c_ids = set(b.loc[b["test_result"] == "陽性", "master_id"]) | set(a.loc[a["prior_offense_flag"] == 1, "master_id"])
        licensing = Institution("C", "監理C", [person_id(m) for m in sorted(c_ids)], pd.DataFrame(index=range(len(c_ids))))
        print("  注意：dataset_c_aligned.csv 尚未提供，監理C 先用規格書的納入規則產生 ID 清單，不提供任何欄位")
    return [police, lab, licensing]


def run_psi(institutions: list[Institution], coordinator: Party, wire: Wire) -> set[str]:
    for i, origin in enumerate(institutions):
        second, third = institutions[(i + 1) % 3], institutions[(i + 2) % 3]
        once = wire.send(origin, second, f"psi-{origin.code}-blind1", origin.first_blinding())
        twice = wire.send(second, third, f"psi-{origin.code}-blind2", second.reblind(once))
        thrice = wire.send(third, origin, f"psi-{origin.code}-blind3", third.reblind(twice))
        origin.receive_own_anon_ids(thrice)

    anon_sets = [
        set(json.loads(wire.send(institution, coordinator, "psi-anon-ids", institution.anon_id_list())))
        for institution in institutions
    ]
    return set.intersection(*anon_sets)


def exchange_fields(institutions: list[Institution], coordinator: Party, wire: Wire, intersection: set[str]) -> pd.DataFrame:
    notice = json.dumps(sorted(intersection)).encode("utf-8")
    table = None
    for institution in institutions:
        members = set(json.loads(wire.send(coordinator, institution, "intersection", notice)))
        rows = pd.read_csv(io.BytesIO(wire.send(institution, coordinator, "fields", institution.fields_for(members))))
        table = rows if table is None else table.merge(rows, on="anon_id")
    return table


def main():
    print("【準備】各機構產生自己的 ML-KEM-768 金鑰與 PSI 私鑰")
    institutions = load_institutions()
    coordinator = Party("K", "協調者")
    wire = Wire(WIRE_DIR)
    for institution in institutions:
        print(f"  {institution.label}：{len(institution):,} 筆身分識別碼")

    print("\n【階段一】PSI：三方輪流盲化，協調者比對三重盲化值")
    intersection = run_psi(institutions, coordinator, wire)
    print(f"  → 三方交集：{len(intersection):,} 人")

    print("\n【階段二】只傳交集中的人需要的欄位")
    table = exchange_fields(institutions, coordinator, wire, intersection)
    OUTPUT.parent.mkdir(exist_ok=True)
    table.to_csv(OUTPUT, index=False, encoding="utf-8-sig")
    print(f"  → 協調者合併出分析表：{len(table):,} 列，欄位 {list(table.columns)}")
    print(f"  → 已寫入 {OUTPUT.relative_to(DATA_DIR)}")

    print("\n【檢查】線路上的內容")
    print(f"  傳輸 {wire.count} 則訊息，共 {wire.total_bytes:,} bytes，全部是 ML-KEM-768 + AES-256-GCM 密文")
    print(f"  在所有密文中搜尋原始身分識別碼：找到 {wire.raw_ids_on_wire()} 個")


if __name__ == "__main__":
    main()
