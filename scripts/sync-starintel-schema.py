#!/usr/bin/env python3
"""Pin and synchronize generated StarIntel schema artifacts from Star-Lang."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def canonical_head(root: Path) -> str:
    process = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    )
    return process.stdout.strip()


def rendered_json(value: dict) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canonical-root", type=Path)
    parser.add_argument("--canonical-repository")
    parser.add_argument("--canonical-commit")
    parser.add_argument("--release")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    repository = Path(__file__).resolve().parents[1]
    if args.canonical_root is None:
        if not args.check:
            parser.error("--canonical-root is required when synchronizing artifacts")
        lock = read_json(repository / "schema" / "starintel-schema.lock.json")
        local_artifacts = {
            "schema": repository / lock["local_schema_path"],
            "portable_manifest": repository / lock["local_portable_manifest_path"],
        }
        stale = [
            str(path)
            for name, path in local_artifacts.items()
            if not path.exists() or sha256(path) != lock["sha256"][name]
        ]
        if stale:
            raise SystemExit("invalid local StarIntel artifacts: " + ", ".join(stale))
        print(
            f"StarIntel {lock['release_version']} local schema artifacts match "
            f"{lock['canonical_repository']}@{lock['canonical_commit']}"
        )
        return 0

    required = {
        "--canonical-repository": args.canonical_repository,
        "--canonical-commit": args.canonical_commit,
        "--release": args.release,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        parser.error("required with --canonical-root: " + ", ".join(missing))

    root = args.canonical_root.resolve()
    if canonical_head(root) != args.canonical_commit:
        raise SystemExit("canonical checkout HEAD does not match --canonical-commit")

    release_root = root / "specs" / "starintel" / args.release
    manifest_path = release_root / "schema-lock-manifest.json"
    release_lock_path = release_root / "release-lock.json"
    schema_source = release_root / "generated" / "schema.json"
    portable_source = release_root / "generated" / "portable-manifest.json"

    manifest = read_json(manifest_path)
    release_lock = read_json(release_lock_path)
    if manifest["release_version"] != args.release:
        raise SystemExit("schema-lock manifest release does not match --release")
    if release_lock["releaseVersion"] != args.release:
        raise SystemExit("release lock does not match --release")

    artifacts = release_lock["artifacts"]
    expected = {
        "schema.json": schema_source,
        "portable-manifest.json": portable_source,
    }
    for name, source in expected.items():
        if sha256(source) != artifacts[name]:
            raise SystemExit(f"canonical artifact hash mismatch: {name}")

    destination = repository / "schema"
    schema_target = destination / "starintel-schema.json"
    portable_target = destination / "starintel-portable-manifest.json"
    lock_target = destination / "starintel-schema.lock.json"
    lock = {
        "authority_library": manifest["authority_library"],
        "canonical_commit": args.canonical_commit,
        "canonical_key_style": manifest["canonical_key_style"],
        "canonical_repository": args.canonical_repository,
        "expansion_path": f"specs/starintel/{args.release}/compatibility.json",
        "local_portable_manifest_path": "schema/starintel-portable-manifest.json",
        "local_schema_path": "schema/starintel-schema.json",
        "manifest_path": f"specs/starintel/{args.release}/schema-lock-manifest.json",
        "release_lock_path": f"specs/starintel/{args.release}/release-lock.json",
        "release_version": manifest["release_version"],
        "schema_path": f"specs/starintel/{args.release}/generated/schema.json",
        "schema_version": manifest["schema_version"],
        "sha256": {
            "portable_manifest": artifacts["portable-manifest.json"],
            "schema": artifacts["schema.json"],
        },
    }

    outputs = {
        schema_target: schema_source.read_bytes(),
        portable_target: portable_source.read_bytes(),
        lock_target: rendered_json(lock),
    }
    if args.check:
        stale = [str(path) for path, content in outputs.items() if not path.exists() or path.read_bytes() != content]
        if stale:
            raise SystemExit("stale StarIntel artifacts: " + ", ".join(stale))
        print(f"StarIntel {args.release} schema artifacts are current")
        return 0

    destination.mkdir(parents=True, exist_ok=True)
    for path, content in outputs.items():
        path.write_bytes(content)
    print(f"synchronized StarIntel {args.release} schema artifacts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
