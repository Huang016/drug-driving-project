"""Stage 1 + 2 in one process: three-party private set intersection over the
post-quantum channel, then the minimal field exchange that gives the
coordinator its analysis table.

This is the quick version that plays all four roles in one program. node.py
runs the same protocol with each role as its own program talking over the
network, on one machine or several.

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

Everything that crosses between parties is sealed with ML-KEM-768 + AES-256-GCM,
signed with ML-DSA-65, and written to wire/, which is exactly what an
eavesdropper would see.

Known limits of this design (semi-honest parties assumed): the coordinator can
also see how many people any two institutions share, and each institution
learns which of its own people are in the intersection.
"""
import io
import json
import re
import shutil
from pathlib import Path

import pandas as pd

import pqc_channel
from institution import DATA_DIR, INSTITUTION_CODES, LABELS, Institution, load_institution, merge_fields

BASE_DIR = Path(__file__).resolve().parent
WIRE_DIR = BASE_DIR / "wire"
OUTPUT = DATA_DIR / "hotspot_pipeline" / "analysis_table.csv"
RAW_ID_PATTERN = re.compile(rb"(?i)sim\d{7}")


class Wire:
    """Carries sealed messages between parties and keeps a copy of what was on the wire."""

    def __init__(self, directory: Path, identities: dict[str, pqc_channel.Identity]):
        self.directory = directory
        self.identities = identities
        shutil.rmtree(directory, ignore_errors=True)
        directory.mkdir(parents=True)
        self.count = 0
        self.total_bytes = 0

    def send(self, sender: str, recipient: str, step: str, payload: bytes) -> bytes:
        context = f"{sender}->{recipient}|{step}"
        message = pqc_channel.seal(self.identities[sender], self.identities[recipient].public, payload, context)
        self.count += 1
        self.total_bytes += len(message)
        (self.directory / f"{self.count:02d}_{sender}_to_{recipient}_{step}.bin").write_bytes(message)
        print(f"  {LABELS[sender]} → {LABELS[recipient]}  [{step}]  {len(message):>9,} bytes  {message[-12:].hex()}…")
        return pqc_channel.unseal(self.identities[recipient], self.identities[sender].public, message, context)

    def raw_ids_on_wire(self) -> int:
        return sum(len(RAW_ID_PATTERN.findall(path.read_bytes())) for path in self.directory.glob("*.bin"))


def run_psi(institutions: list[Institution], wire: Wire) -> set[str]:
    for i, origin in enumerate(institutions):
        second, third = institutions[(i + 1) % 3], institutions[(i + 2) % 3]
        once = wire.send(origin.code, second.code, f"psi-{origin.code}-blind1", origin.first_blinding())
        twice = wire.send(second.code, third.code, f"psi-{origin.code}-blind2", second.reblind(once))
        thrice = wire.send(third.code, origin.code, f"psi-{origin.code}-blind3", third.reblind(twice))
        origin.receive_own_anon_ids(thrice)

    anon_sets = [
        set(json.loads(wire.send(institution.code, "K", "psi-anon-ids", institution.anon_id_list())))
        for institution in institutions
    ]
    return set.intersection(*anon_sets)


def exchange_fields(institutions: list[Institution], wire: Wire, intersection: set[str]) -> pd.DataFrame:
    notice = json.dumps(sorted(intersection)).encode("utf-8")
    tables = []
    for institution in institutions:
        members = set(json.loads(wire.send("K", institution.code, "intersection", notice)))
        tables.append(pd.read_csv(io.BytesIO(wire.send(institution.code, "K", "fields", institution.fields_for(members)))))
    return merge_fields(tables)


def main():
    print("【準備】各方產生自己的 ML-KEM-768 金鑰、ML-DSA-65 簽章金鑰與 PSI 私鑰")
    institutions = [load_institution(code) for code in INSTITUTION_CODES]
    wire = Wire(WIRE_DIR, {code: pqc_channel.generate_identity() for code in [*INSTITUTION_CODES, "K"]})
    for institution in institutions:
        print(f"  {institution.label}：{len(institution):,} 筆身分識別碼")

    print("\n【階段一】PSI：三方輪流盲化，協調者比對三重盲化值")
    intersection = run_psi(institutions, wire)
    print(f"  → 三方交集：{len(intersection):,} 人")

    print("\n【階段二】只傳交集中的人需要的欄位")
    table = exchange_fields(institutions, wire, intersection)
    OUTPUT.parent.mkdir(exist_ok=True)
    table.to_csv(OUTPUT, index=False, encoding="utf-8-sig")
    print(f"  → 協調者合併出分析表：{len(table):,} 列，欄位 {list(table.columns)}")
    print(f"  → 已寫入 {OUTPUT.relative_to(DATA_DIR)}")

    print("\n【檢查】線路上的內容")
    print(f"  傳輸 {wire.count} 則訊息，共 {wire.total_bytes:,} bytes，全部是 ML-KEM-768 + AES-256-GCM 密文，並附 ML-DSA-65 簽章")
    print(f"  在所有密文中搜尋原始身分識別碼：找到 {wire.raw_ids_on_wire()} 個")


if __name__ == "__main__":
    main()
