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
#
# The field is `eipv4`, NOT `public-ipv4`.  Querying the latter returns an HTML
# 404 body, which a plain command substitution happily stores as if it were an
# address.  The previous single fallback was api.ipify.org, which from a Beijing
# instance answered "Connection refused".  Between the two, this script died
# with "could not determine the public IP" on the very platform it targets.
# Measured 2026-10-12; checkip.amazonaws.com and ipinfo.io/ip both worked.
#
# DSH_PUBLIC_IP overrides everything, for hosts where detection cannot work or
# where a different address should be advertised.
if [ -n "${DSH_PUBLIC_IP:-}" ]; then
    PUBLIC_IP="$DSH_PUBLIC_IP"
    log "using DSH_PUBLIC_IP from the environment"
else
    for source in \
        "http://100.100.100.200/latest/meta-data/eipv4" \
        "http://100.100.100.200/latest/meta-data/public-ipv4" \
        "https://checkip.amazonaws.com" \
        "https://ipinfo.io/ip" \
        "https://api.ipify.org"
    do
        candidate="$(curl -fsS --max-time 5 "$source" 2>/dev/null | tr -d '[:space:]' || true)"

        # Reject anything that is not four dotted decimal octets.  Without this,
        # an HTML error body is accepted as an address and turns into nonsense
        # further down (in turnserver.conf and the client-facing URLs).
        case "$candidate" in
            ''|*[!0-9.]*) candidate="" ;;
        esac
        if [ -n "$candidate" ] && [ "$(printf '%s' "$candidate" | tr -cd '.' | wc -c)" != "3" ]; then
            candidate=""
        fi

        if [ -n "$candidate" ]; then
            PUBLIC_IP="$candidate"
            echo "    public IP from: $source"
            break
        fi
    done
fi

if [ -z "$PUBLIC_IP" ]; then
    die "could not determine the public IP -- set DSH_PUBLIC_IP=<address> and re-run"
fi

echo "    private : $PRIVATE_IP"
echo "    public  : $PUBLIC_IP"

log "installing packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq coturn curl python3-pip

# websockets must be >= 13.  signaling_server.py uses websockets.asyncio, which
# does not exist in Ubuntu 22.04's python3-websockets (10.3); installing only
# from apt leaves the signalling service unable to start, with a bare
# ImportError as the only clue.  Install a suitable version when needed.
if python3 -c 'import websockets,sys; sys.exit(0 if int(websockets.__version__.split(".")[0]) >= 13 else 1)' 2>/dev/null; then
    echo "    websockets already new enough: $(python3 -c 'import websockets;print(websockets.__version__)')"
else
    echo "    installing websockets >= 13 (Ubuntu ships 10.3, which this code cannot use)"
    python3 -m pip install --quiet --upgrade pip 2>/dev/null || true
    python3 -m pip install --quiet --break-system-packages 'websockets>=13' 2>/dev/null \
        || python3 -m pip install --quiet 'websockets>=13' 2>/dev/null \
        || apt-get install -y -qq python3-websockets 2>/dev/null \
        || true
    python3 -c 'import websockets; print("    websockets now:", websockets.__version__, websockets.__file__)' 2>&1 | sed 's/^/  /'
fi

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

log "writing deployment values to $ENV_FILE"

# Replace, do not append.
#
# This used to be a bare `} >> "$ENV_FILE"`, so every re-run stacked another
# copy of the same keys.  The last write wins, so it kept working -- but the
# file grew without bound, and after the public IP changed, stale lines sat
# above the current ones.  The header of this script already promised
# idempotence; this makes it true.
if [ -f "$ENV_FILE" ]; then
    # Everything from our marker onwards is ours to redo.
    sed -i '/^# --- written by setup-server.sh ---$/,$d' "$ENV_FILE"

    # And bare copies of the keys we are about to set, from versions that
    # wrote them before the marker existed.
    sed -i -E '/^(DSH_TURN_URL|DSH_STUN_URL|DSH_SIGNALING_HOST|DSH_SIGNALING_PORT|DSH_PAGE_PORT|DSH_DAILY_TX_BUDGET_GB)=/d' "$ENV_FILE"
fi

umask 077
{
    echo ""
    echo "# --- written by setup-server.sh ---"
    echo "DSH_TURN_URL=turn:$PUBLIC_IP:3478?transport=udp"
    echo "DSH_STUN_URL=stun:$PUBLIC_IP:3478"
    echo "DSH_SIGNALING_HOST=0.0.0.0"
    echo "DSH_SIGNALING_PORT=8765"
    echo "DSH_PAGE_PORT=8000"
    echo ""
    echo "# Outbound budget for deploy/traffic_guard.py, in GB per day."
    echo "# Past this the guard stops coturn by itself.  5 GB is roughly CNY 4."
    echo "DSH_DAILY_TX_BUDGET_GB=${DSH_DAILY_TX_BUDGET_GB:-5}"
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

cat > /etc/systemd/system/dsh-traffic-guard.service <<EOF
[Unit]
Description=dsh-camera outbound traffic budget guard
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$REPO_ROOT
EnvironmentFile=$ENV_FILE
ExecStart=/usr/bin/python3 $REPO_ROOT/deploy/traffic_guard.py
Restart=always
RestartSec=10
# root: it has to be able to stop coturn
User=root

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable dsh-signaling dsh-page dsh-traffic-guard >/dev/null 2>&1 || true
systemctl restart dsh-signaling dsh-page dsh-traffic-guard

sleep 3

log "state"

# `|| true` here meant a unit that failed to start still reached the success
# banner below.  Record whether they are all up, and say which are not.
SERVICES_OK=1
for unit in coturn dsh-signaling dsh-page dsh-traffic-guard; do
    state="$(systemctl is-active "$unit" 2>/dev/null || true)"
    printf '  %-18s %s\n' "$unit" "$state"
    [ "$state" = "active" ] || SERVICES_OK=0
done

log "traffic guard"
python3 "$REPO_ROOT/deploy/traffic_guard.py" --status \
    --budget-gb "${DSH_DAILY_TX_BUDGET_GB:-5}" 2>/dev/null || true
echo
ss -tulnp | grep -E ':(3478|8765|8000|443)\b' || true

if [ "$SERVICES_OK" != "1" ]; then

cat <<EOF

==========================================================================
NOT DEPLOYED - at least one service is not active
==========================================================================

  Fix the units listed above, then re-run this script.  Nothing here is
  running, so the addresses below would not answer.
EOF

    exit 1
fi

cat <<EOF

==========================================================================
deployed
==========================================================================

  public IP   : $PUBLIC_IP

  page        : http://$PUBLIC_IP:8765/index_latency.html?token=<token>

  The page is served on the same port as the signalling websocket (8765), which
  keeps them same-origin.

  The access token is deliberately NOT printed here -- it is a long-lived
  credential, and a summary that prints it leaks it into terminal scrollback,
  logs, or anywhere the output is pasted.  Read it when you need it:

      grep ^DSH_ACCESS_TOKEN /etc/dsh-camera/env

  A ready-made URL, also mode 600, is at /root/access-url.txt if it was written.
  signalling  : ws://$PUBLIC_IP:8765/?token=<token>
  STUN        : stun:$PUBLIC_IP:3478
  TURN        : turn:$PUBLIC_IP:3478?transport=udp

  next:
    1. open the firewall ports listed at the top of this script
    2. verify TURN with the trickle-ice page in "relay" mode (deploy/README.md)
    3. run the sender against this server
EOF
