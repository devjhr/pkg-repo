#!/usr/bin/env python3
"""
AndroStudio Repo Generator v2.0
================================
Based on termux-apt-repo by Grimler91
Modified for AndroStudio pkg-repo structure:

  pool/main/m/micro/micro_2.0.15_aarch64.deb
  pool/main/g/git/git_2.51.2_aarch64.deb

Generates:
  dists/stable/main/binary-aarch64/Packages
  dists/stable/main/binary-aarch64/Packages.gz
  dists/stable/Release
  packages.json  ← for web file manager UI

GPG signing is handled automatically by GitHub Actions.

Usage:
    python generate_repo.py
    python generate_repo.py --input ~/pkg --repo ~/pkg-repo
"""

import argparse
import datetime
import glob
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# ── Config ─────────────────────────────────────────────────────────────────────
ORIGIN        = "AndroStudio"
LABEL         = "AndroStudio Package Repository"
CODENAME      = "stable"
SUITE         = "stable"
COMPONENT     = "main"
DESCRIPTION   = "Official AndroStudio terminal package repository"
SUPPORTED_ARCHES = ['all', 'arm', 'i686', 'aarch64', 'x86_64', 'arm64', 'amd64']
HASHES        = ['md5', 'sha256']   # md5 + sha256 (apt needs both)
# ───────────────────────────────────────────────────────────────────────────────


# ── Helpers ────────────────────────────────────────────────────────────────────

def run(cmd):
    """Run a shell command, return output or None on failure."""
    try:
        return subprocess.check_output(
            cmd, shell=True, universal_newlines=True,
            stderr=subprocess.DEVNULL
        ).strip()
    except subprocess.CalledProcessError:
        return None


def hash_file(path, algo):
    h = hashlib.new(algo)
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def hash_label(algo):
    """Return the Release file label for a hash algorithm."""
    return 'MD5Sum' if algo == 'md5' else algo.upper()


def control_file_contents(debfile):
    """
    Extract the control file from a .deb using ar + tar.
    Supports control.tar.gz, control.tar.xz, control.tar.zst
    This is the accurate method — reads real metadata, not just filenames.
    """
    file_list = run(f"ar t {debfile}")
    if file_list is None:
        print(f"  ⚠  Cannot list contents of '{debfile}' — skipping")
        return None

    file_list = file_list.split('\n')

    if 'control.tar.gz' in file_list:
        ctrl = 'control.tar.gz'; tar_flag = '-z'
    elif 'control.tar.xz' in file_list:
        ctrl = 'control.tar.xz'; tar_flag = '-J'
    elif 'control.tar.zst' in file_list:
        ctrl = 'control.tar.zst'; tar_flag = '--zstd'
    else:
        print(f"  ⚠  No control archive found in '{debfile}' — skipping")
        return None

    contents = run(f"ar p {debfile} {ctrl} | tar -O {tar_flag} -xf - ./control")
    if contents is None:
        print(f"  ⚠  Failed to extract control from '{debfile}' — skipping")
        return None

    return contents


def parse_control(contents):
    """Parse control file text into a dict."""
    info = {}
    current_key = None
    for line in contents.splitlines():
        if line.startswith(' ') or line.startswith('\t'):
            # Continuation line (multiline field like Description)
            if current_key:
                info[current_key] = info[current_key] + '\n' + line
        elif ': ' in line:
            key, _, val = line.partition(': ')
            info[key.strip()] = val.strip()
            current_key = key.strip()
    return info


# ── Core: build Packages index ─────────────────────────────────────────────────

def build_packages(pool_dir, repo_root):
    """
    Scan pool/main/**/  for .deb files (3-level deep: letter/pkgname/file.deb)
    Build Packages, Packages.gz, and packages.json
    """
    DIST_DIR   = repo_root / 'dists' / CODENAME / COMPONENT
    BIN_DIR    = DIST_DIR / f'binary-aarch64'
    PKG_FILE   = BIN_DIR / 'Packages'
    PKGGZ_FILE = BIN_DIR / 'Packages.gz'

    BIN_DIR.mkdir(parents=True, exist_ok=True)

    # Find all .deb files — supports:
    #   pool/main/m/micro/micro_2.0.15_aarch64.deb  (3-level ✅)
    #   pool/main/m/micro_2.0.15_aarch64.deb        (2-level ✅)
    #   pool/main/micro_2.0.15_aarch64.deb          (flat ✅)
    deb_files = sorted([
        Path(p) for p in
        glob.glob(str(pool_dir / '**' / '*.deb'), recursive=True)
    ])

    if not deb_files:
        print("  ⚠  No .deb files found in pool/main/")
        PKG_FILE.write_text('', encoding='utf-8')
    else:
        print(f"  Found {len(deb_files)} .deb file(s)")

    entries      = []
    folder_map   = {}   # for packages.json: { "m": { "micro": [{...}] } }
    encountered_arches = set()

    for deb in deb_files:
        rel_to_pool = deb.relative_to(pool_dir)
        parts       = rel_to_pool.parts
        # parts = ('m', 'micro', 'micro_2.0.15_aarch64.deb')  3-level
        # parts = ('m', 'micro_2.0.15_aarch64.deb')           2-level
        # parts = ('micro_2.0.15_aarch64.deb',)               flat

        print(f"  📦  {'/'.join(parts)}")

        # Read real control data from inside the .deb
        ctrl_text = control_file_contents(str(deb))
        if ctrl_text is None:
            continue

        info = parse_control(ctrl_text)

        pkg_name = info.get('Package', deb.stem.split('_')[0])
        pkg_ver  = info.get('Version', '')
        pkg_arch = info.get('Architecture', 'aarch64')

        if pkg_arch not in SUPPORTED_ARCHES:
            print(f"    ⚠  Unsupported arch '{pkg_arch}' — skipping")
            continue

        encountered_arches.add(pkg_arch)

        # Filename path in Packages index — relative to repo root
        filename_path = 'pool/main/' + '/'.join(parts)

        size = deb.stat().st_size

        # Build the Packages entry
        # Start with control fields in order
        block_lines = []
        priority_keys = [
            'Package', 'Version', 'Architecture', 'Maintainer',
            'Installed-Size', 'Depends', 'Pre-Depends', 'Recommends',
            'Suggests', 'Conflicts', 'Breaks', 'Replaces', 'Provides',
            'Homepage', 'Description'
        ]
        added = set()
        for key in priority_keys:
            if key in info:
                block_lines.append(f"{key}: {info[key]}")
                added.add(key)

        # Add any remaining fields from control not yet included
        skip = added | {'Filename', 'Size', 'MD5sum', 'SHA1', 'SHA256', 'SHA512'}
        for key, val in info.items():
            if key not in skip:
                block_lines.append(f"{key}: {val}")

        # Append computed fields
        block_lines.append(f"Filename: {filename_path}")
        block_lines.append(f"Size: {size}")
        for algo in HASHES:
            label = hash_label(algo)
            block_lines.append(f"{label}: {hash_file(str(deb), algo)}")

        entries.append('\n'.join(block_lines))

        # Build packages.json folder map  { "m": { "micro": [{...}] } }
        if len(parts) >= 3:
            letter = parts[0]
            pkgdir = parts[1]
        elif len(parts) == 2:
            letter = parts[0]
            pkgdir = pkg_name
        else:
            letter = pkg_name[0].lower() if pkg_name else '.'
            pkgdir = pkg_name

        folder_map \
            .setdefault(letter, {}) \
            .setdefault(pkgdir, []) \
            .append({
                'name':    deb.name,
                'path':    filename_path,
                'size':    size,
                'package': pkg_name,
                'version': pkg_ver,
                'desc':    info.get('Description', '').split('\n')[0],
            })

    # Write Packages
    packages_content = '\n\n'.join(entries)
    if entries:
        packages_content += '\n'
    PKG_FILE.write_text(packages_content, encoding='utf-8')
    print(f"  ✅  Packages written ({len(entries)} entries)")

    # Write Packages.gz
    with open(PKG_FILE, 'rb') as f_in:
        with gzip.open(PKGGZ_FILE, 'wb', compresslevel=9) as f_out:
            shutil.copyfileobj(f_in, f_out)
    print(f"  ✅  Packages.gz written")

    # Write packages.json
    json_path = repo_root / 'packages.json'
    json_path.write_text(json.dumps(folder_map, indent=2), encoding='utf-8')
    print(f"  ✅  packages.json written")

    return BIN_DIR, PKG_FILE, PKGGZ_FILE, encountered_arches


# ── Core: build Release ────────────────────────────────────────────────────────

def build_release(repo_root, bin_dir, pkg_file, pkggz_file, arches):
    """Generate dists/stable/Release with proper checksums."""
    DIST_DIR     = repo_root / 'dists' / CODENAME
    RELEASE_FILE = DIST_DIR / 'Release'

    now = datetime.datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S UTC')

    lines = [
        f"Origin: {ORIGIN}",
        f"Label: {LABEL}",
        f"Suite: {SUITE}",
        f"Codename: {CODENAME}",
        f"Date: {now}",
        f"Architectures: {' '.join(sorted(arches)) if arches else 'aarch64'}",
        f"Components: {COMPONENT}",
        f"Description: {DESCRIPTION}",
    ]

    # Files to checksum — relative to dists/stable/
    checksum_files = [pkg_file, pkggz_file]

    for algo in HASHES:
        lines.append(f"{hash_label(algo)}:")
        for fpath in checksum_files:
            rel  = fpath.relative_to(DIST_DIR)
            size = fpath.stat().st_size
            h    = hash_file(str(fpath), algo)
            lines.append(f" {h} {size:>10} {rel}")

    RELEASE_FILE.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f"  ✅  Release written")
    return RELEASE_FILE


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='AndroStudio Repo Generator v2.0'
    )
    parser.add_argument(
        '--input', '-i',
        default=None,
        help='Source folder with .deb files (default: auto-detect ~/pkg)'
    )
    parser.add_argument(
        '--repo', '-r',
        default=str(Path(__file__).parent),
        help='Repo root folder (default: same folder as this script)'
    )
    args = parser.parse_args()

    repo_root = Path(args.repo).resolve()
    pool_dir  = repo_root / 'pool' / 'main'

    print()
    print("╔══════════════════════════════════════════╗")
    print("║   AndroStudio Repo Generator  v2.0       ║")
    print("╚══════════════════════════════════════════╝")
    print()

    # If --input given, copy/move debs into pool structure first
    if args.input:
        input_path = Path(args.input).resolve()
        if not input_path.exists():
            sys.exit(f"❌  Input path not found: {input_path}")

        print(f"📂  Importing .deb files from {input_path} ...")
        debs = sorted(input_path.glob('*.deb'))
        if not debs:
            sys.exit(f"❌  No .deb files found in {input_path}")

        copied = 0
        for deb in debs:
            # Read package name from control for accurate folder naming
            ctrl = control_file_contents(str(deb))
            if ctrl:
                info    = parse_control(ctrl)
                pkgname = info.get('Package', deb.stem.split('_')[0])
            else:
                pkgname = deb.stem.split('_')[0]

            letter     = pkgname[0].lower()
            target_dir = pool_dir / letter / pkgname
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / deb.name

            if target.exists() and target.stat().st_size == deb.stat().st_size:
                continue  # skip identical

            shutil.copy2(str(deb), str(target))
            print(f"  ✅  {letter}/{pkgname}/{deb.name}")
            copied += 1

        print(f"\n  Imported: {copied} file(s)")
        print()

    print("📦  Building Packages index...")
    bin_dir, pkg_file, pkggz_file, arches = build_packages(pool_dir, repo_root)

    print()
    print("📝  Building Release file...")
    build_release(repo_root, bin_dir, pkg_file, pkggz_file, arches)

    print()
    print("✨  Done! Files updated:")
    print(f"    dists/stable/main/binary-aarch64/Packages")
    print(f"    dists/stable/main/binary-aarch64/Packages.gz")
    print(f"    dists/stable/Release")
    print(f"    packages.json")
    print()
    print("🔏  GPG signing: handled automatically by GitHub Actions on push")
    print()
    print("💡  Next:")
    print("    git add .")
    print("    git commit -m 'update repo'")
    print("    git push")
    print()


if __name__ == '__main__':
    main()

