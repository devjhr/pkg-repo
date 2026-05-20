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
PKG_DEPENDS="git, which, curl, dpkg, aapt2"
PKG_DESCRIPTION="AndroStudio IDE environment setup
 Installs and configures all required dependencies for
 AndroStudio IDE on Android. Use 'androstudio setup' to
 download openjdk-21, gradle, Android SDK, platform and
 build-tools and set up ANDROID_HOME and JAVA_HOME."
PKG_HOMEPAGE="https://devjhr.github.io/pkg-repo"
PREFIX="/data/data/com.jahangir/files/usr"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUTPUT_DIR="$(dirname "$SCRIPT_DIR")/pkg"
BUILD_DIR="$SCRIPT_DIR/build-androstudio"
PKG_DIR="$BUILD_DIR/pkg"
DEB_NAME="${PKG_NAME}_${PKG_VERSION}_${PKG_ARCH}.deb"

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║   Building AndroStudio .deb package v$PKG_VERSION                        ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "  Build dir : $BUILD_DIR"
echo "  Output    : $OUTPUT_DIR/$DEB_NAME"
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
echo ""
echo "  ✓ AndroStudio v2.4 installed successfully!"
echo ""
echo "  ┌─────────────────────────────────────────────────────┐"
echo "  │  Getting started:                                   │"
echo "  │                                                     │"
echo "  │  androstudio setup       ← full environment setup  │"
echo "  │  androstudio install ndk ← install NDK + cmake     │"
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
mkdir -p "$OUTPUT_DIR"
dpkg-deb --build --root-owner-group "$PKG_DIR" "$OUTPUT_DIR/$DEB_NAME"

# ── Result ────────────────────────────────────────────────────
if [ -f "$OUTPUT_DIR/$DEB_NAME" ]; then
    SIZE=$(du -sh "$OUTPUT_DIR/$DEB_NAME" | cut -f1)
    echo ""
    echo "  ✓ Built : $OUTPUT_DIR/$DEB_NAME ($SIZE)"
    echo ""
    echo "  Control:"
    dpkg-deb -I "$OUTPUT_DIR/$DEB_NAME" | sed 's/^/    /'
    echo ""
    echo "  Next steps:"
    echo "    python $(dirname "$SCRIPT_DIR")/generate_repo.py --input $(dirname "$SCRIPT_DIR")/pkg"
    echo "    git -C $(dirname "$SCRIPT_DIR") add . && git -C $(dirname "$SCRIPT_DIR") commit -m 'androstudio v$PKG_VERSION' && git -C $(dirname "$SCRIPT_DIR") push"
    echo ""
else
    echo "  ERROR: Build failed!"
    exit 1
fi

rm -rf "$BUILD_DIR"

