#!/usr/bin/env bash
#
# hermes_inject.sh — Hermes Agent terminal skill for the iOS Simulator.
# Drops a log_note envelope into the app's App Group HermesInbox; the app
# ingests it into SwiftData on next foreground.
#
# Usage:
#   ./hermes_inject.sh "Reviewed the morning brief. #hermes"
#   ./hermes_inject.sh "Note text" tag1,tag2
#
# Reading app state back:
#   cat "$(dirname "$(inbox_dir)")/hermes_state_today.json"
#
set -euo pipefail

APP_GROUP="group.com.yourteam.personalops"
CONTENT="${1:?usage: hermes_inject.sh \"note text\" [tag1,tag2]}"
TAGS_CSV="${2:-}"

# Locate the App Group container inside the *booted* simulator.
inbox_dir() {
  local sim_root group_dir
  sim_root="$HOME/Library/Developer/CoreSimulator/Devices"
  local booted
  booted=$(xcrun simctl list devices booted -j |
    python3 -c 'import json,sys; d=json.load(sys.stdin)["devices"]; print(next(dev["udid"] for devs in d.values() for dev in devs))')
  group_dir=$(grep -rls "$APP_GROUP" \
    "$sim_root/$booted/data/Containers/Shared/AppGroup"/*/.com.apple.mobile_container_manager.metadata.plist |
    head -n1 | xargs dirname)
  echo "$group_dir/HermesInbox"
}

INBOX=$(inbox_dir)
mkdir -p "$INBOX"

TAGS_JSON=$(python3 - "$TAGS_CSV" <<'EOF'
import json, sys
csv = sys.argv[1]
print(json.dumps([t for t in csv.split(",") if t]))
EOF
)

TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
FILE="$INBOX/$(date +%s)-$RANDOM.json"

python3 - "$CONTENT" "$TIMESTAMP" "$TAGS_JSON" > "$FILE" <<'EOF'
import json, sys
print(json.dumps({
    "action": "log_note",
    "content": sys.argv[1],
    "timestamp": sys.argv[2],
    "tags": json.loads(sys.argv[3]),
}, indent=2))
EOF

echo "Injected note into $FILE"
