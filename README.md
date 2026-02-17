# AndroStudio Package Repository

Official `.deb` package repository for [AndroStudio](https://github.com/devjhr) — an Android IDE with a built-in Termux terminal.

## 📦 Add this repo to your terminal

```bash
pkg repo add https://devjhr.github.io/pkg-repo
pkg update
```

## 🔧 Install packages

```bash
pkg install git
pkg install python
pkg install nano
pkg install curl
```

## 📁 Repository Structure

```
/
├── index.html                          ← Landing page
├── dists/
│   └── stable/
│       ├── Release                     ← Repo metadata & checksums
│       └── main/
│           └── binary-aarch64/
│               ├── Packages            ← Package index
│               └── Packages.gz         ← Compressed index
└── pool/
    └── main/
        └── *.deb                       ← Actual .deb packages go here
```

## ➕ Adding a new package

1. Build your `.deb` for `aarch64`
2. Place it in `pool/main/`
3. Add a new entry in `dists/stable/main/binary-aarch64/Packages`
4. Regenerate `Packages.gz`: `gzip -k Packages`
5. Update checksums in `dists/stable/Release`
6. Push to GitHub

## 📐 Architecture

- `aarch64` (ARM 64-bit) — built for modern Android devices

## 🔗 Links

- Landing page: https://devjhr.github.io/pkg-repo
- GitHub: https://github.com/devjhr
