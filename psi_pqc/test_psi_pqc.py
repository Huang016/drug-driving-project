"""Checks for the PSI and post-quantum channel modules. Run: python test_psi_pqc.py

The PSI check compares the protocol's answer with a plain intersection of the
identifiers. Only a test may do that: it reads all three institutions' private
ID lists at once, which no party in the real protocol can.
"""
import pandas as pd
from cryptography.exceptions import InvalidTag

import pqc_channel
import psi
import run_psi
from institution import INSTITUTION_CODES, load_institution


def test_channel_roundtrip_and_rejections():
    alice, bob, mallory = (pqc_channel.generate_identity() for _ in range(3))
    message = pqc_channel.seal(alice, bob.public, "鄉鎮資料".encode("utf-8"), "A->K|fields")
    assert pqc_channel.unseal(bob, alice.public, message, "A->K|fields").decode("utf-8") == "鄉鎮資料"

    tampered = message[:-1] + bytes([message[-1] ^ 1])
    forged = pqc_channel.seal(mallory, bob.public, b"fake", "A->K|fields")
    for label, recipient, sender, data, context, error in [
        ("tampered ciphertext", bob, alice.public, tampered, "A->K|fields", pqc_channel.InvalidSignature),
        ("replayed under another step", bob, alice.public, message, "A->K|intersection", pqc_channel.InvalidSignature),
        ("sent by someone else", bob, alice.public, forged, "A->K|fields", pqc_channel.InvalidSignature),
        ("opened with the wrong key", mallory, alice.public, message, "A->K|fields", InvalidTag),
    ]:
        try:
            pqc_channel.unseal(recipient, sender, data, context)
        except error:
            continue
        raise AssertionError(f"{label} was accepted")


def test_normalization():
    assert psi.hash_to_point("SIM0000031") == psi.hash_to_point(" sim0000031 ")
    assert psi.hash_to_point("SIM0000031") != psi.hash_to_point("SIM0000032")


def test_blinding_commutes():
    point = psi.hash_to_point("SIM0000031")
    a, b, c = psi.new_secret(), psi.new_secret(), psi.new_secret()
    assert psi.blind(psi.blind(psi.blind([point], a), b), c) == psi.blind(psi.blind(psi.blind([point], c), a), b)


def plain_intersection_table(institutions) -> pd.DataFrame:
    """The table the coordinator should end up with, minus the anonymous IDs."""
    tables = []
    for institution in institutions:
        table = institution._fields.copy()
        table.insert(0, "id", [psi.normalize_id(i) for i in institution._ids])
        tables.append(table)
    merged = tables[0].merge(tables[1], on="id").merge(tables[2], on="id").drop(columns="id")
    return merged.sort_values(list(merged.columns)).reset_index(drop=True)


def same_content(table: pd.DataFrame, expected: pd.DataFrame) -> bool:
    got = table.drop(columns="anon_id")
    got = got.sort_values(list(got.columns)).reset_index(drop=True)
    return got.astype(str).equals(expected.astype(str))


def test_psi_matches_plain_intersection(tmp_dir):
    institutions = [load_institution(code) for code in INSTITUTION_CODES]
    expected = plain_intersection_table(institutions)
    wire = run_psi.Wire(tmp_dir, {code: pqc_channel.generate_identity() for code in [*INSTITUTION_CODES, "K"]})
    intersection = run_psi.run_psi(institutions, wire)
    assert len(intersection) == len(expected), (len(intersection), len(expected))

    table = run_psi.exchange_fields(institutions, wire, intersection)
    assert table["anon_id"].is_unique and same_content(table, expected)
    assert wire.raw_ids_on_wire() == 0
    return len(expected)


def test_networked_result():
    """After run_nodes_local.sh: the coordinator's table must hold exactly the plain intersection."""
    institutions = [load_institution(code) for code in INSTITUTION_CODES]
    expected = plain_intersection_table(institutions)
    table = pd.read_csv(run_psi.OUTPUT)
    assert table["anon_id"].is_unique and same_content(table, expected), "networked run produced a different table"
    received = list((run_psi.BASE_DIR / "received").glob("*/*.bin"))
    assert received and not any(run_psi.RAW_ID_PATTERN.search(path.read_bytes()) for path in received)
    return len(table), len(received)


if __name__ == "__main__":
    import sys

    if "--networked" in sys.argv:
        rows, messages = test_networked_result()
        print(f"networked: coordinator's table equals the plain intersection ({rows} people); {messages} received messages, no raw ID in any")
        sys.exit()
    test_channel_roundtrip_and_rejections()
    print("channel: roundtrip ok; tampered, replayed, forged-sender and wrong-key messages all rejected")
    test_normalization()
    test_blinding_commutes()
    print("psi: normalization and commutative blinding ok")
    n = test_psi_matches_plain_intersection(run_psi.BASE_DIR / "wire_test")
    print(f"psi: coordinator's table equals the plain intersection ({n} people); no raw ID on the wire")
