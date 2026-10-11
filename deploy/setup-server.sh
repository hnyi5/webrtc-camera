#!/usr/bin/env bash
#
# Deploy the public side of the dsh-camera project on a fresh Ubuntu 22.04 VPS:
# coturn (STUN + TURN), the signalling server, and the page server.
#
# Usage, from the repository root, with the secrets already in place:
#
#     sudo install -d -m 700 /etc/dsh-camera
#     sudo cp deploy/secrets.env.example /etc/dsh-camera/env
#     sudo chmod 600 /etc/dsh-camera/env
#     sudo $EDITOR /etc/dsh-camera/env          # fill in the two secrets
#     sudo bash deploy/setup-server.sh
#
# The secrets deliberately live in /etc, NOT in the repository.  Keeping them
# out of the working tree means no future mistake with a static file server can
# publish them.
#
# Idempotent: re-running after editing /etc/dsh-camera/env or the template
# reinstalls everything.
#
# This script does NOT touch the cloud firewall.  Open these yourself in the
# provider's console -- see deploy/README.md:
#
#     22/tcp            ssh
#     8765/tcp          signalling
#     3478/udp, 3478/tcp  STUN / TURN
#     49152-65535/udp   relay range        <-- the one everyone forgets
#     8000/tcp          page
#     443/tcp           TURN over TLS (optional, only needed on hostile networks)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="/etc/dsh-camera/env"
TEMPLATE="$REPO_ROOT/deploy/turnserver.conf.template"

log() { printf '\n=== %s\n' "$*"; }
die() { printf '\n!!! %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run with sudo"
[ -f "$ENV_FILE" ] || die "missing $ENV_FILE -- see the header of this script"
[ -f "$TEMPLATE" ] || die "missing $TEMPLATE"

RUN_USER="${SUDO_USER:-root}"

log "loading $ENV_FILE"
set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

[ -n "${DSH_ACCESS_TOKEN:-}" ] || die "DSH_ACCESS_TOKEN is empty -- refusing to deploy an unauthenticated signalling server"
[ -n "${DSH_TURN_SECRET:-}" ] || die "DSH_TURN_SECRET is empty -- refusing to deploy an unauthenticated TURN server"

log "detecting addresses"
PRIVATE_IP="$(hostname -I | awk '{print $1}')"

# Aliyun publishes instance metadata on a link-local address, reachable before
# any outbound connectivity exists.
PUBLIC_IP="$(curl -fsS --max-time 3 http://100.100.100.200/latest/meta-data/public-ipv4 2>/dev/null || true)"
if [ -z "$PUBLIC_IP" ]; then
    PUBLIC_IP="$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || true)"
fi
[ -n "$PUBLIC_IP" ] || die "could not determine the public IP"

echo "    private : $PRIVATE_IP"
echo "    public  : $PUBLIC_IP"

log "installing packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq coturn python3-websockets curl

log "writing /etc/turnserver.conf"
TMP_CONF="$(mktemp)"
trap 'rm -f "$TMP_CONF"' EXIT
sed \
    -e "s|__PUBLIC_IP__|$PUBLIC_IP|g" \
    -e "s|__PRIVATE_IP__|$PRIVATE_IP|g" \
    -e "s|__TURN_SECRET__|$DSH_TURN_SECRET|g" \
    -e "s|__TURN_REALM__|${DSH_TURN_REALM:-dsh-camera}|g" \
    "$TEMPLATE" > "$TMP_CONF"

install -m 640 -o root -g turnserver "$TMP_CONF" /etc/turnserver.conf

log "enabling coturn"
sed -i 's/^#\?TURNSERVER_ENABLED=.*/TURNSERVER_ENABLED=1/' /etc/default/coturn
systemctl enable coturn >/dev/null 2>&1 || true
systemctl restart coturn

log "appending deployment values to $ENV_FILE"
umask 077
{
    echo ""
    echo "# --- written by setup-server.sh ---"
    echo "DSH_TURN_URL=turn:$PUBLIC_IP:3478?transport=udp"
    echo "DSH_STUN_URL=stun:$PUBLIC_IP:3478"
    echo "DSH_SIGNALING_HOST=0.0.0.0"
    echo "DSH_SIGNALING_PORT=8765"
    echo "DSH_PAGE_PORT=8000"
} >> "$ENV_FILE"

log "installing services"
cat > /etc/systemd/system/dsh-signaling.service <<EOF
[Unit]
Description=dsh-camera signalling server
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$REPO_ROOT
EnvironmentFile=$ENV_FILE
ExecStart=/usr/bin/python3 $REPO_ROOT/signaling_server.py
Restart=always
RestartSec=2
User=$RUN_USER

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/dsh-page.service <<EOF
[Unit]
Description=dsh-camera page server
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$REPO_ROOT
EnvironmentFile=$ENV_FILE
ExecStart=/usr/bin/python3 $REPO_ROOT/serve_page.py 8000
Restart=always
RestartSec=2
User=$RUN_USER

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable dsh-signaling dsh-page >/dev/null 2>&1 || true
systemctl restart dsh-signaling dsh-page

sleep 3

log "state"
systemctl is-active coturn dsh-signaling dsh-page || true
echo
ss -tulnp | grep -E ':(3478|8765|8000|443)\b' || true

cat <<EOF

==========================================================================
deployed
==========================================================================

  public IP   : $PUBLIC_IP

  page        : http://$PUBLIC_IP:8000/index_latency.html?token=$DSH_ACCESS_TOKEN
  signalling  : ws://$PUBLIC_IP:8765/?token=<token>
  STUN        : stun:$PUBLIC_IP:3478
  TURN        : turn:$PUBLIC_IP:3478?transport=udp

  next:
    1. open the firewall ports listed at the top of this script
    2. verify TURN with the trickle-ice page in "relay" mode (deploy/README.md)
    3. run the sender against this server
EOF
