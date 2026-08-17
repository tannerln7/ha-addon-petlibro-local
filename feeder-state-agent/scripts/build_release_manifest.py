#!/usr/bin/env python3
"""Build and sign a deterministic state-agent release manifest.

Publication order: publish the immutable artifact URL first, then publish
latest.json and latest.json.sig for that artifact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib import parse

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


def _validate_semver(value: str, field_name: str) -> str:
    match = SEMVER_RE.fullmatch(value)
    if match is None:
        raise ValueError(f"{field_name} must be SemVer")
    prerelease = match.group(4)
    if prerelease and any(
        identifier.isdigit()
        and len(identifier) > 1
        and identifier.startswith("0")
        for identifier in prerelease.split(".")
    ):
        raise ValueError(
            f"{field_name} numeric prerelease identifiers must not have leading zeros"
        )
    return value


def _https_url(value: str, field_name: str, *, immutable: bool = False) -> str:
    parsed = parse.urlsplit(value)
    if parsed.scheme != "https":
        raise ValueError(f"{field_name} must be an HTTPS URL")
    if not parsed.hostname:
        raise ValueError(f"{field_name} must include a hostname")
    if parsed.username or parsed.password:
        raise ValueError(f"{field_name} must not include credentials")
    if parsed.fragment:
        raise ValueError(f"{field_name} must not include a URL fragment")
    if immutable and parsed.query:
        raise ValueError(f"{field_name} must not include query parameters")
    if immutable and parsed.path in {"", "/"}:
        raise ValueError(f"{field_name} must include a concrete artifact path")
    return value


def _load_public_key_hex(path: Path) -> bytes:
    normalized = "".join(path.read_text(encoding="utf-8").split())
    if len(normalized) != 64 or not re.fullmatch(r"[0-9a-fA-F]{64}", normalized):
        raise ValueError("release public key file must contain 32-byte hex")
    raw = bytes.fromhex(normalized)
    if len(raw) != 32:
        raise ValueError("release public key file must decode to 32 bytes")
    return raw


def _load_private_key(input_value: str) -> Ed25519PrivateKey:
    source = Path(input_value)
    if source.is_file():
        raw = source.read_bytes()
        if raw.startswith(b"-----BEGIN"):
            loaded = serialization.load_pem_private_key(raw, password=None)
            if not isinstance(loaded, Ed25519PrivateKey):
                raise ValueError("signing key PEM is not an Ed25519 private key")
            return loaded
        text = raw.decode("utf-8").strip()
    else:
        text = input_value.strip()

    if len(text) == 64 and re.fullmatch(r"[0-9a-fA-F]{64}", text):
        return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(text))

    raise ValueError("signing key must be a PEM file path or 32-byte hex seed")


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    decoded: dict[str, Any] = {}
    for key, value in pairs:
        if key in decoded:
            raise ValueError("manifest contains duplicate keys")
        decoded[key] = value
    return decoded


def _json_str(data: dict[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"manifest field {key} must be a non-empty string")
    return value


def _json_int(data: dict[str, object], key: str) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"manifest field {key} must be an integer")
    return value


def _canonical_manifest_bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def _validate_manifest_payload(payload: dict[str, object]) -> dict[str, object]:
    expected_keys = {
        "schema_version",
        "product",
        "channel",
        "version",
        "api_version",
        "update_api_version",
        "platform",
        "artifact",
        "release_url",
    }
    if set(payload) != expected_keys:
        raise ValueError("manifest fields are invalid")

    schema_version = _json_int(payload, "schema_version")
    product = _json_str(payload, "product")
    channel = _json_str(payload, "channel")
    version = _json_str(payload, "version")
    api_version = _json_int(payload, "api_version")
    update_api_version = _json_int(payload, "update_api_version")
    platform = _json_str(payload, "platform")
    release_url = _json_str(payload, "release_url")

    artifact_obj = payload.get("artifact")
    if not isinstance(artifact_obj, dict):
        raise TypeError("manifest field artifact must be an object")
    if set(artifact_obj) != {"url", "sha256", "size"}:
        raise ValueError("artifact fields are invalid")
    artifact_url = _json_str(artifact_obj, "url")
    artifact_sha256 = _json_str(artifact_obj, "sha256")
    artifact_size = _json_int(artifact_obj, "size")

    if schema_version != 1:
        raise ValueError("schema_version must be 1")
    if product != "plaf203-state-agent":
        raise ValueError("product must be plaf203-state-agent")
    if channel != "stable":
        raise ValueError("channel must be stable")
    if api_version != 1 or update_api_version != 1:
        raise ValueError("api_version and update_api_version must be 1")
    if platform != "linux-armv7-eabihf":
        raise ValueError("platform must be linux-armv7-eabihf")
    _validate_semver(version, "version")
    _https_url(artifact_url, "artifact.url", immutable=True)
    _https_url(release_url, "release_url")
    if not re.fullmatch(r"[0-9a-f]{64}", artifact_sha256):
        raise ValueError("artifact.sha256 must be lower-case SHA-256")
    if artifact_size <= 0:
        raise ValueError("artifact.size must be positive")

    return {
        "schema_version": schema_version,
        "product": product,
        "channel": channel,
        "version": version,
        "api_version": api_version,
        "update_api_version": update_api_version,
        "platform": platform,
        "artifact": {
            "url": artifact_url,
            "sha256": artifact_sha256,
            "size": artifact_size,
        },
        "release_url": release_url,
    }


def _manifest_bytes(
    *,
    version: str,
    artifact_url: str,
    release_url: str,
    artifact_sha256: str,
    artifact_size: int,
) -> bytes:
    payload = {
        "schema_version": 1,
        "product": "plaf203-state-agent",
        "channel": "stable",
        "version": version,
        "api_version": 1,
        "update_api_version": 1,
        "platform": "linux-armv7-eabihf",
        "artifact": {
            "url": artifact_url,
            "sha256": artifact_sha256,
            "size": artifact_size,
        },
        "release_url": release_url,
    }
    canonical = _validate_manifest_payload(payload)
    return _canonical_manifest_bytes(canonical)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build latest.json and latest.json.sig for plaf203-state-agent. "
            "Publish immutable artifact URL first, then publish signed manifest files."
        )
    )
    parser.add_argument(
        "--artifact-path",
        required=True,
        help="Path to built plaf203-state-agent artifact file",
    )
    parser.add_argument(
        "--artifact-url",
        required=True,
        help="Immutable HTTPS URL for the published artifact",
    )
    parser.add_argument(
        "--release-url",
        required=True,
        help="HTTPS release page URL",
    )
    parser.add_argument(
        "--signing-key",
        help=(
            "PEM path or 32-byte hex seed. If omitted, "
            "PETLIBRO_STATE_AGENT_SIGNING_KEY is required."
        ),
    )
    parser.add_argument(
        "--version-file",
        default=str(Path(__file__).resolve().parents[1] / "VERSION"),
    )
    parser.add_argument(
        "--public-key-file",
        default=str(Path(__file__).resolve().parents[1] / "release-public-key.hex"),
    )
    parser.add_argument(
        "--rotate-trust-anchor",
        action="store_true",
        help=(
            "Allow signing with a key that differs from --public-key-file to perform "
            "a trust-anchor rotation. Requires signer-derived public key and "
            "candidate trust anchor to be different."
        ),
    )
    parser.add_argument("--output-dir", default=".")
    parser.add_argument("--manifest-name", default="latest.json")
    parser.add_argument("--signature-name", default="latest.json.sig")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    signing_key_value = args.signing_key or os.environ.get(
        "PETLIBRO_STATE_AGENT_SIGNING_KEY"
    )
    if not signing_key_value:
        raise SystemExit(
            "A signing key is required via --signing-key or PETLIBRO_STATE_AGENT_SIGNING_KEY"
        )

    artifact_path = Path(args.artifact_path)
    version_path = Path(args.version_file)
    public_key_path = Path(args.public_key_file)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    artifact_url = _https_url(
        str(args.artifact_url).strip(), "artifact_url", immutable=True
    )
    release_url = _https_url(str(args.release_url).strip(), "release_url")

    version = version_path.read_text(encoding="utf-8").strip()
    try:
        _validate_semver(version, "VERSION")
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    artifact_bytes = artifact_path.read_bytes()
    artifact_size = len(artifact_bytes)
    if artifact_size <= 0:
        raise SystemExit("artifact must not be empty")
    artifact_sha256 = hashlib.sha256(artifact_bytes).hexdigest()

    private_key = _load_private_key(signing_key_value)
    candidate_trust_anchor = _load_public_key_hex(public_key_path)
    derived_public_key = private_key.public_key().public_bytes_raw()
    signer_fingerprint = hashlib.sha256(derived_public_key).hexdigest()
    candidate_fingerprint = hashlib.sha256(candidate_trust_anchor).hexdigest()

    print(f"Signer key fingerprint (sha256): {signer_fingerprint}")
    print(
        "Candidate trust-anchor fingerprint (sha256): "
        f"{candidate_fingerprint}"
    )
    if args.rotate_trust_anchor:
        print("Trust-anchor rotation mode: enabled")
        if derived_public_key == candidate_trust_anchor:
            raise SystemExit(
                "--rotate-trust-anchor requires signer key to differ from "
                "candidate trust anchor"
            )
    else:
        print("Trust-anchor rotation mode: disabled")
        if derived_public_key != candidate_trust_anchor:
            raise SystemExit(
                "signing key does not match candidate trust anchor; "
                "use --rotate-trust-anchor for intentional rotation"
            )

    manifest_bytes = _manifest_bytes(
        version=version,
        artifact_url=artifact_url,
        release_url=release_url,
        artifact_sha256=artifact_sha256,
        artifact_size=artifact_size,
    )

    reparsed = json.loads(
        manifest_bytes.decode("utf-8"),
        object_pairs_hook=_reject_duplicate_json_keys,
    )
    if not isinstance(reparsed, dict):
        raise SystemExit("manifest must be a JSON object")
    canonical_reparsed = _validate_manifest_payload(reparsed)
    if _canonical_manifest_bytes(canonical_reparsed) != manifest_bytes:
        raise SystemExit("manifest canonicalization mismatch")

    signature = private_key.sign(manifest_bytes)
    if len(signature) != 64:
        raise SystemExit("Ed25519 signature must be 64 bytes")

    Ed25519PublicKey.from_public_bytes(derived_public_key).verify(
        signature, manifest_bytes
    )

    manifest_path = output_dir / args.manifest_name
    signature_path = output_dir / args.signature_name
    manifest_path.write_bytes(manifest_bytes)
    signature_path.write_bytes(signature)

    print(f"Wrote {manifest_path}")
    print(f"Wrote {signature_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
