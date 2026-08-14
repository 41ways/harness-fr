#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════
#  더블클릭으로 실행되는 맥 앱(.app) 만들기
#
#  설명    : 터미널을 안 열고도 쓸 수 있게 앱 번들을 만듦. 실행하면 서버를
#            띄우고 대시보드를 브라우저로 열어줌. 아이콘은 배트 svg에서 뽑음
#  실행    : bash tools/make_app.sh
#  작성자  : 정한결
#  변경내역:
#    v1 - 앱 번들 + icns 아이콘 생성
#  이슈 사항:
#    - 코드 서명은 안 함. 서명 없는 앱이라 처음 열 때 우클릭 > 열기로 한 번
#      허용해줘야 함. 개인 장난감에 개발자 인증서를 붙일 이유가 없음
#    - 앱 안에 절대경로를 안 박음. 번들이 리포 안에 있다는 전제로 상대경로만
#      씀 — 그래야 리포를 통째로 옮기거나 clone해도 그대로 동작함
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$ROOT/Clanker Bat.app"
CONTENTS="$APP/Contents"

rm -rf "$APP"
mkdir -p "$CONTENTS/MacOS" "$CONTENTS/Resources"

# ── 실행 스크립트 ──────────────────────────────────────────────────────────
cat > "$CONTENTS/MacOS/launch" <<'LAUNCH'
#!/bin/bash
# 번들이 리포 안에 있으므로 세 단계 위가 리포 루트
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$ROOT" || exit 1

PY="$(command -v python3 || true)"
if [ -z "$PY" ]; then
  osascript -e 'display alert "python3이 없음" message "Xcode 명령줄 도구를 설치해줘: xcode-select --install"'
  exit 1
fi

up() { curl -sk -o /dev/null --max-time 1 "https://127.0.0.1:8443/state"; }

STARTED=0
if ! up; then
  "$PY" run.py > /tmp/clanker-bat.log 2>&1 &
  STARTED=$!
  for _ in $(seq 1 60); do up && break; sleep 0.25; done
fi

if ! up; then
  # 따옴표가 섞이면 osascript가 깨져서 미리 제거함
  ERR=$(tail -3 /tmp/clanker-bat.log | tr -d '"')
  osascript -e "display alert \"서버가 안 뜸\" message \"$ERR\""
  exit 1
fi

IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo 127.0.0.1)"
open "https://$IP:8443/dash"

osascript <<OSA
display dialog "빠따 서버가 돌고 있어.

아이폰(빠따):
https://$IP:8443

맥(대시보드)은 브라우저에 열어놨어." ¬
  with title "Clanker Bat" buttons {"종료"} default button "종료" with icon note
OSA

# 이 앱이 띄운 서버만 정리함. 원래 돌고 있던 걸 껐다가 남의 작업을 끊으면 곤란
if [ "$STARTED" != "0" ]; then kill "$STARTED" 2>/dev/null || true; fi
LAUNCH
chmod +x "$CONTENTS/MacOS/launch"

# ── Info.plist ────────────────────────────────────────────────────────────
cat > "$CONTENTS/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Clanker Bat</string>
  <key>CFBundleDisplayName</key><string>Clanker Bat</string>
  <key>CFBundleIdentifier</key><string>local.clanker.bat</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>launch</string>
  <key>CFBundleIconFile</key><string>icon</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

# ── 아이콘 (배트 svg → png → icns) ─────────────────────────────────────────
TMP="$(mktemp -d)"
python3 - "$ROOT" "$TMP" <<'PY'
import re, sys, pathlib
root, tmp = sys.argv[1], sys.argv[2]
html = pathlib.Path(root, "static", "bat.html").read_text()
svg = re.search(r"<svg viewBox.*?</svg>", html, re.S).group(0)
svg = svg.replace('<svg viewBox="0 0 200 200"',
                  '<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="-20 0 240 200"')
# 아이콘은 배경이 있어야 도크에서 안 붕 뜸. 배트는 살짝 눕혀서 야구 느낌을 냄
svg = svg.replace(">", '><rect x="-20" width="240" height="200" rx="34" fill="#14110e"/>'
                       '<g transform="rotate(-28 100 100)">', 1)
svg = svg.replace("</svg>", "</g></svg>")
pathlib.Path(tmp, "icon.svg").write_text(svg)
PY

qlmanage -t -s 1024 -o "$TMP" "$TMP/icon.svg" >/dev/null 2>&1
BASE="$TMP/icon.svg.png"
if [ -f "$BASE" ]; then
  ICONSET="$TMP/icon.iconset"; mkdir -p "$ICONSET"
  for s in 16 32 128 256 512; do
    sips -z $s $s "$BASE" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null 2>&1
    sips -z $((s*2)) $((s*2)) "$BASE" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null 2>&1
  done
  iconutil -c icns "$ICONSET" -o "$CONTENTS/Resources/icon.icns" 2>/dev/null || true
fi
rm -rf "$TMP"

# 파인더가 아이콘 캐시를 다시 읽게 함
touch "$APP"

echo "만듦: $APP"
[ -f "$CONTENTS/Resources/icon.icns" ] && echo "아이콘: 있음" || echo "아이콘: 없음 (기본 아이콘으로 뜸)"
