import hashlib
import importlib.util
import json
import re
import subprocess
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "feeder-state-agent" / "scripts" / "build_release_manifest.py"
SPEC = importlib.util.spec_from_file_location("build_release_manifest", SCRIPT_PATH)
assert SPEC is not None
assert SPEC.loader is not None
release_tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_tool)


def _make_keypair():
    private_key = Ed25519PrivateKey.generate()
    public_key_bytes = private_key.public_key().public_bytes_raw()
    return private_key, public_key_bytes


def _write_public_key_file(path: Path, public_key_bytes: bytes) -> Path:
    path.write_text(public_key_bytes.hex() + "\n", encoding="utf-8")
    return path


def _run_tool(
    *,
    tmp_path: Path,
    signing_private_key: Ed25519PrivateKey,
    candidate_public_key_bytes: bytes,
    artifact_bytes: bytes = b"synthetic-agent-binary",
    version: str = "1.2.3",
    rotate: bool = False,
) -> tuple[Path, Path, Path, int]:
    artifact_path = tmp_path / "plaf203-state-agent"
    artifact_path.write_bytes(artifact_bytes)

    version_path = tmp_path / "VERSION"
    version_path.write_text(f"{version}\n", encoding="utf-8")

    public_key_path = _write_public_key_file(
        tmp_path / "release-public-key.hex", candidate_public_key_bytes
    )

    output_dir = tmp_path / "release"
    output_dir.mkdir()

    argv = [
        "--artifact-path",
        str(artifact_path),
        "--artifact-url",
        "https://downloads.example.invalid/plaf203/1.2.3/plaf203-state-agent",
        "--release-url",
        "https://github.com/example/project/releases/tag/v1.2.3",
        "--version-file",
        str(version_path),
        "--public-key-file",
        str(public_key_path),
        "--output-dir",
        str(output_dir),
        "--signing-key",
        signing_private_key.private_bytes_raw().hex(),
    ]
    if rotate:
        argv.append("--rotate-trust-anchor")

    rc = release_tool.main(argv)
    return artifact_path, version_path, output_dir, rc


def test_normal_embedded_a_signed_a_accepted(tmp_path, capsys):
    private_a, public_a = _make_keypair()

    artifact_path, _version_path, output_dir, rc = _run_tool(
        tmp_path=tmp_path,
        signing_private_key=private_a,
        candidate_public_key_bytes=public_a,
    )
    assert rc == 0

    manifest_path = output_dir / "latest.json"
    signature_path = output_dir / "latest.json.sig"
    assert manifest_path.is_file()
    assert signature_path.is_file()

    manifest_bytes = manifest_path.read_bytes()
    payload = json.loads(manifest_bytes.decode("utf-8"))
    assert payload["schema_version"] == 1
    assert payload["product"] == "plaf203-state-agent"
    assert payload["version"] == "1.2.3"
    assert payload["artifact"]["size"] == len(artifact_path.read_bytes())

    expected_manifest = (
        "{"
        '"schema_version":1,'
        '"product":"plaf203-state-agent",'
        '"channel":"stable",'
        '"version":"1.2.3",'
        '"api_version":1,'
        '"update_api_version":1,'
        '"platform":"linux-armv7-eabihf",'
        '"artifact":{'
        '"url":"https://downloads.example.invalid/plaf203/1.2.3/plaf203-state-agent",'
        f'"sha256":"{hashlib.sha256(artifact_path.read_bytes()).hexdigest()}",'
        f'"size":{len(artifact_path.read_bytes())}'
        "},"
        '"release_url":"https://github.com/example/project/releases/tag/v1.2.3"'
        "}"
    ).encode()
    assert manifest_bytes == expected_manifest

    signature = signature_path.read_bytes()
    assert len(signature) == 64
    Ed25519PublicKey.from_public_bytes(public_a).verify(
        signature, manifest_bytes
    )

    output = capsys.readouterr().out
    assert "Trust-anchor rotation mode: disabled" in output
    assert (
        f"Signer key fingerprint (sha256): "
        f"{hashlib.sha256(public_a).hexdigest()}" in output
    )
    assert (
        "Candidate trust-anchor fingerprint (sha256): "
        f"{hashlib.sha256(public_a).hexdigest()}" in output
    )


def test_accidental_embedded_b_signed_a_without_flag_rejected(tmp_path):
    private_a, _public_a = _make_keypair()
    _private_b, public_b = _make_keypair()

    with pytest.raises(SystemExit, match="does not match candidate trust anchor"):
        _run_tool(
            tmp_path=tmp_path,
            signing_private_key=private_a,
            candidate_public_key_bytes=public_b,
        )


def test_intentional_embedded_b_signed_a_with_flag_accepted_and_verifies_with_derived_a(
    tmp_path, capsys
):
    private_a, public_a = _make_keypair()
    _private_b, public_b = _make_keypair()

    artifact_path, _version_path, output_dir, rc = _run_tool(
        tmp_path=tmp_path,
        signing_private_key=private_a,
        candidate_public_key_bytes=public_b,
        rotate=True,
    )
    assert rc == 0

    manifest_bytes = (output_dir / "latest.json").read_bytes()
    signature = (output_dir / "latest.json.sig").read_bytes()

    Ed25519PublicKey.from_public_bytes(public_a).verify(signature, manifest_bytes)
    with pytest.raises(InvalidSignature):
        Ed25519PublicKey.from_public_bytes(public_b).verify(signature, manifest_bytes)

    payload = json.loads(manifest_bytes.decode("utf-8"))
    assert payload["artifact"]["size"] == len(artifact_path.read_bytes())

    output = capsys.readouterr().out
    assert "Trust-anchor rotation mode: enabled" in output
    assert (
        f"Signer key fingerprint (sha256): "
        f"{hashlib.sha256(public_a).hexdigest()}" in output
    )
    assert (
        "Candidate trust-anchor fingerprint (sha256): "
        f"{hashlib.sha256(public_b).hexdigest()}" in output
    )


def test_candidate_build_metadata_still_embeds_b(tmp_path):
    feeder_dir = ROOT / "feeder-state-agent"
    version_path = tmp_path / "VERSION"
    version_path.write_text("1.2.3\n", encoding="utf-8")

    _private_b, public_b = _make_keypair()
    public_key_path = _write_public_key_file(tmp_path / "release-public-key.hex", public_b)
    build_dir = tmp_path / "build"

    subprocess.run(
        [
            "make",
            "-C",
            str(feeder_dir),
            str(build_dir / "generated_release_metadata.h"),
            f"VERSION_FILE={version_path}",
            f"PUBLIC_KEY_HEX_FILE={public_key_path}",
            f"BUILD_DIR={build_dir}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    header_path = build_dir / "generated_release_metadata.h"
    header = header_path.read_text(encoding="utf-8")
    assert "#define RELEASE_PUBLIC_KEY_EMBEDDED 1" in header
    assert "static const uint8_t RELEASE_PUBLIC_KEY[32] = {" in header
    embedded_key = bytes.fromhex("".join(re.findall(r"0x([0-9A-F]{2})", header)))
    assert embedded_key == public_b


def test_subsequent_normal_embedded_b_signed_b_accepted(tmp_path):
    private_b, public_b = _make_keypair()

    _artifact_path, _version_path, output_dir, rc = _run_tool(
        tmp_path=tmp_path,
        signing_private_key=private_b,
        candidate_public_key_bytes=public_b,
    )
    assert rc == 0

    manifest_bytes = (output_dir / "latest.json").read_bytes()
    signature = (output_dir / "latest.json.sig").read_bytes()
    Ed25519PublicKey.from_public_bytes(public_b).verify(signature, manifest_bytes)


def test_rotation_flag_with_equal_a_a_rejected(tmp_path):
    private_a, public_a = _make_keypair()

    with pytest.raises(
        SystemExit,
        match="--rotate-trust-anchor requires signer key to differ",
    ):
        _run_tool(
            tmp_path=tmp_path,
            signing_private_key=private_a,
            candidate_public_key_bytes=public_a,
            rotate=True,
        )


def test_release_and_build_reject_numeric_prerelease_leading_zero(tmp_path):
    with pytest.raises(ValueError, match="must not have leading zeros"):
        release_tool._validate_semver("1.2.3-rc.01", "version")

    feeder_dir = ROOT / "feeder-state-agent"
    version_path = tmp_path / "VERSION"
    version_path.write_text("1.2.3-rc.01\n", encoding="utf-8")
    _private_key, public_key = _make_keypair()
    public_key_path = _write_public_key_file(
        tmp_path / "release-public-key.hex", public_key
    )
    build_dir = tmp_path / "build"
    result = subprocess.run(
        [
            "make",
            "-C",
            str(feeder_dir),
            str(build_dir / "generated_release_metadata.h"),
            f"VERSION_FILE={version_path}",
            f"PUBLIC_KEY_HEX_FILE={public_key_path}",
            f"BUILD_DIR={build_dir}",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "numeric SemVer prerelease identifier" in result.stderr


@pytest.mark.parametrize(
    "url",
    [
        "http://example.invalid/agent",
        "https://user@example.invalid/agent",
        "https://example.invalid/agent#fragment",
        "https://example.invalid/agent?mutable=1",
        "https://example.invalid/",
    ],
)
def test_release_tool_rejects_nonimmutable_artifact_urls(url):
    with pytest.raises(ValueError):
        release_tool._https_url(url, "artifact.url", immutable=True)
