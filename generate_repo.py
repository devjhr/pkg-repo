#!/usr/bin/env python3
"""
AndroStudio Repo Generator v3.0
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
    python generate_repo.py --release v1.0
    python generate_repo.py --input ~/pkg --release v1.0
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

# GitHub repo slug — change to yours
GH_REPO      = "devjhr/pkg-repo"

# Files >= this size are expected to be in GitHub Releases
LARGE_FILE_THRESHOLD = 90 * 1024 * 1024  # 90 MB


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


def fetch_release_urls(tag):
    """
    Build download URLs for all .deb assets in a GitHub Release.
    URL format is always predictable — no JSON parsing needed:
      https://github.com/{repo}/releases/download/{tag}/{filename}
    Requires: gh CLI to verify the release exists and list filenames.
    """
    result = run(f"gh release view {tag} --repo {GH_REPO} --json assets")
    if result is None:
        print(f"  WARNING: Could not fetch release '{tag}'.")
        print(f"  Make sure: pkg install gh && gh auth login")
        return {}
    try:
        assets = json.loads(result).get('assets', [])
        urls = {}
        for a in assets:
            name = a.get('name', '')
            if name.endswith('.deb'):
                # Construct URL directly — always this format on GitHub
                url = f"https://github.com/{GH_REPO}/releases/download/{tag}/{name}"
                urls[name] = url
        print(f"  Found {len(urls)} .deb asset(s) in release {tag}")
        for name, url in urls.items():
            print(f"    {name}")
            print(f"    → {url}")
        return urls
    except Exception as e:
        print(f"  WARNING: Failed to parse release assets: {e}")
        return {}


def load_release_json(repo_root):
    """
    Load release.json — a manually maintained list of large packages
    hosted on GitHub Releases.

    Format of release.json:
    [
      {
        "file": "gradle_9.3.1_all.deb",
        "url":  "https://github.com/devjhr/pkg-repo/releases/download/v1.0/gradle_9.3.1_all.deb",
        "size": 135056252,
        "md5":  "65af3c15dc001bf29af0447441e6711a",
        "sha256": "5e9dfcd7cb5e7f224524a723033f0fa020a138c933dd538944e6cd1ec4fd9e36"
      }
    ]
    Run with --add-release to generate entries automatically from a .deb file.
    """
    rj = repo_root / 'release.json'
    if not rj.exists():
        return []
    try:
        entries = json.loads(rj.read_text(encoding='utf-8'))
        print(f"  Loaded release.json ({len(entries)} large package(s))")
        return entries
    except Exception as e:
        print(f"  WARNING: Failed to parse release.json: {e}")
        return []


def add_to_release_json(repo_root, deb_path, url):
    """
    Compute checksums for a large .deb and add/update its entry in release.json.
    Called by --add-release flag.
    """
    deb_path = Path(deb_path)
    if not deb_path.exists():
        print(f"  ERROR: File not found: {deb_path}")
        return False

    print(f"  Computing checksums for {deb_path.name} ...")
    size   = deb_path.stat().st_size
    md5    = hash_file(str(deb_path), 'md5')
    sha256 = hash_file(str(deb_path), 'sha256')

    # Read control info
    ctrl = control_file_contents(str(deb_path))
    info = parse_control(ctrl) if ctrl else {}

    entry = {
        "file":    deb_path.name,
        "url":     url,
        "size":    size,
        "md5":     md5,
        "sha256":  sha256,
        "package": info.get('Package', deb_path.stem.split('_')[0]),
        "version": info.get('Version', ''),
        "arch":    info.get('Architecture', 'all'),
        "control": ctrl or "",
    }

    rj   = repo_root / 'release.json'
    data = []
    if rj.exists():
        try:
            data = json.loads(rj.read_text(encoding='utf-8'))
        except Exception:
            data = []

    # Replace existing entry for same file or append
    replaced = False
    for i, e in enumerate(data):
        if e.get('file') == deb_path.name:
            data[i] = entry
            replaced = True
            break
    if not replaced:
        data.append(entry)

    rj.write_text(json.dumps(data, indent=2), encoding='utf-8')
    print(f"  {'Updated' if replaced else 'Added'} {deb_path.name} in release.json")
    print(f"    URL    : {url}")
    print(f"    Size   : {size:,} bytes ({size//1024//1024} MB)")
    print(f"    MD5    : {md5}")
    print(f"    SHA256 : {sha256}")
    return True


def build_packages(pool_dir, repo_root, release_urls=None):
    """Scan pool/main for .deb files and generate Packages, Packages.gz, packages.json.
    release_urls: dict { filename: url } for large files manually uploaded to GitHub Releases.
    """
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

    # Remove old versions — keep only the newest .deb per package folder
    # A package folder is pool/main/m/micro/ — all debs inside are versions of same pkg
    pkg_dirs = set(deb.parent for deb in deb_files)
    for pkg_dir in pkg_dirs:
        all_debs = sorted(pkg_dir.glob('*.deb'))
        if len(all_debs) > 1:
            # Sort by modification time, keep newest
            all_debs.sort(key=lambda f: f.stat().st_mtime)
            for old_deb in all_debs[:-1]:
                old_deb.unlink()
                print(f"  Removed old version: {old_deb.name}")
            # Refresh deb_files list
    deb_files = sorted([
        Path(p) for p in glob.glob(str(pool_dir / '**' / '*.deb'), recursive=True)
    ])

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

        size          = deb.stat().st_size
        is_large      = size >= LARGE_FILE_THRESHOLD

        # Large files: use Release URL as Filename, will be deleted from pool/ after indexing
        if is_large and release_urls and deb.name in release_urls:
            filename_path = release_urls[deb.name]
            print(f"  Large {deb.name} ({size//1024//1024} MB) → Release URL")
        else:
            filename_path = 'pool/main/' + '/'.join(parts)
            if is_large:
                print(f"  Large {deb.name} ({size//1024//1024} MB) → pool/ (no release URL — add --release TAG)")

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
            'release': is_large and release_urls and deb.name in release_urls,
        })

    # ── Process release.json entries (large files on GitHub Releases) ──────────
    release_entries = load_release_json(repo_root)
    for re in release_entries:
        filename  = re.get('file', '')
        url       = re.get('url', '')
        size      = re.get('size', 0)
        ctrl_text = re.get('control', '')

        if not url:
            print(f"  WARNING: release.json entry '{filename}' has no url — skipping")
            continue

        info = parse_control(ctrl_text) if ctrl_text else {}
        pkg_name = re.get('package') or info.get('Package', filename.split('_')[0])
        pkg_ver  = re.get('version') or info.get('Version', '')
        pkg_arch = re.get('arch')    or info.get('Architecture', 'all')

        print(f"  Release {filename} ({size//1024//1024} MB) → {url}")
        encountered_arches.add(pkg_arch)

        PRIORITY_KEYS_R = [
            'Package', 'Version', 'Architecture', 'Maintainer',
            'Installed-Size', 'Depends', 'Pre-Depends', 'Recommends',
            'Suggests', 'Conflicts', 'Breaks', 'Replaces', 'Provides',
            'Homepage', 'Description'
        ]
        block = []
        added = set()
        if ctrl_text:
            parsed = parse_control(ctrl_text)
            for key in PRIORITY_KEYS_R:
                if key in parsed:
                    block.append(f"{key}: {parsed[key]}")
                    added.add(key)
            for key, val in parsed.items():
                if key not in added and key not in COMPUTED:
                    block.append(f"{key}: {val}")
        else:
            block.append(f"Package: {pkg_name}")
            block.append(f"Version: {pkg_ver}")
            block.append(f"Architecture: {pkg_arch}")

        block.append(f"Filename: {url}")
        block.append(f"Size: {size}")
        block.append(f"MD5Sum: {re.get('md5', '')}")
        block.append(f"SHA256: {re.get('sha256', '')}")
        entries.append('\n'.join(block))

        # Add to packages.json
        letter = pkg_name[0].lower() if pkg_name else 'z'
        folder_map.setdefault(letter, {}).setdefault(pkg_name, []).append({
            'name':    filename,
            'path':    url,
            'size':    size,
            'package': pkg_name,
            'version': pkg_ver,
            'desc':    info.get('Description', '').split('\n')[0],
            'release': True,
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
    """Copy .deb files from input_path into pool/main/letter/pkgname/.
    Old versions of the same package are automatically removed."""
    debs = sorted(Path(input_path).glob('*.deb'))
    if not debs:
        sys.exit(f"No .deb files found in {input_path}")

    copied = skipped = replaced = 0
    for deb in debs:
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

        # Remove any existing .deb for this package (old versions)
        existing = [f for f in target_dir.glob('*.deb') if f != target]
        for old_deb in existing:
            old_deb.unlink()
            print(f"  Removed {letter}/{pkgname}/{old_deb.name}")
            replaced += 1

        # Skip if exact same file already exists
        if target.exists() and target.stat().st_size == deb.stat().st_size:
            skipped += 1
            continue

        shutil.copy2(str(deb), str(target))
        size_mb = deb.stat().st_size / 1024 / 1024
        flag = " [LARGE]" if deb.stat().st_size >= LARGE_FILE_THRESHOLD else ""
        print(f"  Copied  {letter}/{pkgname}/{deb.name} ({size_mb:.1f} MB){flag}")
        copied += 1

    print(f"\n  Copied: {copied}   Replaced: {replaced}   Skipped: {skipped} (unchanged)")


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



def cleanup_large_files(pool_dir):
    """
    Delete large .deb files from pool/ after they have been indexed.
    They are hosted on GitHub Releases so they don't need to be in the repo.
    """
    import glob as _glob
    deb_files = [
        Path(p) for p in _glob.glob(str(pool_dir / '**' / '*.deb'), recursive=True)
    ]
    removed = 0
    for deb in deb_files:
        if deb.stat().st_size >= LARGE_FILE_THRESHOLD:
            size_mb = deb.stat().st_size / 1024 / 1024
            # Also remove empty parent folder if it becomes empty
            pkg_dir = deb.parent
            deb.unlink()
            print(f"  Deleted {deb.relative_to(pool_dir)} ({size_mb:.1f} MB)")
            # Remove empty pkg folder and letter folder
            try:
                pkg_dir.rmdir()
                pkg_dir.parent.rmdir()
            except OSError:
                pass  # not empty, that's fine
            removed += 1
    if removed:
        print(f"  Cleaned {removed} large file(s) from pool/")
    else:
        print(f"  No large files in pool/ to clean")
    return removed


def main():
    parser = argparse.ArgumentParser(description='AndroStudio Repo Generator v3.0')
    parser.add_argument('--input', '-i', default=None,
                        help='Import .deb files from this folder into pool/main/ first')
    parser.add_argument('--repo', '-r', default=str(Path(__file__).parent),
                        help='Repo root folder (default: script directory)')
    parser.add_argument('--release', '-R', default=None, metavar='TAG',
                        help='GitHub Release tag where large .debs were manually uploaded (e.g. v1.0)')
    parser.add_argument('--add-release', nargs=2, metavar=('DEB', 'URL'),
                        help='Add a large .deb to release.json: --add-release file.deb https://...')
    parser.add_argument('--no-sign', action='store_true',
                        help='Skip GPG signing')
    args = parser.parse_args()

    repo_root = Path(args.repo).resolve()
    pool_dir  = repo_root / 'pool' / 'main'

    print()
    print("╔══════════════════════════════════════════╗")
    print("║   AndroStudio Repo Generator  v3.0       ║")
    print("╚══════════════════════════════════════════╝")
    print()
    print(f"  Repo    : {repo_root}")
    print(f"  Pool    : {pool_dir}")
    print(f"  Release : {args.release or '(none — use --release TAG for large files)'}")
    print()

    # Handle --add-release: register a large file in release.json then exit
    if args.add_release:
        deb_path, url = args.add_release
        print()
        print("AddRelease  Registering large package ...")
        add_to_release_json(repo_root, deb_path, url)
        print()
        print("Done  release.json updated.")
        print("      Now run: python generate_repo.py")
        print()
        return

    if args.input:
        print(f"Import  Importing from {args.input} ...")
        import_debs(args.input, pool_dir)
        print()

    # Fetch release asset URLs if --release tag given
    release_urls = {}
    if args.release:
        print(f"Release Fetching asset URLs from release '{args.release}' ...")
        release_urls = fetch_release_urls(args.release)
        print()

    print("Build   Scanning pool/main/ ...")
    bin_dir, pkg_file, pkggz, arches = build_packages(pool_dir, repo_root, release_urls)

    print()
    print("Build   Generating Release ...")
    build_release(repo_root, bin_dir, pkg_file, pkggz, arches)

    if not args.no_sign:
        print()
        print("Sign    Signing Release with GPG ...")
        sign_release(repo_root)

    # ── Cleanup: delete large files from pool/ (they live on GitHub Releases) ──
    print()
    print("Clean   Removing large files from pool/ ...")
    cleanup_large_files(pool_dir)

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


