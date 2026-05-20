#!/bin/bash
# ╔══════════════════════════════════════════════════════════════╗
# ║   build-androstudio-deb.sh — Build AndroStudio meta package  ║
# ╚══════════════════════════════════════════════════════════════╝
#
# Usage:
#   Place this file and 'androstudio' CLI in the same folder
#   chmod +x build-androstudio-deb.sh androstudio
#   ./build-androstudio-deb.sh

set -e

PKG_NAME="androstudio"
PKG_VERSION="2.4"
PKG_ARCH="all"
PKG_MAINTAINER="AndroStudio <repo@androstudio.dev>"
PKG_DEPENDS="git, which, curl, dpkg"
PKG_DESCRIPTION="AndroStudio IDE environment setup
 Installs and configures all required dependencies for
 AndroStudio IDE on Android. Downloads openjdk-21, gradle
 from GitHub Releases and sets up ANDROID_HOME, JAVA_HOME."
PKG_HOMEPAGE="https://devjhr.github.io/pkg-repo"
PREFIX="/data/data/com.jahangir/files/usr"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="$SCRIPT_DIR/build-androstudio"
PKG_DIR="$BUILD_DIR/pkg"
DEB_NAME="${PKG_NAME}_${PKG_VERSION}_${PKG_ARCH}.deb"

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║   Building AndroStudio .deb package v$PKG_VERSION                        ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "  Build dir : $BUILD_DIR"
echo "  Output    : $SCRIPT_DIR/$DEB_NAME"
echo "  Depends   : $PKG_DEPENDS"
echo ""

if [ ! -f "$SCRIPT_DIR/androstudio" ]; then
    echo "  ERROR: 'androstudio' CLI script not found in $SCRIPT_DIR"
    exit 1
fi

rm -rf "$BUILD_DIR"
mkdir -p "$PKG_DIR/DEBIAN"
mkdir -p "$PKG_DIR${PREFIX}/bin"
mkdir -p "$PKG_DIR${PREFIX}/etc/androstudio"
mkdir -p "$PKG_DIR${PREFIX}/share/doc/androstudio"

echo "  Copying androstudio CLI..."
cp "$SCRIPT_DIR/androstudio" "$PKG_DIR${PREFIX}/bin/androstudio"
echo "  OK  androstudio → $PREFIX/bin/androstudio"

# ── Write postinst ────────────────────────────────────────────
echo "  Writing postinst..."
cat > "$PKG_DIR/DEBIAN/postinst" << 'POSTINST'
#!/bin/bash
RELEASE_BASE="https://github.com/devjhr/pkg-repo/releases/download/v1.0"

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║   AndroStudio — Setting up environment...                ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# ── Detect shell config ───────────────────────────────────────
if [ -f "$HOME/.zshrc" ]; then
    SHELL_RC="$HOME/.zshrc"
else
    SHELL_RC="$HOME/.bashrc"
    touch "$SHELL_RC"
fi
echo "  Shell config : $SHELL_RC"

# ── Create android-sdk dir ────────────────────────────────────
mkdir -p "$HOME/android-sdk"
echo "  ANDROID_HOME : $HOME/android-sdk"
echo "  JAVA_HOME    : auto-detected via readlink + which"

# ── Write env block ───────────────────────────────────────────
sed -i '/# ── AndroStudio Environment/,/# ── End AndroStudio/d' "$SHELL_RC" 2>/dev/null
cat >> "$SHELL_RC" << 'ENV'

# ── AndroStudio Environment ────────────────────────────────
export ANDROID_HOME="$HOME/android-sdk"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export PATH="$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$PATH"

export JAVA_HOME="$(dirname $(dirname $(readlink -f $(which java))))"
export PATH="$JAVA_HOME/bin:$PATH"

for ver in $(ls -r $ANDROID_HOME/build-tools/ 2>/dev/null); do
    export PATH="$ANDROID_HOME/build-tools/$ver:$PATH"
done
# ── End AndroStudio ─────────────────────────────────────────
ENV
echo "  Environment variables written to $SHELL_RC"

# ── Install large packages from GitHub Releases ───────────────
echo ""
echo "  Installing packages from GitHub Releases..."
echo "  ────────────────────────────────────────────"

install_pkg() {
    local name="$1" url="$2"
    local file
    file=$(basename "$url")

    if dpkg -l "$name" &>/dev/null 2>&1; then
        printf "  \033[0;32m✓\033[0m %-16s already installed\n" "$name"
        return
    fi

    printf "  \033[0;34m→\033[0m %-16s downloading...\n" "$name"
    curl -L --progress-bar "$url" -o "$TMPDIR/$file"

    if [ ! -s "$TMPDIR/$file" ]; then
        printf "  \033[0;31m✗\033[0m %-16s download failed\n" "$name"
        rm -f "$TMPDIR/$file"
        return
    fi

    apt install -y "$TMPDIR/$file" > /dev/null 2>&1 \
        && printf "  \033[0;32m✓\033[0m %-16s installed\n" "$name" \
        || printf "  \033[0;31m✗\033[0m %-16s install failed\n" "$name"
    rm -f "$TMPDIR/$file"
}

install_pkg "openjdk-21" "$RELEASE_BASE/openjdk-21_21.0.10_aarch64.deb"
install_pkg "openjdk-17" "$RELEASE_BASE/openjdk-17_17.0.18_aarch64.deb"
install_pkg "gradle"     "$RELEASE_BASE/gradle_9.3.1_all.deb"

# ── Apply env to current session ──────────────────────────────
export ANDROID_HOME="$HOME/android-sdk"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export PATH="$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$PATH"
export JAVA_HOME="$(dirname $(dirname $(readlink -f $(which java))) 2>/dev/null)"
export PATH="$JAVA_HOME/bin:$PATH"

# ── Verify ────────────────────────────────────────────────────
echo ""
echo "  Verifying tools..."
check() {
    local cmd="$1" name="$2"
    if command -v "$cmd" &>/dev/null; then
        local ver; ver=$("$cmd" --version 2>/dev/null | head -1 || echo "ok")
        printf "  \033[0;32m✓\033[0m %-12s %s\n" "$name" "$ver"
    else
        printf "  \033[0;31m✗\033[0m %-12s not found\n" "$name"
    fi
}
check java   "Java"
check gradle "Gradle"
check aapt2  "aapt2"
check git    "Git"

echo ""
printf "  \033[0;32m✓\033[0m AndroStudio environment ready!\n"
echo ""
echo "  ┌─────────────────────────────────────────────────────┐"
echo "  │  Getting started:                                   │"
echo "  │                                                     │"
echo "  │  1. source ~/.bashrc                                │"
echo "  │  2. androstudio setup       ← download Android SDK │"
echo "  │  3. androstudio install ndk ← install NDK + cmake  │"
echo "  │                                                     │"
echo "  │  Other commands:                                    │"
echo "  │    androstudio install pkg --list  ← release pkgs  │"
echo "  │    androstudio status              ← installed tools│"
echo "  │    androstudio doctor              ← check issues   │"
echo "  │    androstudio -help               ← all commands   │"
echo "  └─────────────────────────────────────────────────────┘"
echo ""
POSTINST

# ── Write prerm ───────────────────────────────────────────────
echo "  Writing prerm..."
cat > "$PKG_DIR/DEBIAN/prerm" << 'PRERM'
#!/bin/bash
echo "  Removing AndroStudio environment config..."
for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
    if [ -f "$rc" ]; then
        sed -i '/# ── AndroStudio Environment/,/# ── End AndroStudio/d' "$rc"
        echo "  Cleaned $rc"
    fi
done
PRERM

# ── Write control file ────────────────────────────────────────
echo "  Writing control file..."
INSTALLED_SIZE=$(du -sk "$PKG_DIR${PREFIX}" | cut -f1)
cat > "$PKG_DIR/DEBIAN/control" << EOF
Package: $PKG_NAME
Version: $PKG_VERSION
Architecture: $PKG_ARCH
Maintainer: $PKG_MAINTAINER
Installed-Size: $INSTALLED_SIZE
Depends: $PKG_DEPENDS
Homepage: $PKG_HOMEPAGE
Description: $PKG_DESCRIPTION
EOF

# ── Fix permissions ───────────────────────────────────────────
chmod 755 "$PKG_DIR/DEBIAN"
find "$PKG_DIR" -type d | xargs chmod 755
find "$PKG_DIR" -type f | xargs chmod 644
chmod 755 "$PKG_DIR/DEBIAN/postinst"
chmod 755 "$PKG_DIR/DEBIAN/prerm"
chmod 755 "$PKG_DIR${PREFIX}/bin/androstudio"

# ── Build .deb ────────────────────────────────────────────────
echo "  Building .deb..."
cd "$SCRIPT_DIR"
dpkg-deb --build --root-owner-group "$PKG_DIR" "$DEB_NAME"

# ── Result ────────────────────────────────────────────────────
if [ -f "$DEB_NAME" ]; then
    SIZE=$(du -sh "$DEB_NAME" | cut -f1)
    echo ""
    echo "  ✓ Built : $DEB_NAME ($SIZE)"
    echo ""
    echo "  Control:"
    dpkg-deb -I "$DEB_NAME" | sed 's/^/    /'
    echo ""
    echo "  Next steps:"
    echo "    ~/pkg-add.sh $SCRIPT_DIR/$DEB_NAME"
    echo "    python ~/pkg-repo/generate_repo.py --input ~/pkg"
    echo "    git -C ~/pkg-repo add . && git -C ~/pkg-repo commit -m 'androstudio v1.2' && git -C ~/pkg-repo push"
    echo ""
else
    echo "  ERROR: Build failed!"
    exit 1
fi

rm -rf "$BUILD_DIR"

