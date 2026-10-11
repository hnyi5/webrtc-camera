#!/usr/bin/env bash
#
# Immediate cutoff, for when an alarm fires and you want it to stop NOW.
#
#   sudo bash deploy/kill-switch.sh status     what is running, how much used
#   sudo bash deploy/kill-switch.sh relay      stop relaying            (seconds)
#   sudo bash deploy/kill-switch.sh inbound    relay off + drop inbound  (seconds)
#   sudo bash deploy/kill-switch.sh restore    undo the firewall rules
#
# Why `relay` is usually enough: coturn is the only thing in this deployment
# that can move gigabytes.  Stopping it costs nothing permanent -- P2P keeps
# working, and relaying comes back with `systemctl start coturn`.
#
# THE FASTEST PANIC BUTTON IS NOT HERE, THOUGH.  It is the cloud console:
#
#     * lower the peak bandwidth to 1 Mbps                 (instant)
#     * remove the TURN rules from the security group      (instant)
#     * stop the instance                                  (instant)
#
# Those need no shell access, so they work from a phone.  Reach for them first
# if you are not already at a terminal.

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

UDP_PORTS=(3478 49152:65535)
TCP_PORTS=(8765 3478 8000)

log() { printf '%s\n' "$*"; }

stop_relay() {
    log "stopping coturn (the relay)"

    if systemctl stop coturn 2>/dev/null; then
        log "  coturn stopped -- P2P still works, only relaying is off"
    else
        log "  !! could not stop coturn"
    fi
}

drop_inbound() {
    stop_relay

    log "dropping inbound on the deployment ports"

    for port in "${UDP_PORTS[@]}"; do
        if ! iptables -C INPUT -p udp --dport "$port" -j DROP 2>/dev/null; then
            iptables -I INPUT -p udp --dport "$port" -j DROP
            log "  drop udp $port"
        fi
    done

    for port in "${TCP_PORTS[@]}"; do
        if ! iptables -C INPUT -p tcp --dport "$port" -j DROP 2>/dev/null; then
            iptables -I INPUT -p tcp --dport "$port" -j DROP
            log "  drop tcp $port"
        fi
    done

    log ""
    log "note: iptables rules are NOT persistent across reboot."
    log "      This is a panic button, not a configuration."
    log "      Undo with:  bash deploy/kill-switch.sh restore"
    log "      And fix the real problem in the security group too."
}

restore() {
    log "removing the panic-button rules"

    for port in "${UDP_PORTS[@]}"; do
        while iptables -C INPUT -p udp --dport "$port" -j DROP 2>/dev/null; do
            iptables -D INPUT -p udp --dport "$port" -j DROP
            log "  removed udp $port"
        done
    done

    for port in "${TCP_PORTS[@]}"; do
        while iptables -C INPUT -p tcp --dport "$port" -j DROP 2>/dev/null; do
            iptables -D INPUT -p tcp --dport "$port" -j DROP
            log "  removed tcp $port"
        done
    done

    log ""
    log "coturn is not restarted automatically -- decide first:"
    log "    sudo systemctl start coturn"
}

status() {
    log "services"
    for unit in coturn dsh-signaling dsh-page dsh-traffic-guard; do
        state="$(systemctl is-active "$unit" 2>/dev/null || true)"
        log "  $(printf '%-22s' "$unit") ${state:-unknown}"
    done

    log ""
    log "listening"
    ss -tuln 2>/dev/null | grep -E ':(3478|8765|8000|443)\b' || log "  nothing on our ports"

    log ""
    if [ -f "$REPO_ROOT/deploy/traffic_guard.py" ]; then
        python3 "$REPO_ROOT/deploy/traffic_guard.py" --status \
            --budget-gb "${DSH_DAILY_TX_BUDGET_GB:-5}" 2>/dev/null \
            || log "  (traffic guard state not readable)"
    fi

    log ""
    log "recent rejections (someone probing the signalling server?)"
    journalctl -u dsh-signaling --since "1 hour ago" --no-pager 2>/dev/null \
        | grep -c "rejected connection" \
        | sed 's/^/  rejected in the last hour: /' || true
}

case "${1:-}" in
    status)  status ;;
    relay)   stop_relay ;;
    inbound) drop_inbound ;;
    restore) restore ;;
    *)
        log "usage: bash deploy/kill-switch.sh {status|relay|inbound|restore}"
        log ""
        log "  status   what is running and how much traffic has been used"
        log "  relay    stop coturn -- stops all relaying immediately"
        log "  inbound  relay off, plus drop inbound on 3478 / 8765 / 8000 / relay range"
        log "  restore  remove the firewall rules added by 'inbound'"
        exit 2
        ;;
esac
