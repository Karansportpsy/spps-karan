"""Create a VAPID key pair for Web Push. Run once, keep the private key secret.

    python tools/generate_vapid_keys.py

Prints SPPS_VAPID_PUBLIC_KEY (share freely; the browser needs it) and SPPS_VAPID_PRIVATE_KEY (set on Render only,
never commit it). Needs `cryptography`, which the project already depends on.
"""
from __future__ import annotations

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def main() -> int:
    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    private = key.private_numbers().private_value.to_bytes(32, "big")
    print("SPPS_VAPID_PUBLIC_KEY=" + b64(public))
    print("SPPS_VAPID_PRIVATE_KEY=" + b64(private))
    print("SPPS_VAPID_SUBJECT=mailto:<a monitored contact address>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
