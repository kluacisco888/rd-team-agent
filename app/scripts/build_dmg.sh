#!/usr/bin/env bash
# Build "研发团队 Agent.app" (universal, ad-hoc signed) and package it as a DMG.
# Needs only the Xcode Command Line Tools. Output: dist/RD-Team-Agent-<version>.dmg
set -euo pipefail

VERSION="0.1.0"
APP_NAME="研发团队 Agent"
BUNDLE_ID="com.dengwj.rd-team-agent"
EXECUTABLE="RDTeamAgentMac"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST="$ROOT/dist"
APP="$DIST/$APP_NAME.app"
DMG="$DIST/RD-Team-Agent-$VERSION.dmg"
JOBS="${JOBS:-2}"
ICON="$ROOT/Resources/AppIcon.icns"

cd "$ROOT"
[[ -f "$ICON" ]] || { echo "missing $ICON; run: swift scripts/make_icon.swift" >&2; exit 1; }
echo "== tests"
swift test -j "$JOBS" --quiet

echo "== release build (arm64 + x86_64)"
swift build -c release -j "$JOBS" --arch arm64 --arch x86_64
BIN_DIR="$(swift build -c release --arch arm64 --arch x86_64 --show-bin-path)"
BINARY="$BIN_DIR/$EXECUTABLE"
lipo -info "$BINARY"

echo "== assemble $APP_NAME.app"
# dist/ holds only this script's own output, so it is rebuilt from scratch.
[[ "$DIST" == "$ROOT/dist" ]] || { echo "unexpected dist path: $DIST" >&2; exit 1; }
rm -rf "$DIST"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BINARY" "$APP/Contents/MacOS/$EXECUTABLE"
cp "$ICON" "$APP/Contents/Resources/AppIcon.icns"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key><string>zh_CN</string>
  <key>CFBundleExecutable</key><string>$EXECUTABLE</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>CFBundleName</key><string>$APP_NAME</string>
  <key>CFBundleDisplayName</key><string>$APP_NAME</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.productivity</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSSupportsAutomaticTermination</key><false/>
</dict>
</plist>
PLIST
plutil -lint "$APP/Contents/Info.plist"

echo "== ad-hoc sign"
codesign --force --sign - --timestamp=none "$APP"
codesign --verify --strict --verbose=2 "$APP"

echo "== dmg"
STAGING="$DIST/dmg-staging"
mkdir -p "$STAGING"
cp -R "$APP" "$STAGING/"
ln -s /Applications "$STAGING/Applications"
hdiutil create -volname "$APP_NAME" -srcfolder "$STAGING" -fs HFS+ -format UDZO -ov "$DMG" >/dev/null
rm -rf "$STAGING"
hdiutil verify "$DMG" >/dev/null
shasum -a 256 "$DMG"
ls -lh "$DMG"
