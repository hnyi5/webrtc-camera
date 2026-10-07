#!/usr/bin/env python3
"""
One-shot NTP-style time server for measuring the VM <-> host clock offset.

Protocol (proper NTP bracket):
    1. client connects
    2. client sends one request byte   <- client records t1 just before this
    3. server receives it and records t2 immediately
    4. server replies with t2
    5. client records t4 on receipt

t2 therefore always lies inside [t1, t4], so
    offset = t2 - (t1 + t4) / 2
with uncertainty +/- (t4 - t1) / 2.

Run in the VM (foreground, exits by itself):
    python3 tools/timesrv.py [port] [count]
"""

import socket
import sys
import time

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9099
COUNT = int(sys.argv[2]) if len(sys.argv) > 2 else 15

server = socket.socket()
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind(("0.0.0.0", PORT))
server.listen(16)

print(f"timesrv ready on {PORT}, serving {COUNT} requests", flush=True)

served = 0

while served < COUNT:
    connection, _address = server.accept()

    request = connection.recv(16)

    if not request:
        connection.close()
        continue

    timestamp_ns = time.time_ns()
    connection.sendall((str(timestamp_ns) + "\n").encode())
    connection.close()
    served += 1

print("timesrv done", flush=True)
