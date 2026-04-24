"""
ws_server.py — Railway WebSocket Bridge
----------------------------------------
Jalankan di Railway sebagai cloud bridge.
Tugasnya: terima command dari voice_agent.py → broadcast ke laptop_listener.py

Deploy ke Railway:
  1. Push file ini + requirements.txt ke GitHub
  2. Connect repo ke Railway
  3. Set PORT di Railway env (otomatis tersedia)
"""

import asyncio
import json
import os
import websockets
from websockets.server import WebSocketServerProtocol

PORT = int(os.getenv("PORT", 8765))

# ─────────────────────────────────────────────
# Registry koneksi aktif
# ─────────────────────────────────────────────
# voice_agent connect sebagai "sender"
# laptop_listener connect sebagai "receiver"
senders: set[WebSocketServerProtocol] = set()
receivers: set[WebSocketServerProtocol] = set()


# ─────────────────────────────────────────────
# Broadcast command ke semua laptop listener
# ─────────────────────────────────────────────
async def broadcast_to_receivers(payload: str):
    if not receivers:
        print("[WS Server] Tidak ada laptop yang terhubung!")
        return

    disconnected = set()
    for ws in receivers:
        try:
            await ws.send(payload)
            print(f"[WS Server] Command dikirim ke laptop: {payload}")
        except websockets.ConnectionClosed:
            disconnected.add(ws)

    receivers.difference_update(disconnected)


# ─────────────────────────────────────────────
# Handler setiap koneksi masuk
# ─────────────────────────────────────────────
async def handler(ws: WebSocketServerProtocol):
    # Pesan pertama menentukan role: sender atau receiver
    try:
        init_msg = await asyncio.wait_for(ws.recv(), timeout=10)
        data = json.loads(init_msg)
        role = data.get("role", "")
    except (asyncio.TimeoutError, json.JSONDecodeError, Exception) as e:
        print(f"[WS Server] Gagal identify role: {e}")
        await ws.close()
        return

    if role == "sender":
        senders.add(ws)
        print(f"[WS Server] voice_agent terhubung. Total senders: {len(senders)}")
        try:
            async for message in ws:
                print(f"[WS Server] Command masuk dari voice_agent: {message}")
                await broadcast_to_receivers(message)
        except websockets.ConnectionClosed:
            pass
        finally:
            senders.discard(ws)
            print("[WS Server] voice_agent disconnected.")

    elif role == "receiver":
        receivers.add(ws)
        print(f"[WS Server] laptop_listener terhubung. Total receivers: {len(receivers)}")
        try:
            # Receiver cukup stay alive, tunggu command dari server
            await ws.wait_closed()
        except websockets.ConnectionClosed:
            pass
        finally:
            receivers.discard(ws)
            print("[WS Server] laptop_listener disconnected.")

    else:
        print(f"[WS Server] Role tidak dikenal: {role}")
        await ws.close()


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────
async def main():
    print(f"[WS Server] Berjalan di port {PORT}...")
    async with websockets.serve(handler, "0.0.0.0", PORT):
        await asyncio.Future()  # jalan selamanya


if __name__ == "__main__":
    asyncio.run(main())