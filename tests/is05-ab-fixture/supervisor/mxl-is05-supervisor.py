#!/usr/bin/env python3
"""IS-05 preview supervisor for the A/B MXL fixture (dmfdeploy/dmfdeploy#624).

Runs beside an nmos-cpp node in the viewer pod. It polls the node's own
Connection API for the MXL receiver's resolved /active, and when the selected
flow changes it replaces only the preview child (`mxl-gst-sink`). The pod, the
node and this process stay up. Stdlib only, so it runs in the existing MXL demo
image unchanged (mounted from a ConfigMap).

The fixture also runs this script with SUPERVISE=0 for the negative case: the
preview then follows the static STATIC_FLOW_ID env contract that the shipped
status server uses today, and ignores /active.

Env:
  NODE_API        node base URL (default http://127.0.0.1:8020)
  MXL_DOMAIN      local MXL domain directory (default /home/mxl/domain)
  MXL_DOMAIN_ID   the mxl_domain_id the node advertises for MXL_DOMAIN
  SUPERVISE       "1" follows /active (default); "0" keeps STATIC_FLOW_ID
  STATIC_FLOW_ID  flow to preview when SUPERVISE=0
  POLL_INTERVAL   seconds between /active reads (default 0.2)
  STATUS_PORT     HTTP port for /status and /preview.jpg (default 9000)
  PREVIEW_W/PREVIEW_H/PREVIEW_FPS  preview size and maximum rate (320x180 @ 5)
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

NODE_API = os.environ.get("NODE_API", "http://127.0.0.1:8020").rstrip("/")
DOMAIN = os.environ.get("MXL_DOMAIN", "/home/mxl/domain")
DOMAIN_ID = os.environ.get("MXL_DOMAIN_ID", "")
SUPERVISE = os.environ.get("SUPERVISE", "1") == "1"
STATIC_FLOW_ID = os.environ.get("STATIC_FLOW_ID", "")
POLL_INTERVAL = float(os.environ.get("POLL_INTERVAL", "0.2"))
PORT = int(os.environ.get("STATUS_PORT", "9000"))
PREVIEW_PATH = os.environ.get("PREVIEW_PATH", "/tmp/preview/preview.jpg")
MXL_TRANSPORT = "urn:x-nmos:transport:mxl"
STOP_GRACE = 3.0
MAX_EVENTS = 50

# The shipped status server's sink tail uses a plain `videorate`, which in the
# fixture's smoke test never wrote a frame for one flow and grew to 1.4 GiB for
# the other. drop-only/max-rate and sync=false keep the preview bounded.
SINK_TAIL = (
    "video/x-raw,width={w},height={h} ! "
    "videorate drop-only=true max-rate={fps} ! jpegenc ! "
    "multifilesink location={path} max-files=1 post-messages=false sync=false"
)

_lock = threading.Lock()
_state: dict = {
    "mode": "supervised" if SUPERVISE else "static",
    "supervisor_pid": os.getpid(),
    "started_at": time.time(),
    "receiver_id": None,
    "active": None,
    "active_error": None,
    "last_poll_at": None,
    "selected_flow": None,
    "child": None,
    "child_starts": 0,
    "events": [],
}
_child: subprocess.Popen | None = None
_child_flow: str | None = None


def _now() -> float:
    return time.time()


def _get_json(url: str, timeout: float = 2.0):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read() or b"null")


def _find_receiver() -> str | None:
    try:
        receivers = _get_json(f"{NODE_API}/x-nmos/node/v1.3/receivers")
    except (OSError, urllib.error.URLError, ValueError):
        return None
    for rcv in receivers or []:
        if str(rcv.get("transport", "")).startswith(MXL_TRANSPORT):
            return rcv.get("id")
    return None


def _selection(active: dict) -> tuple[str | None, str | None]:
    """Return (flow id to preview, error) for a resolved /active body."""
    if not active.get("master_enable"):
        return None, None
    legs = active.get("transport_params") or [{}]
    domain_id = legs[0].get("mxl_domain_id")
    flow_id = legs[0].get("mxl_flow_id")
    if DOMAIN_ID and domain_id != DOMAIN_ID:
        return None, f"mxl_domain_id {domain_id} is not the local domain"
    return flow_id or None, None


def _start_child(flow_id: str) -> subprocess.Popen:
    os.makedirs(os.path.dirname(PREVIEW_PATH), exist_ok=True)
    tail = SINK_TAIL.format(
        w=os.environ.get("PREVIEW_W", "320"),
        h=os.environ.get("PREVIEW_H", "180"),
        fps=os.environ.get("PREVIEW_FPS", "5"),
        path=PREVIEW_PATH,
    )
    env = dict(os.environ, MXL_GST_VIDEO_SINK=tail)
    return subprocess.Popen(
        ["mxl-gst-sink", "-d", DOMAIN, "-v", flow_id],
        env=env,
        start_new_session=True,
    )


def _stop_child(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=STOP_GRACE)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
    except ProcessLookupError:
        pass


def _switch(flow_id: str | None, reason: str) -> None:
    """Replace the preview child so it reads flow_id (None stops it)."""
    global _child, _child_flow
    event = {
        "reason": reason,
        "observed_at": _now(),
        "from_flow": _child_flow,
        "to_flow": flow_id,
        "old_pid": _child.pid if _child else None,
    }
    if _child is not None:
        _stop_child(_child)
    event["stopped_at"] = _now()
    # Drop the old picture so a reader sees "switching", never a stale frame.
    try:
        os.unlink(PREVIEW_PATH)
    except FileNotFoundError:
        pass
    _child, _child_flow = None, None
    if flow_id:
        _child = _start_child(flow_id)
        _child_flow = flow_id
    event["new_pid"] = _child.pid if _child else None
    event["started_at"] = _now()
    with _lock:
        _state["selected_flow"] = flow_id
        _state["child"] = (
            {"pid": _child.pid, "flow": flow_id, "started_at": event["started_at"]}
            if _child else None
        )
        if _child:
            _state["child_starts"] += 1
        _state["events"] = (_state["events"] + [event])[-MAX_EVENTS:]
    print(json.dumps({"switch": event}), flush=True)


def _supervise_loop() -> None:
    receiver_id = None
    while True:
        if receiver_id is None:
            receiver_id = _find_receiver()
            with _lock:
                _state["receiver_id"] = receiver_id
        if receiver_id is not None:
            url = f"{NODE_API}/x-nmos/connection/v1.2/single/receivers/{receiver_id}/active"
            try:
                active = _get_json(url)
                flow_id, err = _selection(active)
                with _lock:
                    _state["active"] = active
                    _state["active_error"] = err
                    _state["last_poll_at"] = _now()
                if flow_id != _child_flow:
                    _switch(flow_id, "active-changed")
            except (OSError, urllib.error.URLError, ValueError) as exc:
                with _lock:
                    _state["active_error"] = str(exc)
        if _child is not None and _child.poll() is not None:
            _switch(_child_flow, "child-exited")
        time.sleep(POLL_INTERVAL)


def _static_loop() -> None:
    while True:
        if STATIC_FLOW_ID and (_child is None or _child.poll() is not None):
            _switch(STATIC_FLOW_ID, "static-start" if _child is None else "child-exited")
        time.sleep(1.0)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # quiet
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/status"):
            with _lock:
                body = json.dumps(dict(_state, ts=_now())).encode()
            self._send(200, body, "application/json")
        elif self.path.startswith("/preview.jpg"):
            try:
                with open(PREVIEW_PATH, "rb") as fh:
                    data = fh.read()
            except OSError:
                self._send(404, b"", "text/plain")
                return
            self._send(200, data, "image/jpeg")
        else:
            self._send(404, b"", "text/plain")


def main() -> None:
    loop = _supervise_loop if SUPERVISE else _static_loop
    threading.Thread(target=loop, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
