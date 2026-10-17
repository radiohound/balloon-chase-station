#!/bin/zsh
# Install the hourly link9 far-station alert on this Mac (launchd user agent).
# Run from this folder:  ./install-mac.sh
# Remove with:           ./install-mac.sh --uninstall
set -e
LABEL=net.k6atv.link9-alerts
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
SCRIPT="$(cd "$(dirname "$0")" && pwd)/link9_far_alert.py"
EMAIL="${EMAIL:-radiohound@gmail.com}"

if [[ "$1" == "--uninstall" ]]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Removed $LABEL. (Keychain item 'link9-alerts' left in place.)"
  exit 0
fi

if ! security find-generic-password -s link9-alerts -a "$EMAIL" >/dev/null 2>&1; then
  echo "No Gmail app password stored yet. Paste it at the prompt (16 letters, spaces OK)."
  security add-generic-password -s link9-alerts -a "$EMAIL" -T /usr/bin/security -w
fi

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>/usr/bin/python3</string><string>$SCRIPT</string>
    <string>--email</string><string>$EMAIL</string>
  </array>
  <key>StartInterval</key><integer>3600</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/link9-alerts.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/link9-alerts.log</string>
</dict></plist>
EOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installed. Runs now and every hour. Log: ~/Library/Logs/link9-alerts.log"
