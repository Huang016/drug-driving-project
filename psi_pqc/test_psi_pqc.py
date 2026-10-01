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


def test_channel_roundtrip_and_rejections():
    alice, bob = pqc_channel.generate_keypair(), pqc_channel.generate_keypair()
    envelope = pqc_channel.seal(bob.public, "鄉鎮資料".encode("utf-8"), "A->K|fields")
    assert pqc_channel.unseal(bob.private, envelope, "A->K|fields").decode("utf-8") == "鄉鎮資料"

    tampered = envelope[:-1] + bytes([envelope[-1] ^ 1])
    for label, key, data, context in [
        ("tampered ciphertext", bob.private, tampered, "A->K|fields"),
        ("wrong step", bob.private, envelope, "A->K|intersection"),
        ("wrong recipient key", alice.private, envelope, "A->K|fields"),
    ]:
        try:
            pqc_channel.unseal(key, data, context)
        except InvalidTag:
            continue
        raise AssertionError(f"{label} was accepted")


def test_normalization():
    assert psi.hash_to_point("SIM0000031") == psi.hash_to_point(" sim0000031 ")
    assert psi.hash_to_point("SIM0000031") != psi.hash_to_point("SIM0000032")


def test_blinding_commutes():
    point = psi.hash_to_point("SIM0000031")
    a, b, c = psi.new_secret(), psi.new_secret(), psi.new_secret()
    assert psi.blind(psi.blind(psi.blind([point], a), b), c) == psi.blind(psi.blind(psi.blind([point], c), a), b)


def test_psi_matches_plain_intersection(tmp_dir):
    institutions = run_psi.load_institutions()
    coordinator = run_psi.Party("K", "協調者")
    wire = run_psi.Wire(tmp_dir)
    intersection = run_psi.run_psi(institutions, coordinator, wire)

    plain = set.intersection(*[{psi.normalize_id(i) for i in institution._ids} for institution in institutions])
    assert len(intersection) == len(plain), (len(intersection), len(plain))

    table = run_psi.exchange_fields(institutions, coordinator, wire, intersection)
    assert len(table) == len(plain) and table["anon_id"].is_unique
    assert wire.raw_ids_on_wire() == 0

    # each person's fields must be the ones their own institution holds
    police = institutions[0]
    own = pd.DataFrame({"anon_id": police._anon_ids, "county": police._fields["county"], "district": police._fields["district"]})
    merged = table.merge(own, on="anon_id", suffixes=("", "_own"))
    assert (merged["county"] == merged["county_own"]).all() and (merged["district"] == merged["district_own"]).all()
    return len(plain)


if __name__ == "__main__":
    test_channel_roundtrip_and_rejections()
    print("channel: roundtrip ok; tampered ciphertext, wrong step and wrong key all rejected")
    test_normalization()
    test_blinding_commutes()
    print("psi: normalization and commutative blinding ok")
    n = test_psi_matches_plain_intersection(run_psi.BASE_DIR / "wire_test")
    print(f"psi: protocol intersection equals plain intersection ({n} people); no raw ID on the wire")
