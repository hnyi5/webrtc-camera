#!/usr/bin/env python3

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import websockets

import config


clients = set()


async def handler(websocket):
    clients.add(websocket)

    print("[SIGNALING] Client connected")

    try:
        async for message in websocket:
            print("[SIGNALING] Message received")

            data = json.loads(message)

            # 把消息转发给其他客户端
            for client in clients:
                if client != websocket:
                    await client.send(json.dumps(data))

    except websockets.exceptions.ConnectionClosed:
        pass

    finally:
        clients.discard(websocket)
        print("[SIGNALING] Client disconnected")


async def main():
    print(
        "[SIGNALING] WebSocket server starting on "
        f"{config.SIGNALING_HOST}:{config.SIGNALING_PORT}"
    )

    async with websockets.serve(
        handler,
        config.SIGNALING_HOST,
        config.SIGNALING_PORT
    ):
        print("[SIGNALING] WebSocket server ready")
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
