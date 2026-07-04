#!/usr/bin/env python3
"""MXL demo status sidecar — exposes the local node's flow state as JSON and an
optional periodic JPEG preview, for the dmf-cms "MXL Flows" evaluation page.

Stdlib only. Reads `mxl-info` for live flow stats; optionally drives `mxl-gst-sink`
(with the env-configurable video sink) to overwrite a JPEG snapshot of the flow.

Env:
  MXL_DOMAIN     domain dir (default /home/mxl/domain)
  MXL_FLOW_ID    video flow id (required for /status flow stats + preview)
  MXL_NODE       node name (downward API spec.nodeName)
  MXL_PROVIDER   cloud provider slug for the UI logo (e.g. aliyun) — NO IPs
  MXL_ROLE       producer | receiver
  MXL_FABRICS_INTERFACE fabrics NIC name (e.g. eth0 or lima0)
  MXL_TRANSPORT_PROVIDER  libfabric provider (default tcp)
  MXL_SERVICE    fabrics service/port (default 1234)
  STATUS_PORT    HTTP port (default 9000)
  PREVIEW        "1" to run the JPEG snapshot pipeline (receiver side)
  PREVIEW_W/PREVIEW_H/PREVIEW_FPS  preview size/rate (default 480x270 @ 5fps)
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DOMAIN = os.environ.get("MXL_DOMAIN", "/home/mxl/domain")
FLOW_ID = os.environ.get("MXL_FLOW_ID", "")
NODE = os.environ.get("MXL_NODE", "")
PROVIDER = os.environ.get("MXL_PROVIDER", "")
ROLE = os.environ.get("MXL_ROLE", "")
INTERFACE = os.environ.get("MXL_FABRICS_INTERFACE", "")
TRANSPORT_PROVIDER = os.environ.get("MXL_TRANSPORT_PROVIDER", "tcp")
SERVICE = os.environ.get("MXL_SERVICE", "1234")
PORT = int(os.environ.get("STATUS_PORT", "9000"))
PREVIEW = os.environ.get("PREVIEW", "0") == "1"
PREVIEW_PATH = "/tmp/mxl-preview.jpg"
HOST_OS_RELEASE = os.environ.get(
    "MXL_HOST_OS_RELEASE",
    "/host/etc/os-release:/host/usr/lib/os-release",
)
K8S_API = "https://kubernetes.default.svc"
K8S_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
K8S_CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"

_state_lock = threading.Lock()
_flow_state: dict = {}


def _run_capture(args: list[str], timeout: int = 5) -> str | None:
    try:
        res = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        text = (res.stdout or "") + ("\n" + res.stderr if res.stderr else "")
        text = text.strip()
        return text or None
    except Exception:  # noqa: BLE001 - demo resilience
        return None


def _host_os() -> str | None:
    for path in HOST_OS_RELEASE.split(":"):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("PRETTY_NAME="):
                        val = line.partition("=")[2].strip().strip('"')
                        return val or None
        except OSError:
            continue
    return None


def _host_hostinfo() -> dict:
    return {
        "os": _host_os(),
        "kernel": _run_capture(["uname", "-r"], timeout=2),
        "arch": _run_capture(["uname", "-m"], timeout=2),
    }


def _mxl_version() -> str | None:
    for cmd in (("mxl-info", "--version"), ("mxl-fabrics-demo", "--version")):
        out = _run_capture(list(cmd), timeout=5)
        if out:
            return out.splitlines()[0].strip() or None
    return None


def _k8s_node_info() -> dict:
    if not NODE:
        return {}
    try:
        with open(K8S_TOKEN_PATH, "r", encoding="utf-8") as fh:
            token = fh.read().strip()
        context = ssl.create_default_context(cafile=K8S_CA_PATH)
        req = urllib.request.Request(
            f"{K8S_API}/api/v1/nodes/{NODE}",
            headers={"Authorization": f"Bearer {token}"},
            method="GET",
        )
        with urllib.request.urlopen(req, context=context, timeout=3) as resp:
            payload = json.loads(resp.read() or b"{}")
        return {
            "container": {
                "k8s_version": payload.get("status", {}).get("nodeInfo", {}).get("kubeletVersion"),
            },
            "infra": {
                "zone": payload.get("metadata", {}).get("labels", {}).get("topology.kubernetes.io/zone"),
            },
        }
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, ValueError, ssl.SSLError):
        return {}


def _parse_mxl_info(text: str) -> dict:
    """Parse `mxl-info` 'key: value' lines into a normalised flow dict."""
    raw: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        raw[key.strip().lower()] = val.strip()

    def num(s):
        try:
            return int(s)
        except (TypeError, ValueError):
            try:
                return float(s)
            except (TypeError, ValueError):
                return None

    out: dict = {"raw": raw}
    if "head index" in raw:
        out["head_index"] = num(raw["head index"])
    if "grain count" in raw:
        out["grain_count"] = num(raw["grain count"])
    if "active" in raw:
        out["active"] = raw["active"].lower() == "true"
    if "last write time" in raw:
        out["last_write_ns"] = num(raw["last write time"])
    if "last read time" in raw:
        out["last_read_ns"] = num(raw["last read time"])
    # "Latency (grains, ms): 2, 59.86"
    lat = raw.get("latency (grains, ms)")
    if lat:
        parts = [p.strip() for p in lat.split(",")]
        if len(parts) == 2:
            out["latency_grains"] = num(parts[0])
            out["latency_ms"] = num(parts[1])
    # Best-effort format/rate fields if mxl-info prints them.
    for k in ("format", "grain rate", "rate", "width", "height"):
        if k in raw:
            out[k.replace(" ", "_")] = raw[k]
    return out


def _poll_loop() -> None:
    global _flow_state
    uri = f"mxl://{DOMAIN}?id={FLOW_ID}"
    while True:
        info: dict = {}
        try:
            res = subprocess.run(
                ["mxl-info", uri],
                capture_output=True, text=True, timeout=10,
            )
            info = _parse_mxl_info(res.stdout + "\n" + res.stderr)
        except Exception as exc:  # noqa: BLE001 - demo resilience
            info = {"error": str(exc)}
        with _state_lock:
            _flow_state = info
        time.sleep(1.0)


def _preview_loop() -> None:
    """Run mxl-gst-sink with a JPEG sink (env-configurable sink patch) and keep it up."""
    w = os.environ.get("PREVIEW_W", "480")
    h = os.environ.get("PREVIEW_H", "270")
    fps = os.environ.get("PREVIEW_FPS", "5")
    sink = (
        f"video/x-raw,width={w},height={h} ! videorate ! "
        f"video/x-raw,framerate={fps}/1 ! jpegenc ! "
        f"multifilesink location={PREVIEW_PATH} max-files=1 post-messages=false"
    )
    env = dict(os.environ, MXL_GST_VIDEO_SINK=sink)
    while True:
        try:
            subprocess.run(
                ["mxl-gst-sink", "-d", DOMAIN, "-v", FLOW_ID],
                env=env, timeout=None,
            )
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2.0)  # restart if it exits


class Handler(BaseHTTPRequestHandler):
    # HTTP/1.1, not the BaseHTTPRequestHandler default HTTP/1.0: the blackbox
    # http_2xx module rejects HTTP/1.0 outright ("Invalid HTTP version
    # number"), which zeroed probe_success for the stamped /status lane
    # (dmfdeploy/dmfdeploy#17 live verify). Safe because every response path
    # below sends an explicit Content-Length.
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # quiet
        pass

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/status"):
            with _state_lock:
                flow = dict(_flow_state)
            body = json.dumps({
                "node": NODE,
                "provider": PROVIDER,
                "role": ROLE,
                "interface": INTERFACE,
                "host": _host_hostinfo(),
                "mxl_version": _mxl_version(),
                **_k8s_node_info(),
                "transport": {
                    "library": "libfabric",
                    "provider": TRANSPORT_PROVIDER,
                    "service": SERVICE,
                    "interface": INTERFACE,
                },
                "flow": {"id": FLOW_ID, **flow},
                "preview": PREVIEW,
                "ts": time.time(),
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._cors()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/preview.jpg"):
            try:
                with open(PREVIEW_PATH, "rb") as fh:
                    data = fh.read()
            except OSError:
                self.send_response(404)
                self._cors()
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Cache-Control", "no-store")
            self._cors()
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_response(404)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()


def main() -> None:
    threading.Thread(target=_poll_loop, daemon=True).start()
    if PREVIEW and FLOW_ID:
        threading.Thread(target=_preview_loop, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
