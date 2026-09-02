#!/usr/bin/env bash
# Install the intern-alert watchers as systemd timers on an Oracle Cloud
# (or any Debian/Ubuntu) VM. Safe to re-run: it is idempotent.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_USER="${SUDO_USER:-$USER}"
UNIT_DIR=/etc/systemd/system

echo "==> installing from $APP_DIR as user $RUN_USER"

if ! command -v python3 >/dev/null; then
  echo "python3 not found; installing"
  sudo apt-get update -qq && sudo apt-get install -y -qq python3
fi

PYV=$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])')
echo "==> python3 $PYV"
python3 -c 'import sys; sys.exit(0 if sys.version_info>=(3,9) else 1)' || {
  echo "ERROR: need Python 3.9+"; exit 1; }

# --- secrets -----------------------------------------------------------------
if [ ! -f "$APP_DIR/secrets.json" ]; then
  echo
  echo "==> secrets.json not found; creating it now"
  read -rp "    Gmail address to alert: " EMAIL
  read -rsp "    Gmail APP PASSWORD (16 chars, input hidden): " APPPW; echo
  cat > "$APP_DIR/secrets.json" <<JSON
{
  "gmail_app_password": "$APPPW",
  "alert_email_to": "$EMAIL",
  "alert_email_from": "$EMAIL",
  "startup_jobs_api_key": ""
}
JSON
  echo "    written"
fi
chmod 600 "$APP_DIR/secrets.json"
chown "$RUN_USER" "$APP_DIR/secrets.json"
echo "==> secrets.json secured (chmod 600)"

# --- preflight ---------------------------------------------------------------
echo
echo "==> preflight"
if ! python3 "$APP_DIR/deploy/preflight.py"; then
  echo
  echo "Preflight failed. Fix the above before enabling timers."
  echo "If SMTP is blocked, Oracle blocks port 25 by default; 465/587 are normally"
  echo "allowed. Raise a service-limit request if both are unreachable."
  exit 1
fi

# --- systemd -----------------------------------------------------------------
echo
echo "==> installing systemd units"
for u in intern-alerts-realtime intern-alerts-sweep; do
  sed -e "s#^User=.*#User=$RUN_USER#" \
      -e "s#^WorkingDirectory=.*#WorkingDirectory=$APP_DIR#" \
      "$APP_DIR/deploy/$u.service" | sudo tee "$UNIT_DIR/$u.service" >/dev/null
  sudo cp "$APP_DIR/deploy/$u.timer" "$UNIT_DIR/$u.timer"
done

sudo systemctl daemon-reload
sudo systemctl enable --now intern-alerts-realtime.timer intern-alerts-sweep.timer

# --- seed --------------------------------------------------------------------
if [ ! -f "$APP_DIR/ats_state.json" ]; then
  echo
  echo "==> no state found; seeding so you are not emailed the whole backlog"
  echo "    (skip this and delete ats_state.json if you DO want the backlog)"
  sudo -u "$RUN_USER" python3 "$APP_DIR/atswatch.py" --seed >/dev/null
  sudo -u "$RUN_USER" python3 "$APP_DIR/main.py" --seed >/dev/null
fi

echo
echo "==> done"
systemctl list-timers 'intern-alerts-*' --no-pager || true
echo
echo "Logs:   journalctl -u intern-alerts-realtime -f"
echo "Run now: sudo systemctl start intern-alerts-realtime.service"
