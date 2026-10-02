"""Post-quantum channel between institutions: ML-KEM-768 + AES-256-GCM, signed with ML-DSA-65.

Every message is sealed for one recipient and signed by its sender:
  1. ML-KEM-768 (FIPS 203) encapsulates a fresh shared secret under the
     recipient's public key.
  2. HKDF-SHA256 turns that secret into an AES-256-GCM key bound to the message
     context (who sends to whom, and for which step).
  3. AES-256-GCM encrypts the payload; the context is authenticated as well, so
     an envelope replayed under a different step fails to open.
  4. ML-DSA-65 (FIPS 204) signs the context and the envelope, so the recipient
     knows which party sent it before decrypting anything.

Public keys have to reach the other parties by a route an attacker cannot
tamper with (handed over in person, or a repository all parties trust).
kyber-py and dilithium-py are pure-Python reference implementations, fine for a
demonstration but not hardened against timing side channels.
"""
import json
import os
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from dilithium_py.ml_dsa import ML_DSA_65
from kyber_py.ml_kem import ML_KEM_768

KEM_CIPHERTEXT_BYTES = 1088
NONCE_BYTES = 12
SIGNATURE_BYTES = 3309


class InvalidSignature(Exception):
    """The message was not signed by the party it claims to come from."""


@dataclass
class PublicKeys:
    """What a party hands to everyone else."""
    kem: bytes   # others encrypt to this
    sign: bytes  # others check this party's signatures with this

    def save(self, path: Path) -> None:
        path.write_text(json.dumps({"kem": self.kem.hex(), "sign": self.sign.hex()}), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "PublicKeys":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(kem=bytes.fromhex(data["kem"]), sign=bytes.fromhex(data["sign"]))


@dataclass
class Identity:
    """A party's full key material. The private halves never leave the party."""
    public: PublicKeys
    kem_private: bytes
    sign_private: bytes

    def save_private(self, path: Path) -> None:
        path.write_text(json.dumps({"kem": self.kem_private.hex(), "sign": self.sign_private.hex()}), encoding="utf-8")

    @classmethod
    def load(cls, public_path: Path, private_path: Path) -> "Identity":
        private = json.loads(private_path.read_text(encoding="utf-8"))
        return cls(
            public=PublicKeys.load(public_path),
            kem_private=bytes.fromhex(private["kem"]),
            sign_private=bytes.fromhex(private["sign"]),
        )


def generate_identity() -> Identity:
    kem_public, kem_private = ML_KEM_768.keygen()
    sign_public, sign_private = ML_DSA_65.keygen()
    return Identity(public=PublicKeys(kem=kem_public, sign=sign_public), kem_private=kem_private, sign_private=sign_private)


def _derive_key(shared_secret: bytes, context: str) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=context.encode("utf-8")).derive(shared_secret)


def seal(sender: Identity, recipient: PublicKeys, plaintext: bytes, context: str) -> bytes:
    shared_secret, kem_ciphertext = ML_KEM_768.encaps(recipient.kem)
    nonce = os.urandom(NONCE_BYTES)
    ciphertext = AESGCM(_derive_key(shared_secret, context)).encrypt(nonce, plaintext, context.encode("utf-8"))
    envelope = kem_ciphertext + nonce + ciphertext
    signature = ML_DSA_65.sign(sender.sign_private, context.encode("utf-8") + envelope)
    return signature + envelope


def unseal(recipient: Identity, sender: PublicKeys, message: bytes, context: str) -> bytes:
    signature, envelope = message[:SIGNATURE_BYTES], message[SIGNATURE_BYTES:]
    if not ML_DSA_65.verify(sender.sign, context.encode("utf-8") + envelope, signature):
        raise InvalidSignature(context)
    kem_ciphertext = envelope[:KEM_CIPHERTEXT_BYTES]
    nonce = envelope[KEM_CIPHERTEXT_BYTES:KEM_CIPHERTEXT_BYTES + NONCE_BYTES]
    ciphertext = envelope[KEM_CIPHERTEXT_BYTES + NONCE_BYTES:]
    shared_secret = ML_KEM_768.decaps(recipient.kem_private, kem_ciphertext)
    return AESGCM(_derive_key(shared_secret, context)).decrypt(nonce, ciphertext, context.encode("utf-8"))
