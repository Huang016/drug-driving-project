"""Step 1: three-party ECDH-PSI (semi-honest, simulated as separate party objects).

Protocol, curve Ed25519 prime-order subgroup via libsodium:
  1. Each party P picks a secret scalar k_P and maps every local id x to a point H(x)
     (hash-to-curve with Elligator 2, domain-separated).
  2. Each party's blinded list travels a ring and every party raises it to its own key:
        A: H(a)^kA -> B -> ^kB -> C -> ^kC -> coordinator
        B: H(b)^kB -> C -> ^kC -> A -> ^kA -> coordinator
        C: H(c)^kC -> A -> ^kA -> B -> ^kB -> coordinator
     Scalar multiplication commutes, so the same person gives the same point H(x)^(kA kB kC)
     no matter whose list it came from. Parties forward lists in the order received,
     so the coordinator can answer each party with positions only.
  3. The coordinator (holds no key) intersects the three triple-blinded lists and tells
     each party which of its positions are in the intersection, plus a `psi_token`
     derived from the triple-blinded point, which is the only join key used downstream.

What each role learns (semi-honest, no collusion):
  - a party: the other parties' set sizes, and which of its own records are in A∩B∩C.
  - the coordinator: set sizes, |A∩B∩C|, and pairwise intersection sizes
    (|A∩B|, |A∩C|, |B∩C|). It never sees a raw id or a key.
  - if the coordinator colludes with a party, that party can test ids against the others,
    which is the usual limit of DH-PSI.
"""
import hashlib
import json
import secrets
from pathlib import Path

import pandas as pd
from nacl import bindings as nb

BASE = Path(__file__).resolve().parent
PARTIES = BASE / "parties"
DOMAIN = b"innoserve2026/drug-driving/psi/v1"
L = 2**252 + 27742317777372353535851937790883648493  # Ed25519 subgroup order


def hash_to_point(identifier: str) -> bytes:
    digest = hashlib.sha256(DOMAIN + b"|" + identifier.encode("utf-8")).digest()
    return nb.crypto_core_ed25519_from_uniform(digest)


class Party:
    def __init__(self, name: str):
        self.name = name
        self.records = pd.read_csv(PARTIES / name / "local_records.csv", dtype={"person_id": str})
        scalar = secrets.randbelow(L - 1) + 1
        self._key = scalar.to_bytes(32, "little")  # never leaves this object

    def blind_own(self) -> list[bytes]:
        return [nb.crypto_scalarmult_ed25519_noclamp(self._key, hash_to_point(x)) for x in self.records["person_id"]]

    def blind_forward(self, points: list[bytes]) -> list[bytes]:
        return [nb.crypto_scalarmult_ed25519_noclamp(self._key, p) for p in points]

    def receive_result(self, positions: list[int], tokens: list[str]):
        matched = self.records.iloc[positions].copy()
        matched.insert(0, "psi_token", tokens)
        # the party keeps token <-> person_id privately (to relink later); only tokens leave
        matched[["psi_token", "person_id"]].to_csv(PARTIES / self.name / "private_token_map.csv", index=False)
        matched = matched.drop(columns=["person_id"])
        matched.to_csv(PARTIES / self.name / "psi_matched.csv", index=False)
        return len(matched)


class Coordinator:
    """Receives only triple-blinded points; holds no key and no raw id."""

    def intersect(self, lists: dict[str, list[bytes]]):
        sets = {name: set(pts) for name, pts in lists.items()}
        common = set.intersection(*sets.values())
        result = {}
        for name, pts in lists.items():
            positions = [i for i, p in enumerate(pts) if p in common]
            tokens = [hashlib.sha256(b"token|" + pts[i]).hexdigest()[:20] for i in positions]
            result[name] = (positions, tokens)
        names = list(sets)
        transcript = {
            "set_sizes": {n: len(s) for n, s in sets.items()},
            "intersection_size": len(common),
            "pairwise_sizes_seen_by_coordinator": {
                f"{a}∩{b}": len(sets[a] & sets[b]) for i, a in enumerate(names) for b in names[i + 1:]
            },
        }
        return result, transcript


def main():
    A, B, C = Party("A"), Party("B"), Party("C")
    ring = {"A": [A, B, C], "B": [B, C, A], "C": [C, A, B]}

    triple = {}
    for owner, order in ring.items():
        pts = order[0].blind_own()
        for nxt in order[1:]:
            pts = nxt.blind_forward(pts)
        triple[owner] = pts

    result, transcript = Coordinator().intersect(triple)
    parties = {"A": A, "B": B, "C": C}
    for name, (positions, tokens) in result.items():
        n = parties[name].receive_result(positions, tokens)
        print(f"party {name}: {n} records in A∩B∩C -> parties/{name}/psi_matched.csv")

    (BASE / "logs").mkdir(exist_ok=True)
    (BASE / "logs" / "psi_transcript.json").write_text(json.dumps(transcript, ensure_ascii=False, indent=2))
    print(json.dumps(transcript, ensure_ascii=False))


if __name__ == "__main__":
    main()
