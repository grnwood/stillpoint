#!/bin/zsh

APP="/Applications/StillPoint.app"
BIN="$APP/Contents/MacOS/stillpoint"
LOG="$HOME/Library/Logs/StillPoint-launch.log"
VAULTS_ROOT="${STILLPOINT_VAULTS_ROOT:-$HOME/.stillpoint/vaults}"

if /usr/bin/pgrep -f "^${BIN}( |$)" >/dev/null; then
    /usr/bin/open "$APP"  # Ask LaunchServices to foreground it
else
    /bin/mkdir -p "${LOG:h}"
    /usr/bin/nohup /usr/bin/env STILLPOINT_VAULTS_ROOT="$VAULTS_ROOT" \
        "$BIN" >>"$LOG" 2>&1 </dev/null &!
fi
