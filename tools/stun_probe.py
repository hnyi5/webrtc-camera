#!/usr/bin/env python3
"""
STUN probe: ask several STUN servers, from one local socket, and report the
mapping EACH server sees.

Why this tool exists
--------------------
A browser's ICE candidate list de-duplicates identical mapped addresses, so
"only one srflx candidate" cannot be told apart from "every server agreed",
and those two cases mean opposite things for hole punching:

    one socket -> server A -> 1.2.3.4:5000
    one socket -> server B -> 1.2.3.4:5000    same   -> cone NAT, punching works
    one socket -> server B -> 1.2.3.4:6123    differ -> symmetric NAT, needs TURN

This tool asks every server separately and prints every answer, so the two
cases are distinguishable.  Everything else (browser-based tests) cannot do
that.

It also sends a request from a *second* local socket, which distinguishes
three NAT behaviours:

    endpoint-independent mapping (cone)         same server, different ports
                                                -> different mapped ports, but
                                                   each socket keeps ONE port
                                                   for all destinations
    address/port-dependent mapping (symmetric)  each destination gets its own
                                                mapped port

Usage:
    python3 tools/stun_probe.py
    python3 tools/stun_probe.py stun.l.google.com:19302 stun.miwifi.com:3478
"""

import os
import socket
import struct
import sys

MAGIC_COOKIE = 0x2112A442
BINDING_REQUEST = 0x0001
BINDING_SUCCESS = 0x0101

ATTR_MAPPED_ADDRESS = 0x0001
ATTR_XOR_MAPPED_ADDRESS = 0x0020

DEFAULT_SERVERS = [
    "stun.l.google.com:19302",
    "stun.miwifi.com:3478",
    "stun.qq.com:3478",
    "stun.cloudflare.com:3478",
]


def build_binding_request():
    transaction_id = os.urandom(12)
    header = struct.pack("!HHI", BINDING_REQUEST, 0, MAGIC_COOKIE)
    return header + transaction_id, transaction_id


def parse_binding_response(data):
    """Return the mapped address as 'ip:port', or None."""
    if len(data) < 20:
        return None

    message_type, message_length, magic = struct.unpack("!HHI", data[:8])

    if magic != MAGIC_COOKIE or message_type != BINDING_SUCCESS:
        return None

    offset = 20
    end = min(20 + message_length, len(data))
    mapped = None

    while offset + 4 <= end:
        attribute_type, attribute_length = struct.unpack(
            "!HH", data[offset : offset + 4]
        )
        value = data[offset + 4 : offset + 4 + attribute_length]

        if attribute_type == ATTR_XOR_MAPPED_ADDRESS and len(value) >= 8:
            port = struct.unpack("!H", value[2:4])[0] ^ (MAGIC_COOKIE >> 16)
            address = struct.unpack("!I", value[4:8])[0] ^ MAGIC_COOKIE
            mapped = "%s:%d" % (socket.inet_ntoa(struct.pack("!I", address)), port)

        elif attribute_type == ATTR_MAPPED_ADDRESS and len(value) >= 8:
            port = struct.unpack("!H", value[2:4])[0]
            address = value[4:8]
            mapped = "%s:%d" % (socket.inet_ntoa(address), port)

        # attributes are padded to a 4-byte boundary
        offset += 4 + attribute_length
        offset += (4 - attribute_length % 4) % 4

    return mapped


def resolve(server, default_port=3478):
    host, _, port_text = server.partition(":")
    port = int(port_text) if port_text else default_port
    return socket.gethostbyname(host), port


def ask(sock, server, timeout=3.0):
    """Query one server over an existing socket. Returns mapped string or None."""
    try:
        address, port = resolve(server)
    except Exception as error:  # noqa: BLE001
        print("    %-32s DNS failed: %s" % (server, error))
        return None

    request, transaction_id = build_binding_request()
    sock.settimeout(timeout)

    try:
        sock.sendto(request, (address, port))
    except Exception as error:  # noqa: BLE001
        print("    %-32s send failed: %s" % (server, error))
        return None

    deadline_hits = 0
    while deadline_hits < 3:
        try:
            data, _ = sock.recvfrom(2048)
        except socket.timeout:
            print("    %-32s timeout" % server)
            return None
        except Exception as error:  # noqa: BLE001
            print("    %-32s recv failed: %s" % (server, error))
            return None

        if len(data) >= 20 and data[8:20] == transaction_id:
            mapped = parse_binding_response(data)
            print("    %-32s -> %s" % (server, mapped or "unparsable response"))
            return mapped

        deadline_hits += 1

    print("    %-32s no matching response" % server)
    return None


def main():
    servers = sys.argv[1:] or DEFAULT_SERVERS

    print("=" * 74)
    print("NAT behaviour probe")
    print("=" * 74)
    print()
    print("Asking %d STUN servers, all from ONE local socket:" % len(servers))

    first_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    first_socket.bind(("0.0.0.0", 0))
    local_a = first_socket.getsockname()
    print("  local socket A = %s:%d" % local_a)
    print()

    answers_a = []

    for server in servers:
        mapped = ask(first_socket, server)
        if mapped:
            answers_a.append((server, mapped))

    first_socket.close()

    print()
    print("Asking the same servers again from a SECOND local socket:")
    second_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    second_socket.bind(("0.0.0.0", 0))
    local_b = second_socket.getsockname()
    print("  local socket B = %s:%d" % local_b)
    print()

    answers_b = []

    for server in servers:
        mapped = ask(second_socket, server)
        if mapped:
            answers_b.append((server, mapped))

    second_socket.close()

    print()
    print("=" * 74)
    print("result")
    print("=" * 74)

    if len(answers_a) < 2:
        print()
        print("  Only %d server(s) answered from socket A." % len(answers_a))
        print("  That is NOT enough to judge the NAT: with a single answer,")
        print("  'the other server would have agreed' and 'the other server")
        print("  was unreachable' look exactly the same.")
        print()
        print("  Try again with different servers, e.g.:")
        print("    python3 tools/stun_probe.py stun.l.google.com:19302 \\")
        print("        stun1.l.google.com:19302 stun.miwifi.com:3478")
        return 2

    mapped_ports = sorted({mapped.rsplit(":", 1)[1] for _, mapped in answers_a})

    print()
    print("  socket A asked %d servers and got %d distinct mapped port(s):"
          % (len(answers_a), len(mapped_ports)))

    for server, mapped in answers_a:
        print("    %-32s -> %s" % (server, mapped))

    print()

    if len(mapped_ports) == 1:
        print("  ==> ENDPOINT-INDEPENDENT MAPPING (cone NAT).")
        print("      Every destination sees the same external port, which is")
        print("      exactly what hole punching needs.")
        print("      LIKELY OUTCOME: direct peer-to-peer works, no TURN needed.")
    else:
        print("  ==> ADDRESS/PORT-DEPENDENT MAPPING (symmetric NAT).")
        print("      Each destination gets its own external port, so the port")
        print("      a peer learns from STUN is not the port that peer must")
        print("      send to.")
        print("      LIKELY OUTCOME: hole punching fails, a TURN relay is required.")

    print()
    print("  second run, different local port (%s:%d):"
          % (local_b[0], local_b[1]))
    for server, mapped in answers_b:
        print("    %-32s -> %s" % (server, mapped))

    print()
    print("  Reminder: this only describes the network this command runs on.")
    print("  NAT behaviour differs per network, so run it on the machine that")
    print("  will actually host the camera.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
