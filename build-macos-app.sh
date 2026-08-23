#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
APP="${1:-$HOME/Desktop/WorkBuddy 会话港.app}"

# 删旧留新：目标已存在时自动移到废纸篓（可恢复），保证桌面只保留最新版。
if [[ -e "$APP" ]]; then
  TRASH_NAME="$(basename "$APP" .app)-$(date +%s).app"
  TRASH_DEST="$HOME/.Trash/$TRASH_NAME"
  print -u2 "已存在旧版，移到废纸篓：$TRASH_DEST"
  mv "$APP" "$TRASH_DEST"
fi

CONTENTS="$APP/Contents"
RESOURCES="$CONTENTS/Resources"
ICONSET="$(mktemp -d)/AppIcon.iconset"
trap 'rm -rf "${ICONSET:h}"' EXIT
mkdir -p "$CONTENTS/MacOS" "$RESOURCES" "$ICONSET"

swiftc "$ROOT/macos/WorkBuddySyncApp.swift" -o "$CONTENTS/MacOS/WorkBuddySessionHarbor" -framework AppKit -framework WebKit
swiftc "$ROOT/macos/MakeIcon.swift" -o "${ICONSET:h}/make-icon" -framework AppKit
"${ICONSET:h}/make-icon" "${ICONSET:h}/AppIcon.png"
for spec in "16x16:16" "16x16@2x:32" "32x32:32" "32x32@2x:64" "128x128:128" "128x128@2x:256" "256x256:256" "256x256@2x:512" "512x512:512" "512x512@2x:1024"; do
  name="${spec%%:*}"
  pixels="${spec##*:}"
  sips -z "$pixels" "$pixels" "${ICONSET:h}/AppIcon.png" --out "$ICONSET/icon_${name}.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$RESOURCES/AppIcon.icns"
cp "$ROOT/workbuddy-sync-app.py" "$RESOURCES/workbuddy-sync-app.py"
cp -R "$ROOT/platforms" "$RESOURCES/platforms"
find "$RESOURCES/platforms" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
cp "$ROOT/macos/Info.plist" "$CONTENTS/Info.plist"
codesign --force --deep --sign - "$APP" >/dev/null
plutil -lint "$CONTENTS/Info.plist" >/dev/null
codesign --verify --deep --strict "$APP"
print "已生成：$APP"
