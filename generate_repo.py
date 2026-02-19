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
  packages.json  <- for web file manager UI

GPG signing is handled automatically by GitHub Actions.

Usage:
    python generate_repo.py
    python generate_repo.py --input ~/pkg
"""

import argparse, datetime, glob, gzip, hashlib, json
import os, re, shutil, subprocess, sys
from pathlib import Path

# Config
ORIGIN       = "AndroStudio"
LABEL        = "AndroStudio Package Repository"
CODENAME     = "stable"
SUITE        = "stable"
COMPONENT    = "main"
DESCRIPTION  = "Official AndroStudio terminal package repository"
SUPPORTED_ARCHES = ['all', 'arm', 'i686', 'aarch64', 'x86_64', 'arm64', 'amd64']
HASHES       = ['md5', 'sha256']


def run(cmd):
    try:
        return subprocess.check_output(cmd, shell=True, universal_newlines=True,
                                       stderr=subprocess.DEVNULL).strip()
    except subprocess.CalledProcessError:
        return None


def hash_file(path, algo):
    h = hashlib.new(algo)
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def hash_label(algo):
    return 'MD5Sum' if algo == 'md5' else algo.upper()


def control_file_contents(debfile):
    """Extract control file from .deb using ar+tar. Supports gz/xz/zst."""
    file_list = run(f"ar t {debfile}")
    if file_list is None:
        print(f"  WARNING: Cannot list contents of '{os.path.basename(debfile)}' - skipping")
        return None

    file_list = file_list.split('\n')

    if 'control.tar.gz' in file_list:
        ctrl, flag = 'control.tar.gz', '-z'
    elif 'control.tar.xz' in file_list:
        ctrl, flag = 'control.tar.xz', '-J'
    elif 'control.tar.zst' in file_list:
        ctrl, flag = 'control.tar.zst', '--zstd'
    else:
        print(f"  WARNING: No control archive in '{os.path.basename(debfile)}' - skipping")
        return None

    contents = run(f"ar p {debfile} {ctrl} | tar -O {flag} -xf - ./control")
    if contents is None:
        print(f"  WARNING: Failed to extract control from '{os.path.basename(debfile)}' - skipping")
        return None
    return contents


def parse_control(text):
    """Parse control file text into a dict, handling multiline fields."""
    info = {}
    current_key = None
    for line in text.splitlines():
        if line.startswith(' ') or line.startswith('\t'):
            if current_key:
                info[current_key] += '\n' + line
        elif ': ' in line:
            key, _, val = line.partition(': ')
            info[key.strip()] = val.strip()
            current_key = key.strip()
    return info


def build_packages(pool_dir, repo_root):
    """Scan pool/main for .deb files and generate Packages, Packages.gz, packages.json."""
    bin_dir  = repo_root / 'dists' / CODENAME / COMPONENT / 'binary-aarch64'
    pkg_file = bin_dir / 'Packages'
    pkggz    = bin_dir / 'Packages.gz'
    bin_dir.mkdir(parents=True, exist_ok=True)

    # Find debs at any depth inside pool/main/
    deb_files = sorted([
        Path(p) for p in glob.glob(str(pool_dir / '**' / '*.deb'), recursive=True)
    ])

    if not deb_files:
        print("  WARNING: No .deb files found in pool/main/")
        pkg_file.write_text('', encoding='utf-8')
    else:
        print(f"  Found {len(deb_files)} .deb file(s)")

    entries           = []
    folder_map        = {}
    encountered_arches = set()

    PRIORITY_KEYS = [
        'Package', 'Version', 'Architecture', 'Maintainer',
        'Installed-Size', 'Depends', 'Pre-Depends', 'Recommends',
        'Suggests', 'Conflicts', 'Breaks', 'Replaces', 'Provides',
        'Homepage', 'Description'
    ]
    COMPUTED = {'Filename', 'Size', 'MD5Sum', 'SHA256', 'MD5sum', 'SHA1', 'SHA512'}

    for deb in deb_files:
        rel   = deb.relative_to(pool_dir)
        parts = rel.parts
        print(f"  Pkg  {'/'.join(parts)}")

        ctrl_text = control_file_contents(str(deb))
        if ctrl_text is None:
            continue

        info     = parse_control(ctrl_text)
        pkg_name = info.get('Package', deb.stem.split('_')[0])
        pkg_ver  = info.get('Version', '')
        pkg_arch = info.get('Architecture', 'aarch64')

        if pkg_arch not in SUPPORTED_ARCHES:
            print(f"    WARNING: Unsupported arch '{pkg_arch}' - skipping")
            continue
        encountered_arches.add(pkg_arch)

        filename_path = 'pool/main/' + '/'.join(parts)
        size          = deb.stat().st_size

        # Build Packages entry
        block = []
        added = set()
        for key in PRIORITY_KEYS:
            if key in info:
                block.append(f"{key}: {info[key]}")
                added.add(key)
        for key, val in info.items():
            if key not in added and key not in COMPUTED:
                block.append(f"{key}: {val}")
        block.append(f"Filename: {filename_path}")
        block.append(f"Size: {size}")
        for algo in HASHES:
            block.append(f"{hash_label(algo)}: {hash_file(str(deb), algo)}")
        entries.append('\n'.join(block))

        # Build packages.json map  { "m": { "micro": [{...}] } }
        if len(parts) >= 3:
            letter, pkgdir = parts[0], parts[1]
        elif len(parts) == 2:
            letter, pkgdir = parts[0], pkg_name
        else:
            letter = pkg_name[0].lower() if pkg_name else '.'
            pkgdir = pkg_name

        folder_map.setdefault(letter, {}).setdefault(pkgdir, []).append({
            'name':    deb.name,
            'path':    filename_path,
            'size':    size,
            'package': pkg_name,
            'version': pkg_ver,
            'desc':    info.get('Description', '').split('\n')[0],
        })

    # Write Packages
    packages_content = '\n\n'.join(entries) + ('\n' if entries else '')
    pkg_file.write_text(packages_content, encoding='utf-8')
    print(f"  OK   Packages ({len(entries)} entries)")

    # Write Packages.gz
    with open(pkg_file, 'rb') as fi, gzip.open(pkggz, 'wb', compresslevel=9) as fo:
        shutil.copyfileobj(fi, fo)
    print(f"  OK   Packages.gz")

    # Write packages.json
    (repo_root / 'packages.json').write_text(json.dumps(folder_map, indent=2), encoding='utf-8')
    print(f"  OK   packages.json")

    return bin_dir, pkg_file, pkggz, encountered_arches


def build_release(repo_root, bin_dir, pkg_file, pkggz, arches):
    """Generate dists/stable/Release with checksums."""
    dist_dir     = repo_root / 'dists' / CODENAME
    release_file = dist_dir / 'Release'
    now          = datetime.datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S UTC')
    arch_str     = ' '.join(sorted(arches)) if arches else 'aarch64'

    lines = [
        f"Origin: {ORIGIN}",
        f"Label: {LABEL}",
        f"Suite: {SUITE}",
        f"Codename: {CODENAME}",
        f"Date: {now}",
        f"Architectures: {arch_str}",
        f"Components: {COMPONENT}",
        f"Description: {DESCRIPTION}",
    ]

    for algo in HASHES:
        lines.append(f"{hash_label(algo)}:")
        for fpath in [pkg_file, pkggz]:
            rel  = fpath.relative_to(dist_dir)
            size = fpath.stat().st_size
            h    = hash_file(str(fpath), algo)
            lines.append(f" {h} {size:>10} {rel}")

    release_file.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f"  OK   Release")


def import_debs(input_path, pool_dir):
    """Copy .deb files from input_path into pool/main/letter/pkgname/ structure."""
    debs = sorted(Path(input_path).glob('*.deb'))
    if not debs:
        sys.exit(f"No .deb files found in {input_path}")

    copied = skipped = 0
    for deb in debs:
        ctrl = control_file_contents(str(deb))
        if ctrl:
            pkgname = parse_control(ctrl).get('Package', deb.stem.split('_')[0])
        else:
            pkgname = deb.stem.split('_')[0]

        letter     = pkgname[0].lower()
        target_dir = pool_dir / letter / pkgname
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / deb.name

        if target.exists() and target.stat().st_size == deb.stat().st_size:
            skipped += 1
            continue

        shutil.copy2(str(deb), str(target))
        print(f"  Copied  {letter}/{pkgname}/{deb.name}")
        copied += 1

    print(f"\n  Copied: {copied}   Skipped: {skipped} (unchanged)")


def sign_release(repo_root):
    """Sign Release file with GPG to produce InRelease and Release.gpg."""
    release = repo_root / 'dists' / CODENAME / 'Release'

    if not release.exists():
        print("  ERROR: Release file not found - cannot sign")
        return False

    # Check GPG is available
    if run("gpg --version") is None:
        print("  WARNING: gpg not found - skipping signing")
        return False

    # Check a secret key exists
    keys = run("gpg --list-secret-keys --keyid-format=long")
    if not keys:
        print("  WARNING: No GPG secret key found - skipping signing")
        print("  Tip: run 'gpg --gen-key' to create one")
        return False

    inrelease = repo_root / 'dists' / CODENAME / 'InRelease'
    releasegpg = repo_root / 'dists' / CODENAME / 'Release.gpg'

    # InRelease = clearsigned (modern apt)
    r1 = run(f"gpg --batch --yes --armor --clearsign "
             f"-o {inrelease} {release}")

    # Release.gpg = detached signature (older apt)
    r2 = run(f"gpg --batch --yes --armor --detach-sign "
             f"-o {releasegpg} {release}")

    if inrelease.exists() and releasegpg.exists():
        print(f"  OK   InRelease  (clearsign)")
        print(f"  OK   Release.gpg (detached)")
        return True
    else:
        print("  ERROR: GPG signing failed")
        return False


def main():
    parser = argparse.ArgumentParser(description='AndroStudio Repo Generator v2.0')
    parser.add_argument('--input', '-i', default=None,
                        help='Import .deb files from this folder into pool/main/ first')
    parser.add_argument('--repo', '-r', default=str(Path(__file__).parent),
                        help='Repo root folder (default: script directory)')
    parser.add_argument('--no-sign', action='store_true',
                        help='Skip GPG signing')
    args = parser.parse_args()

    repo_root = Path(args.repo).resolve()
    pool_dir  = repo_root / 'pool' / 'main'

    print()
    print("╔══════════════════════════════════════════╗")
    print("║   AndroStudio Repo Generator  v2.0       ║")
    print("╚══════════════════════════════════════════╝")
    print()
    print(f"  Repo : {repo_root}")
    print(f"  Pool : {pool_dir}")
    print()

    if args.input:
        print(f"Import  Importing from {args.input} ...")
        import_debs(args.input, pool_dir)
        print()

    print("Build   Scanning pool/main/ ...")
    bin_dir, pkg_file, pkggz, arches = build_packages(pool_dir, repo_root)

    print()
    print("Build   Generating Release ...")
    build_release(repo_root, bin_dir, pkg_file, pkggz, arches)

    if not args.no_sign:
        print()
        print("Sign    Signing Release with GPG ...")
        sign_release(repo_root)

    print()
    print("Done    Files updated:")
    print("          dists/stable/main/binary-aarch64/Packages")
    print("          dists/stable/main/binary-aarch64/Packages.gz")
    print("          dists/stable/Release")
    if not args.no_sign:
        print("          dists/stable/InRelease")
        print("          dists/stable/Release.gpg")
    print("          packages.json")
    print()
    print("Next    git add . && git commit -m 'update repo' && git push")
    print()


if __name__ == '__main__':
    main()

