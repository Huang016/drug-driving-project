"""Post-quantum encrypted channel between institutions: ML-KEM-768 + AES-256-GCM.

Every message is sealed for one recipient:
  1. ML-KEM-768 (FIPS 203) encapsulates a fresh shared secret under the
     recipient's public key.
  2. HKDF-SHA256 turns that secret into an AES-256-GCM key bound to the message
     context (who sends to whom, and for which step).
  3. AES-256-GCM encrypts the payload; the context is authenticated as well, so
     an envelope replayed under a different step fails to open.

What this does not give: proof of who the sender is. ML-KEM only keeps the
content secret. A deployment would add a post-quantum signature (ML-DSA) on top.
kyber-py is a pure-Python reference implementation, fine for a demonstration
but not hardened against timing side channels.
"""
import os
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from kyber_py.ml_kem import ML_KEM_768

KEM_CIPHERTEXT_BYTES = 1088
NONCE_BYTES = 12


@dataclass
class KeyPair:
    public: bytes   # encapsulation key, given to every other party
    private: bytes  # decapsulation key, never leaves the institution


def generate_keypair() -> KeyPair:
    public, private = ML_KEM_768.keygen()
    return KeyPair(public=public, private=private)


def _derive_key(shared_secret: bytes, context: str) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=context.encode("utf-8")).derive(shared_secret)


def seal(recipient_public: bytes, plaintext: bytes, context: str) -> bytes:
    shared_secret, kem_ciphertext = ML_KEM_768.encaps(recipient_public)
    nonce = os.urandom(NONCE_BYTES)
    ciphertext = AESGCM(_derive_key(shared_secret, context)).encrypt(nonce, plaintext, context.encode("utf-8"))
    return kem_ciphertext + nonce + ciphertext


def unseal(recipient_private: bytes, envelope: bytes, context: str) -> bytes:
    kem_ciphertext = envelope[:KEM_CIPHERTEXT_BYTES]
    nonce = envelope[KEM_CIPHERTEXT_BYTES:KEM_CIPHERTEXT_BYTES + NONCE_BYTES]
    ciphertext = envelope[KEM_CIPHERTEXT_BYTES + NONCE_BYTES:]
    shared_secret = ML_KEM_768.decaps(recipient_private, kem_ciphertext)
    return AESGCM(_derive_key(shared_secret, context)).decrypt(nonce, ciphertext, context.encode("utf-8"))
