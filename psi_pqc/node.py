"""One party of the PSI protocol as its own program, talking to the others over TCP.

    python node.py A        警政
    python node.py B        檢驗
    python node.py C        監理
    python node.py K        協調者

Start all four, in any order, in four terminals on one machine or on separate
machines. Where each party listens is set in network.json; with every host set
to 127.0.0.1 everything stays on one machine.

Keys. The first start creates the party's own keys:
    keys/private/<party>.json   stays on this machine, never shared
    keys/public/<party>.json    give this file to every other party
On separate machines, run `python node.py <party> --init` on each one first,
collect the four public files into everyone's keys/public/, then start the nodes.

Each node loads only its own data file and only its own private keys. Whatever
arrives over the network is kept in received/<party>/ exactly as it arrived.
"""
import io
import json
import queue
import shutil
import socket
import sys
import threading
import time
from pathlib import Path

import pandas as pd

import pqc_channel
from institution import DATA_DIR, INSTITUTION_CODES, LABELS, load_institution, merge_fields

BASE_DIR = Path(__file__).resolve().parent
NETWORK_PATH = BASE_DIR / "network.json"
PUBLIC_DIR = BASE_DIR / "keys" / "public"
PRIVATE_DIR = BASE_DIR / "keys" / "private"
OUTPUT = DATA_DIR / "hotspot_pipeline" / "analysis_table.csv"
WAIT_SECONDS = 600


def own_identity(code: str) -> pqc_channel.Identity:
    public_path, private_path = PUBLIC_DIR / f"{code}.json", PRIVATE_DIR / f"{code}.json"
    if private_path.exists() and public_path.exists():
        return pqc_channel.Identity.load(public_path, private_path)
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    identity = pqc_channel.generate_identity()
    identity.save_private(private_path)
    identity.public.save(public_path)
    print(f"  已產生 {LABELS[code]} 的 ML-KEM-768 與 ML-DSA-65 金鑰（私鑰留在本機 keys/private/）")
    return identity


def peer_public_keys(code: str, peers: list[str]) -> dict[str, pqc_channel.PublicKeys]:
    deadline = time.time() + WAIT_SECONDS
    missing = [p for p in peers if not (PUBLIC_DIR / f"{p}.json").exists()]
    if missing:
        print(f"  等待其他方的公鑰：{'、'.join(LABELS[p] for p in missing)}")
    while any(not (PUBLIC_DIR / f"{p}.json").exists() for p in peers):
        if time.time() > deadline:
            raise TimeoutError("等不到其他方的公鑰，請確認 keys/public/ 裡有四方的檔案")
        time.sleep(0.5)
    time.sleep(0.2)  # a file that has just appeared may still be being written
    return {p: pqc_channel.PublicKeys.load(PUBLIC_DIR / f"{p}.json") for p in peers}


class Node:
    def __init__(self, code: str):
        self.code = code
        self.label = LABELS[code]
        self.network = json.loads(NETWORK_PATH.read_text(encoding="utf-8"))
        self.peers = [p for p in self.network if p != code]
        self.identity = own_identity(code)
        self.inbox = queue.Queue()
        self.received_dir = BASE_DIR / "received" / code
        shutil.rmtree(self.received_dir, ignore_errors=True)
        self.received_dir.mkdir(parents=True)
        self.received_count = 0

        address = self.network[code]
        self.server = socket.create_server((address["host"], address["port"]))
        threading.Thread(target=self._accept_loop, daemon=True).start()
        print(f"  {self.label} 在 {address['host']}:{address['port']} 等待連線")
        self.public_keys = peer_public_keys(code, self.peers)

    def _accept_loop(self) -> None:
        while True:
            connection, _ = self.server.accept()
            with connection, connection.makefile("rb") as stream:
                header = json.loads(stream.readline())
                self.inbox.put((header["sender"], header["step"], stream.read(header["size"])))

    def send(self, recipient: str, step: str, payload: bytes) -> None:
        message = pqc_channel.seal(self.identity, self.public_keys[recipient], payload, f"{self.code}->{recipient}|{step}")
        header = json.dumps({"sender": self.code, "step": step, "size": len(message)}).encode("utf-8") + b"\n"
        address = self.network[recipient]
        deadline = time.time() + WAIT_SECONDS
        while True:
            try:
                with socket.create_connection((address["host"], address["port"]), timeout=10) as connection:
                    connection.sendall(header + message)
                break
            except OSError:
                # the other party may not have started yet
                if time.time() > deadline:
                    raise
                time.sleep(1)
        print(f"  送出 → {LABELS[recipient]}  [{step}]  {len(message):>9,} bytes  {message[-12:].hex()}…")

    def receive(self) -> tuple[str, str, bytes]:
        sender, step, message = self.inbox.get(timeout=WAIT_SECONDS)
        self.received_count += 1
        (self.received_dir / f"{self.received_count:02d}_from_{sender}_{step}.bin").write_bytes(message)
        # the signature proves who sent it; a wrong or forged sender stops here
        payload = pqc_channel.unseal(self.identity, self.public_keys[sender], message, f"{sender}->{self.code}|{step}")
        print(f"  收到 ← {LABELS[sender]}  [{step}]  {len(message):>9,} bytes  簽章驗證通過")
        return sender, step, payload


def run_institution(node: Node) -> None:
    institution = load_institution(node.code)
    ring = INSTITUTION_CODES
    previous_party = ring[(ring.index(node.code) - 1) % 3]
    next_party = ring[(ring.index(node.code) + 1) % 3]
    print(f"  {node.label} 持有 {len(institution):,} 筆身分識別碼")

    print("\n【階段一】PSI：盲化自己的清單，並替另外兩方的清單再盲化一次")
    node.send(next_party, f"psi-{node.code}-blind1", institution.first_blinding())
    while True:
        sender, step, payload = node.receive()
        if step == "intersection":
            if sender != "K":
                raise ValueError(f"交集名單只能來自協調者，卻來自 {sender}")
            print("\n【階段二】只送交集中的人的必要欄位")
            node.send("K", "fields", institution.fields_for(set(json.loads(payload))))
            print(f"\n  {node.label} 完成。原始識別碼、PSI 私鑰與金鑰私鑰都沒有離開本機。")
            return
        if sender != previous_party:
            raise ValueError(f"{step} 應該來自 {previous_party}，卻來自 {sender}")
        origin, round_name = step.split("-")[1], step.split("-")[2]
        if round_name == "blind3":
            institution.receive_own_anon_ids(payload)
            node.send("K", "psi-anon-ids", institution.anon_id_list())
        else:
            next_round = {"blind1": "blind2", "blind2": "blind3"}[round_name]
            node.send(next_party, f"psi-{origin}-{next_round}", institution.reblind(payload))


def run_coordinator(node: Node) -> None:
    print("\n【階段一】等三個機構各自送來三重盲化後的匿名 ID")
    anon_sets = {}
    while len(anon_sets) < 3:
        sender, step, payload = node.receive()
        if step != "psi-anon-ids":
            raise ValueError(f"協調者此時不應收到 {step}")
        anon_sets[sender] = set(json.loads(payload))
    intersection = set.intersection(*anon_sets.values())
    print(f"  → 三方交集：{len(intersection):,} 人")

    print("\n【階段二】通知各機構交集名單，收回必要欄位")
    notice = json.dumps(sorted(intersection)).encode("utf-8")
    for code in INSTITUTION_CODES:
        node.send(code, "intersection", notice)
    tables = {}
    while len(tables) < 3:
        sender, step, payload = node.receive()
        if step != "fields":
            raise ValueError(f"協調者此時不應收到 {step}")
        tables[sender] = pd.read_csv(io.BytesIO(payload))

    table = merge_fields([tables[code] for code in INSTITUTION_CODES])
    OUTPUT.parent.mkdir(exist_ok=True)
    table.to_csv(OUTPUT, index=False, encoding="utf-8-sig")
    print(f"  → 合併出分析表：{len(table):,} 列，欄位 {list(table.columns)}")
    print(f"  → 已寫入 {OUTPUT.relative_to(DATA_DIR)}")
    print("\n  協調者完成。全程沒有收到任何原始識別碼，只有匿名 ID 與必要欄位。")


def main():
    code = sys.argv[1].upper()
    if code not in LABELS:
        raise SystemExit("用法：python node.py <A|B|C|K> [--init]")
    print(f"【{LABELS[code]}】")
    if "--init" in sys.argv:
        own_identity(code)
        print(f"  把 keys/public/{code}.json 交給其他三方")
        return
    node = Node(code)
    if code == "K":
        run_coordinator(node)
    else:
        run_institution(node)
    time.sleep(0.5)  # let the last message finish leaving


if __name__ == "__main__":
    main()
