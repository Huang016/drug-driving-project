"""Step 2: each party sends its PSI-matched rows to the trusted third party (TTP)
over a post-quantum hybrid channel.

  key agreement : ML-KEM-768 (FIPS 203)  +  X25519   -> HKDF-SHA256 -> 256-bit key
  encryption    : AES-256-GCM
  origin/integrity: ML-DSA-65 (FIPS 204) signature by the sending party

Hybrid means an attacker has to break both ML-KEM and X25519 to read the data,
so it stays safe even if one of the two turns out to be weak.
Key distribution (who trusts which public key) is simulated with local files;
in production this is the institutions' PKI.
"""
import base64
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from dilithium_py.ml_dsa import ML_DSA_65
from kyber_py.ml_kem import ML_KEM_768

BASE = Path(__file__).resolve().parent
PARTIES = BASE / "parties"
TTP = BASE / "ttp"
TRANSIT = BASE / "transit"
INFO = b"innoserve2026/drug-driving/pqc-transfer/v1"

b64 = lambda b: base64.b64encode(b).decode()
unb64 = base64.b64decode


def raw(pub: X25519PublicKey) -> bytes:
    return pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def derive_key(kem_secret: bytes, ecdh_secret: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=INFO).derive(kem_secret + ecdh_secret)


class TrustedThirdParty:
    def __init__(self):
        self.kem_ek, self._kem_dk = ML_KEM_768.keygen()
        self._x_sk = X25519PrivateKey.generate()
        self.x_pk = self._x_sk.public_key()
        self.trusted_signers = {}

    def receive(self, package: dict) -> bytes:
        sender = package["sender"]
        body = json.dumps(package["body"], sort_keys=True).encode()
        if not ML_DSA_65.verify(self.trusted_signers[sender], body, unb64(package["signature"])):
            raise ValueError(f"signature check failed for party {sender}")
        b = package["body"]
        kem_secret = ML_KEM_768.decaps(self._kem_dk, unb64(b["kem_ciphertext"]))
        ecdh_secret = self._x_sk.exchange(X25519PublicKey.from_public_bytes(unb64(b["x25519_ephemeral"])))
        key = derive_key(kem_secret, ecdh_secret)
        return AESGCM(key).decrypt(unb64(b["nonce"]), unb64(b["ciphertext"]), sender.encode())


class SendingParty:
    def __init__(self, name: str):
        self.name = name
        self.sign_pk, self._sign_sk = ML_DSA_65.keygen()

    def send(self, plaintext: bytes, ttp_kem_ek: bytes, ttp_x_pk: X25519PublicKey) -> dict:
        kem_secret, kem_ct = ML_KEM_768.encaps(ttp_kem_ek)
        eph = X25519PrivateKey.generate()
        key = derive_key(kem_secret, eph.exchange(ttp_x_pk))
        nonce = os.urandom(12)
        body = {
            "kem_ciphertext": b64(kem_ct),
            "x25519_ephemeral": b64(raw(eph.public_key())),
            "nonce": b64(nonce),
            "ciphertext": b64(AESGCM(key).encrypt(nonce, plaintext, self.name.encode())),
        }
        signature = ML_DSA_65.sign(self._sign_sk, json.dumps(body, sort_keys=True).encode())
        return {"sender": self.name, "scheme": "ML-KEM-768+X25519/HKDF-SHA256/AES-256-GCM, ML-DSA-65",
                "body": body, "signature": b64(signature)}


def main():
    ttp = TrustedThirdParty()
    (TTP / "inbox").mkdir(parents=True, exist_ok=True)
    TRANSIT.mkdir(exist_ok=True)

    for name in "ABC":
        party = SendingParty(name)
        ttp.trusted_signers[name] = party.sign_pk  # simulated PKI registration
        plaintext = (PARTIES / name / "psi_matched.csv").read_bytes()
        package = party.send(plaintext, ttp.kem_ek, ttp.x_pk)
        (TRANSIT / f"{name}_to_ttp.json").write_text(json.dumps(package))

        received = ttp.receive(json.loads((TRANSIT / f"{name}_to_ttp.json").read_text()))
        assert received == plaintext
        (TTP / "inbox" / f"{name}.csv").write_bytes(received)
        print(f"party {name}: {len(plaintext):,} bytes encrypted, signature verified, decrypted at TTP")

    # tamper test: flipping one ciphertext byte must be rejected
    pkg = json.loads((TRANSIT / "A_to_ttp.json").read_text())
    ct = bytearray(unb64(pkg["body"]["ciphertext"]))
    ct[0] ^= 1
    pkg["body"]["ciphertext"] = b64(bytes(ct))
    try:
        ttp.receive(pkg)
        print("tamper test: NOT detected")
    except ValueError:
        print("tamper test: modified package rejected (ML-DSA signature)")


if __name__ == "__main__":
    main()
