"""ECDH-style blinding for private set intersection.

An identifier is hashed to a point on the Ed25519 curve, and each institution
multiplies that point by its own secret number. Multiplication is commutative,
so once all three institutions have applied their secrets the same person ends
up at the same point no matter whose list they started in. Nobody holds all
three secrets, so nobody can undo the blinding or test a guessed identifier.
"""
import hashlib
import os

import nacl.bindings as sodium

POINT_BYTES = 32
HASH_DOMAIN = b"drug-driving-psi-v1:"


def normalize_id(raw: str) -> str:
    # the same person must hash to the same point however each institution typed the ID
    return "".join(str(raw).split()).upper()


def hash_to_point(identifier: str) -> bytes:
    digest = hashlib.sha256(HASH_DOMAIN + normalize_id(identifier).encode("utf-8")).digest()
    return sodium.crypto_core_ed25519_from_uniform(digest)


def new_secret() -> bytes:
    while True:
        secret = sodium.crypto_core_ed25519_scalar_reduce(os.urandom(64))
        if any(secret):
            return secret


def blind(points: list[bytes], secret: bytes) -> list[bytes]:
    return [sodium.crypto_scalarmult_ed25519_noclamp(secret, point) for point in points]


def pack_points(points: list[bytes]) -> bytes:
    return b"".join(points)


def unpack_points(data: bytes) -> list[bytes]:
    return [data[i:i + POINT_BYTES] for i in range(0, len(data), POINT_BYTES)]
