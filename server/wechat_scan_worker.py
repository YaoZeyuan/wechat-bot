"""Run the Windows scanner in an isolated process after user confirmation."""

import ctypes
import json
import re
import sys
from collections import Counter
from pathlib import Path

from cryptography.hazmat.primitives.serialization import load_der_private_key

# Compatibility material used by the source fork's challenge-response ABI.
SIGNER = bytes.fromhex(
    "6a74585b5a6a5f5c59713f2a5e785e7a168e0e9425838c437f0b1274d114f59457f436c936b80178da1848856b58eef3"
)


def image_template(root: Path):
    suffixes = Counter()
    block = None
    for index, path in enumerate(root.rglob("*_t.dat")):
        if index >= 32:
            break
        try:
            with path.open("rb") as stream:
                header = stream.read(31)
                if len(header) < 2:
                    continue
                stream.seek(-2, 2)
                x, y = stream.read(2)
        except OSError:
            continue
        if x ^ 0xFF == y ^ 0xD9:
            suffixes[x ^ 0xFF] += 1
        if len(header) == 31 and header.startswith(b"\x07\x08V2\x08\x07"):
            block = block or header[15:31]
    if not suffixes:
        return None, block
    return suffixes.most_common(1)[0][0], block


def scan(dll_path: str, mode: str, root: Path) -> dict:
    if mode == "image":
        xor, block = image_template(root)
        if xor is None:
            return {"error": "image_template_missing"}
        if block is None:
            return {"image_xor_key": f"0x{xor:02x}", "image_aes_key": ""}
    dll = ctypes.CDLL(dll_path)
    challenge = dll.wkt_challenge
    challenge.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    challenge.restype = ctypes.c_int
    nonce = ctypes.create_string_buffer(32)
    if challenge(nonce, len(nonce)) != 32:
        return {"error": "challenge_failed"}
    signer = load_der_private_key(bytes(value ^ 0x5A for value in SIGNER), password=None)
    signature = signer.sign(nonce.raw)
    if mode == "database":
        fn = dll.wkt_scan_account_auth
        args = (signature, len(signature))
    else:
        fn = dll.wkt_scan_image_key_auth
        args = (signature, len(signature), block, len(block))
    fn.argtypes = [ctypes.c_char_p, ctypes.c_size_t] * (len(args) // 2)
    fn.restype = ctypes.c_void_p
    free = dll.wkt_free
    free.argtypes = [ctypes.c_void_p]
    free.restype = None
    result = fn(*args)
    if not result:
        return {"error": "no_key"}
    try:
        value = ctypes.string_at(result).decode("utf-8").strip()
    finally:
        free(result)
    if mode == "image":
        if len(value) < 16:
            return {"error": "no_key"}
        return {"image_xor_key": f"0x{xor:02x}", "image_aes_key": value[:16]}
    account = json.loads(value)
    key = account.get("db_key") or ""
    if not re.fullmatch(r"[0-9a-fA-F]{64}", key):
        return {"error": "no_key"}
    return {"key": key, "account": account.get("wxid") or ""}


if __name__ == "__main__":
    try:
        result = scan(sys.argv[1], sys.argv[2], Path(sys.argv[3]))
    except (OSError, ValueError, AttributeError):
        result = {"error": "scanner_failed"}
    print(json.dumps(result), flush=True)
