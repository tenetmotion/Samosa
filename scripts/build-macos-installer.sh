#!/bin/bash
set -euo pipefail

VERSION="${1:-1.2.0}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="$ROOT/dist/installer"
WORK="$ROOT/dist/macos-build"
PAYLOAD="$WORK/payload"
APP_DIR="$PAYLOAD/Applications/Samosa"
RESOURCES="$APP_DIR/Resources"
PKG_ID="com.tenet.samosa"

command -v pkgbuild >/dev/null || { echo "pkgbuild is required (run this on macOS)." >&2; exit 1; }
command -v productbuild >/dev/null || { echo "productbuild is required (run this on macOS)." >&2; exit 1; }

rm -rf "$WORK"
mkdir -p "$RESOURCES" "$DIST" "$WORK/pkg-scripts" "$WORK/product-resources"
cp -R "$ROOT/panel" "$RESOURCES/panel"
rm -f "$RESOURCES/panel/config.json"
find "$RESOURCES/panel" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$RESOURCES/panel" -type f -name '*.pyc' -delete
cp -R "$ROOT/backend" "$RESOURCES/backend"
find "$RESOURCES/backend" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$RESOURCES/backend" -type f -name '*.pyc' -delete
mkdir -p "$RESOURCES/installer/macos"
cp "$ROOT/installer/download_models.py" "$RESOURCES/installer/download_models.py"
cp "$ROOT/installer/macos/bootstrap.sh" "$RESOURCES/installer/macos/bootstrap.sh"
cp "$ROOT/installer/macos/manage-models.sh" "$RESOURCES/installer/macos/manage-models.sh"
cp "$ROOT/LICENSE" "$ROOT/NOTICE.md" "$ROOT/THIRD_PARTY_NOTICES.md" "$ROOT/README.md" "$RESOURCES/"
cp -R "$ROOT/docs" "$RESOURCES/docs"
chmod +x "$RESOURCES/installer/macos/"*.sh

cat > "$APP_DIR/Manage Model Packs.command" <<'EOF'
#!/bin/bash
clear
/bin/bash "/Applications/Samosa/Resources/installer/macos/manage-models.sh" --install-root "$HOME/Library/Application Support/Samosa"
status=$?
echo
read -r -p "Press Return to close..." _
exit $status
EOF

cat > "$APP_DIR/Uninstall Samosa.command" <<'EOF'
#!/bin/bash
clear
/bin/bash "$HOME/Library/Application Support/Samosa/installer/macos/bootstrap.sh" --uninstall
status=$?
if [[ $status -eq 0 ]]; then
  /usr/bin/osascript -e 'do shell script "/bin/rm -rf /Applications/Samosa" with administrator privileges'
fi
echo
read -r -p "Press Return to close..." _
exit $status
EOF

chmod +x "$APP_DIR/"*.command
cp "$ROOT/installer/macos/pkg-scripts/postinstall" "$WORK/pkg-scripts/postinstall"
chmod +x "$WORK/pkg-scripts/postinstall"

pkgbuild \
  --root "$PAYLOAD" \
  --scripts "$WORK/pkg-scripts" \
  --identifier "$PKG_ID" \
  --version "$VERSION" \
  --install-location / \
  "$WORK/Samosa-component.pkg"

cp "$ROOT/LICENSE" "$WORK/product-resources/LICENSE"
cat > "$WORK/product-resources/README.html" <<EOF
<!doctype html><html><body><h1>Samosa $VERSION</h1><p>Samosa installs an After Effects CEP panel and a per-user Sammie-Roto-2 runtime.</p><p>Standard installation includes SAM2 Base. Other model packs download when first requested or through <strong>Applications &gt; Samosa &gt; Manage Model Packs</strong>.</p><p>This package is unsigned. Optional model packs have additional or noncommercial terms described in THIRD_PARTY_NOTICES.md.</p></body></html>
EOF
cat > "$WORK/distribution.xml" <<EOF
<?xml version="1.0" encoding="utf-8"?>
<installer-gui-script minSpecVersion="2">
  <title>Samosa $VERSION</title>
  <organization>com.tenet</organization>
  <domains enable_localSystem="true" enable_currentUserHome="false" enable_anywhere="false"/>
  <options customize="never" require-scripts="false" hostArchitectures="x86_64,arm64"/>
  <license file="LICENSE"/>
  <readme file="README.html"/>
  <choices-outline><line choice="default"/></choices-outline>
  <choice id="default" visible="false"><pkg-ref id="$PKG_ID"/></choice>
  <pkg-ref id="$PKG_ID" version="$VERSION" onConclusion="none">Samosa-component.pkg</pkg-ref>
</installer-gui-script>
EOF

productbuild \
  --distribution "$WORK/distribution.xml" \
  --resources "$WORK/product-resources" \
  --package-path "$WORK" \
  "$DIST/Samosa-$VERSION-macOS.pkg"

PACKAGE_NAME="Samosa-$VERSION-macOS.pkg"
PACKAGE_HASH="$(shasum -a 256 "$DIST/$PACKAGE_NAME" | awk '{print toupper($1)}')"
printf '%s  %s\n' "$PACKAGE_HASH" "$PACKAGE_NAME" > "$DIST/Samosa-$VERSION-macOS-SHA256.txt"
pkgutil --check-signature "$DIST/Samosa-$VERSION-macOS.pkg" || true
echo "Package: $DIST/Samosa-$VERSION-macOS.pkg"
cat "$DIST/Samosa-$VERSION-macOS-SHA256.txt"
