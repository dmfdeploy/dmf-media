"""Minimal client for the crosspoint controller's public WebSocket API.

The slice talks to the controller only the way its own web UI does: one
WebSocket on the server port, a seeded login, the "crosspoint" sync channel
for the device and flow numbers, and the "makeconnection" request. It imports
nothing from the controller. Standard library only, so the fixture keeps one
pinned dependency.

Protocol, as served by the controller at the pinned commits:
  server -> {"type": "authseed", "seed": S}
  client -> {"type": "auth", "user": U, "password": sha256(sha256(P) + S)}
  server -> {"type": "auth", "user": U}            (or {"type": "authfailed"})
  client -> {"type": "sync", "channel": "crosspoint"}
  server -> {"type": "sync", "channel": "crosspoint", "action": "init", "data": ...}
  (later "patch" messages carry RFC 6902 patches; the client ignores them and
  subscribes again whenever it needs the current numbers)
  client -> {"type": "request", "method": "POST", "route": "makeconnection",
             "id": N, "data": {"source": "<dev>.v<n>", "destination": "<dev>.v<n>",
                              "preview": false}}
  server -> {"type": "response", "id": N, "status": ..., "data": ...}
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import time
from urllib.parse import urlparse


class WebSocket:
    """Just enough RFC 6455 for text frames, from a client."""

    def __init__(self, url: str, timeout: float = 10.0):
        u = urlparse(url)
        self.sock = socket.create_connection((u.hostname, u.port or 80), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET {u.path or '/'} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(req.encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(1)
            if not chunk:
                raise ConnectionError("controller closed the WebSocket handshake")
            head += chunk
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError(f"WebSocket upgrade refused: {head.splitlines()[0]!r}")

    def _recv_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("controller closed the WebSocket")
            buf += chunk
        return buf

    def send_text(self, text: str) -> None:
        payload = text.encode()
        header = bytearray([0x81])
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack("!H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack("!Q", n)
        mask = os.urandom(4)
        self.sock.sendall(bytes(header) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def recv_text(self) -> str:
        """Return the next text message; answer pings, skip pongs."""
        message = b""
        while True:
            b0, b1 = self._recv_exact(2)
            opcode, n = b0 & 0x0F, b1 & 0x7F
            if n == 126:
                n = struct.unpack("!H", self._recv_exact(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", self._recv_exact(8))[0]
            mask = self._recv_exact(4) if b1 & 0x80 else None
            data = self._recv_exact(n)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if opcode == 0x9:  # ping
                self.sock.sendall(bytes([0x8A, 0x80 | len(data)]) + b"\0\0\0\0" + data)
                continue
            if opcode == 0xA:  # pong
                continue
            if opcode == 0x8:
                raise ConnectionError("controller closed the WebSocket")
            message += data
            if b0 & 0x80:
                return message.decode()

    def close(self) -> None:
        try:
            self.sock.sendall(bytes([0x88, 0x80]) + b"\0\0\0\0")
        except OSError:
            pass
        self.sock.close()


class Crosspoint:
    """A logged-in session with the controller."""

    def __init__(self, base_url: str, user: str, password: str, timeout: float = 10.0):
        self.ws = WebSocket(base_url.replace("http://", "ws://", 1) + "/", timeout=timeout)
        self.timeout = timeout
        self.state: dict = {}
        self.channels: dict = {}
        self._next_id = 1
        seed = self._wait(lambda m: m.get("type") == "authseed")["seed"]
        digest = hashlib.sha256(password.encode()).hexdigest()
        proof = hashlib.sha256((digest + seed).encode()).hexdigest()
        self.ws.send_text(json.dumps({"type": "auth", "user": user, "password": proof}))
        reply = self._wait(lambda m: m.get("type") in ("auth", "authfailed"))
        if reply["type"] != "auth":
            raise PermissionError("controller refused the fixture login")
        self.refresh()

    def refresh(self, channel: str = "crosspoint") -> dict:
        """Subscribe to a sync channel again and return its full state.

        "crosspoint" carries the device and flow numbers; "nmos" carries the
        IS-04 resources as the controller sees them.
        """
        self.ws.send_text(json.dumps({"type": "unsync", "channel": channel}))
        self.ws.send_text(json.dumps({"type": "sync", "channel": channel}))
        self._wait(lambda m: m.get("type") == "sync" and m.get("channel") == channel and m.get("action") == "init")
        self.ws.send_text(json.dumps({"type": "unsync", "channel": channel}))
        return self.channels[channel]

    def _handle(self, msg: dict) -> None:
        if msg.get("type") == "sync" and msg.get("action") == "init":
            self.channels[msg.get("channel")] = msg.get("data") or {}
            if msg.get("channel") == "crosspoint":
                self.state = self.channels["crosspoint"]

    def _wait(self, pred, timeout: float | None = None) -> dict:
        deadline = time.time() + (timeout or self.timeout)
        while time.time() < deadline:
            self.ws.sock.settimeout(max(0.1, deadline - time.time()))
            msg = json.loads(self.ws.recv_text())
            self._handle(msg)
            if pred(msg):
                return msg
        raise TimeoutError("no matching message from the controller")

    def find(self, nmos_id: str, side: str) -> str | None:
        """Return "<device num>.<type letter><flow num>" for an NMOS sender or receiver id."""
        for dev in self.state.get("devices", []):
            for kind, flows in (dev.get(side) or {}).items():
                for flow in flows:
                    if flow.get("id") == "nmos_" + nmos_id and dev.get("num", -1) > 0:
                        return f"{dev['num']}.{kind[0]}{flow['num']}"
        return None

    def request(self, route: str, data: dict, timeout: float = 30.0) -> dict:
        rid = self._next_id
        self._next_id += 1
        self.ws.send_text(json.dumps({"type": "request", "method": "POST", "route": route, "id": rid, "data": data}))
        return self._wait(lambda m: m.get("type") == "response" and m.get("id") == rid, timeout=timeout)

    def close(self) -> None:
        self.ws.close()

