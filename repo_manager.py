#!/usr/bin/env python3
"""
AndroStudio PKG Repo Generator
================================
Automatically generates Packages, Packages.gz, and Release files
from .deb files in the pool/main/ directory.

Usage:
    python generate_repo.py

Just drop your .deb files into pool/main/ and run this script.
"""

import os
import gzip
import hashlib
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
REPO_ROOT     = Path(__file__).parent          # folder where this script lives
POOL_DIR      = REPO_ROOT / "pool" / "main"
ARCH          = "aarch64"
DIST          = "stable"
COMPONENT     = "main"
ORIGIN        = "AndroStudio"
LABEL         = "AndroStudio Package Repository"
DESCRIPTION   = "Official AndroStudio terminal package repository"

BINARY_DIR    = REPO_ROOT / "dists" / DIST / COMPONENT / f"binary-{ARCH}"
PACKAGES_FILE = BINARY_DIR / "Packages"
PACKAGESGZ    = BINARY_DIR / "Packages.gz"
RELEASE_FILE  = REPO_ROOT / "dists" / DIST / "Release"
# ──────────────────────────────────────────────────────────────────────────────


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_deb_info(deb_path: Path) -> dict:
    """Extract control fields from a .deb file using dpkg-deb or ar+tar fallback."""
    info = {}

    # Try dpkg-deb first (available if dpkg is installed)
    try:
        result = subprocess.run(
            ["dpkg-deb", "-f", str(deb_path)],
            capture_output=True, text=True, check=True
        )
        for line in result.stdout.splitlines():
            if ": " in line:
                key, _, val = line.partition(": ")
                info[key.strip()] = val.strip()
        return info
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    # Fallback: parse filename as pkg_version_arch.deb
    name = deb_path.stem  # e.g. git_2.44.0_aarch64
    parts = name.split("_")
    if len(parts) >= 3:
        info["Package"]      = parts[0]
        info["Version"]      = parts[1]
        info["Architecture"] = parts[2]
    elif len(parts) == 2:
        info["Package"]      = parts[0]
        info["Version"]      = parts[1]
        info["Architecture"] = ARCH
    else:
        info["Package"]      = name
        info["Version"]      = "1.0"
        info["Architecture"] = ARCH

    info.setdefault("Description", f"{info['Package']} package")
    info.setdefault("Maintainer",  "AndroStudio <repo@androstudio.dev>")
    return info


def build_packages():
    """Scan pool/main/ for .deb files and write the Packages index."""
    BINARY_DIR.mkdir(parents=True, exist_ok=True)
    POOL_DIR.mkdir(parents=True, exist_ok=True)

    deb_files = sorted(POOL_DIR.rglob("*.deb"))  # rglob = recursive, finds in any subfolder

    if not deb_files:
        print("⚠  No .deb files found in pool/main/ or subfolders — Packages will be empty.")

    entries = []
    for deb in deb_files:
        # Preserve subfolder path e.g. pool/main/m/micro_2.0.15_aarch64.deb
        rel_path = "pool/main/" + str(deb.relative_to(POOL_DIR)).replace("\\", "/")
        print(f"  📦  Processing {rel_path} ...")
        info = extract_deb_info(deb)

        size       = deb.stat().st_size
        file_sha   = sha256(deb)

        # Build control block
        block_lines = []
        # Required fields first
        for key in ["Package", "Version", "Architecture", "Maintainer",
                    "Installed-Size", "Depends", "Pre-Depends",
                    "Conflicts", "Breaks", "Replaces", "Provides",
                    "Homepage", "Description"]:
            if key in info:
                block_lines.append(f"{key}: {info[key]}")

        # Add computed fields
        block_lines.append(f"Filename: {rel_path}")
        block_lines.append(f"Size: {size}")
        block_lines.append(f"SHA256: {file_sha}")

        # Add any remaining fields not already included
        skip = {"Package","Version","Architecture","Maintainer",
                "Installed-Size","Depends","Pre-Depends","Conflicts",
                "Breaks","Replaces","Provides","Homepage","Description",
                "Filename","Size","SHA256","MD5sum"}
        for key, val in info.items():
            if key not in skip:
                block_lines.append(f"{key}: {val}")

        entries.append("\n".join(block_lines))

    packages_content = "\n\n".join(entries)
    if entries:
        packages_content += "\n"  # trailing newline

    PACKAGES_FILE.write_text(packages_content, encoding="utf-8")
    print(f"  ✅  Packages written ({len(deb_files)} package(s))")

    # Build folder map for the web file browser (packages.json)
    import json
    folder_map = {}
    for deb in deb_files:
        rel    = deb.relative_to(POOL_DIR)
        parts  = rel.parts
        folder = parts[0] if len(parts) > 1 else "."
        inf    = extract_deb_info(deb)
        folder_map.setdefault(folder, []).append({
            "name":    deb.name,
            "path":    "pool/main/" + str(rel).replace("\\", "/"),
            "size":    deb.stat().st_size,
            "package": inf.get("Package", deb.stem.split("_")[0]),
            "version": inf.get("Version", ""),
            "desc":    inf.get("Description", "").split("\n")[0],
        })
    json_path = REPO_ROOT / "packages.json"
    json_path.write_text(json.dumps(folder_map, indent=2), encoding="utf-8")
    print(f"  ✅  packages.json written")

    return packages_content


def build_packages_gz():
    """Compress Packages → Packages.gz"""
    with open(PACKAGES_FILE, "rb") as f_in:
        with gzip.open(PACKAGESGZ, "wb", compresslevel=9) as f_out:
            shutil.copyfileobj(f_in, f_out)
    print(f"  ✅  Packages.gz written")


def build_release():
    """Generate the Release file with checksums."""
    now = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")

    def file_entry(path: Path) -> tuple:
        rel = path.relative_to(REPO_ROOT / "dists" / DIST)
        size = path.stat().st_size
        return str(rel), path.stat().st_size, md5(path), sha256(path)

    files = [PACKAGES_FILE, PACKAGESGZ]
    entries = [file_entry(f) for f in files]

    md5_lines    = "\n".join(f" {e[2]} {e[1]:>10} {e[0]}" for e in entries)
    sha256_lines = "\n".join(f" {e[3]} {e[1]:>10} {e[0]}" for e in entries)

    release = f"""Origin: {ORIGIN}
Label: {LABEL}
Suite: {DIST}
Codename: {DIST}
Date: {now}
Architectures: {ARCH}
Components: {COMPONENT}
Description: {DESCRIPTION}
MD5Sum:
{md5_lines}
SHA256:
{sha256_lines}
"""

    RELEASE_FILE.parent.mkdir(parents=True, exist_ok=True)
    RELEASE_FILE.write_text(release, encoding="utf-8")
    print(f"  ✅  Release written")


def main():
    print()
    print("╔══════════════════════════════════════╗")
    print("║   AndroStudio Repo Generator  v1.0   ║")
    print("╚══════════════════════════════════════╝")
    print()

    print("📂  Scanning pool/main/ for .deb files...")
    build_packages()

    print("🗜   Compressing Packages → Packages.gz...")
    build_packages_gz()

    print("📝  Generating Release file...")
    build_release()

    print()
    print("✨  Done! Files updated:")
    print(f"    {PACKAGES_FILE.relative_to(REPO_ROOT)}")
    print(f"    {PACKAGESGZ.relative_to(REPO_ROOT)}")
    print(f"    {RELEASE_FILE.relative_to(REPO_ROOT)}")
    print(f"    packages.json")
    print()
    print("💡  Now commit and push to GitHub to publish your repo.")
    print()


if __name__ == "__main__":
    main()

