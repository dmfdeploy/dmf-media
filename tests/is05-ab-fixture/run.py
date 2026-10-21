#!/usr/bin/env python3
"""Driver for the IS-05 A/B MXL fixture (dmfdeploy/dmfdeploy#624).

run.sh prepares a single-node cluster and calls this script. It deploys the
fixture, checks every acceptance item in the issue, runs the negative case and
the AMWA nmos-testing suites (warn mode), and writes the observations to
--out. Exit status is 0 only when every primary acceptance item passes;
nmos-testing results never change it.

With --switch-via crosspoint (dmfdeploy/dmfdeploy#629) the same checks run,
but every switch is the crosspoint controller's own connection call instead of
the driver's direct PATCH. The slice adds two fixture-only NMOS sender nodes,
the controller line chosen from crosspoint/lines.json, and a packet capture
that records the PATCH the viewer's node received.

Needs kubectl on PATH with KUBECONFIG pointing at the fixture cluster, and
Pillow for reading the preview frames.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import io
import json
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "crosspoint"))
from client import Crosspoint  # noqa: E402 - fixture-local module
NS = "is05-ab"
DOMAIN_ID = "d0d0d0d0-0000-4000-8000-000000000001"
FLOW = {
    "A": "5a1e0000-0000-4000-8000-00000000000a",
    "B": "5a1e0000-0000-4000-8000-00000000000b",
}
# Crosspoint slice: the sources write the flows that the fixture-only sender
# nodes advertise. nmos-cpp derives a flow ID from the node's seed_id as a
# name-based UUID; deploy_crosspoint checks the derived IDs against the
# nodes' own IS-04 flows.
SENDER_CONFIG = {"A": "sender-a.json", "B": "sender-b.json"}
CROSSPOINT_PORT = 8080
CROSSPOINT_USER = "is05-ab-slice"
# Upper bound for "the preview shows the newly selected pattern", measured
# from the PATCH. The observed interval is recorded next to it.
SWITCH_BOUND_S = 10.0
# Longer than the registry's 12 s expiry, so a missed heartbeat would show.
HEARTBEAT_MIN_WINDOW_S = 15.0
# A/B pairs after the first A then B, and the pause between switches.
EXTRA_PAIRS = 5
SETTLE_S = 2.0
NMOS_TESTING_IMAGE = (
    "docker.io/amwa/nmos-testing@sha256:"
    "cbff9e48e5c7888384dfe4f9793b0f7989dd17fa10a8f144d77c261116aada1f"
)


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def kubectl(*args: str, check: bool = True, input_: str | None = None) -> str:
    res = subprocess.run(
        ["kubectl", *args], capture_output=True, text=True, input=input_
    )
    if check and res.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args)} failed: {res.stderr.strip()}")
    return res.stdout


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextlib.contextmanager
def port_forward(target: str, remote: int):
    """Yield a local base URL forwarded to target:remote."""
    local = free_port()
    proc = subprocess.Popen(
        ["kubectl", "-n", NS, "port-forward", target, f"{local}:{remote}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{local}"
    try:
        deadline = time.time() + 20
        while time.time() < deadline:
            with contextlib.suppress(OSError):
                socket.create_connection(("127.0.0.1", local), timeout=0.5).close()
                break
            time.sleep(0.2)
        yield url
    finally:
        proc.terminate()
        proc.wait()


def http(method: str, url: str, body=None, timeout: float = 3.0):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (OSError, urllib.error.URLError):
        return None, b""


def get_json(url: str):
    code, raw = http("GET", url)
    if code != 200:
        raise RuntimeError(f"GET {url} returned {code}")
    return json.loads(raw)


def classify(jpeg: bytes) -> str:
    """Return "A" (SMPTE bars), "B" (checkers-8) or "?" for one preview frame.

    Averages a horizontal band into seven columns. The bars give seven very
    different colours; the fine checkerboard averages to one olive colour.
    """
    try:
        img = Image.open(io.BytesIO(jpeg)).convert("RGB")
        img.load()
    except Exception:  # noqa: BLE001 - a partly written JPEG is not a frame
        return "?"
    w, h = img.size
    cols = list(img.crop((0, int(h * 0.15), w, int(h * 0.6))).resize((7, 1), Image.BOX).get_flattened_data())
    spread = max(max(c[i] for c in cols) - min(c[i] for c in cols) for i in range(3))
    r, g, b = (sum(c[i] for c in cols) / 7 for i in range(3))
    if spread > 150:
        return "A"
    if spread < 30 and 80 < r < 170 and 80 < g < 170 and b < 70:
        return "B"
    return "?"


class Viewer:
    """Port-forwards to the viewer pod and wraps its node and supervisor APIs."""

    def __init__(self, stack: contextlib.ExitStack):
        self.node = stack.enter_context(port_forward("pod/viewer", 8020))
        self.sup = stack.enter_context(port_forward("pod/viewer", 9000))
        self.receiver_id = self._receiver_id()
        self.rx = f"{self.node}/x-nmos/connection/v1.2/single/receivers/{self.receiver_id}"

    def _receiver_id(self) -> str:
        deadline = time.time() + 60
        while time.time() < deadline:
            with contextlib.suppress(RuntimeError, ValueError):
                for rcv in get_json(f"{self.node}/x-nmos/node/v1.3/receivers"):
                    if rcv["transport"].startswith("urn:x-nmos:transport:mxl"):
                        return rcv["id"]
            time.sleep(1)
        raise RuntimeError("viewer node never exposed an MXL receiver")

    def node_id(self) -> str:
        return get_json(f"{self.node}/x-nmos/node/v1.3/self")["id"]

    def status(self) -> dict:
        return get_json(f"{self.sup}/status")

    def patch(self, flow_id: str, domain_id: str = "auto") -> dict:
        body = {
            "sender_id": None,
            "master_enable": True,
            "activation": {"mode": "activate_immediate"},
            "transport_params": [{"mxl_domain_id": domain_id, "mxl_flow_id": flow_id}],
        }
        sent = time.time()
        code, raw = http("PATCH", f"{self.rx}/staged", body)
        done = time.time()
        return {
            "request": body,
            "sent_at": sent,
            "response_at": done,
            "status": code,
            "response": json.loads(raw) if raw else None,
            "staged": get_json(f"{self.rx}/staged"),
            "active": get_json(f"{self.rx}/active"),
        }

    def preview(self) -> tuple[str, bytes | None]:
        code, raw = http("GET", f"{self.sup}/preview.jpg", timeout=1.0)
        if code != 200 or not raw:
            return ("none", None)
        return (classify(raw), raw)


def wait_for_pattern(viewer: Viewer, want: str, since: float, bound: float, out: Path, tag: str) -> dict:
    """Sample the preview every 100 ms until it shows `want` or `bound` passes."""
    samples = []
    last_other = None
    deadline = since + bound
    while time.time() < deadline:
        t = time.time()
        label, raw = viewer.preview()
        samples.append({"t": round(t - since, 3), "label": label})
        if label == want:
            (out / f"preview-{tag}.jpg").write_bytes(raw)
            return {
                "reached": True,
                "seconds_from_patch": round(t - since, 3),
                "last_other_frame_s": last_other,
                "interruption_s": round(t - since - last_other, 3) if last_other is not None else None,
                "samples": samples,
            }
        if label not in ("none", "?"):
            last_other = round(t - since, 3)
            (out / f"preview-{tag}-last-other.jpg").write_bytes(raw)
        time.sleep(0.1)
    return {"reached": False, "bound_s": bound, "samples": samples}


class HeartbeatWatch(threading.Thread):
    """Polls the registry Query API for the viewer node once a second."""

    def __init__(self, registry: str, node_id: str):
        super().__init__(daemon=True)
        self.url = f"{registry}/x-nmos/query/v1.3/nodes/{node_id}"
        self.codes: list[tuple[float, int | None]] = []
        self.stop_flag = threading.Event()

    def run(self) -> None:
        while not self.stop_flag.is_set():
            code, _ = http("GET", self.url, timeout=2.0)
            self.codes.append((time.time(), code))
            self.stop_flag.wait(1.0)

    def summary(self) -> dict:
        self.stop_flag.set()
        self.join()
        start, end = self.codes[0][0], self.codes[-1][0]
        bad = [c for c in self.codes if c[1] != 200]
        return {
            "polls": len(self.codes),
            "window_s": round(end - start, 1),
            "non_200": len(bad),
            "pass": not bad and end - start >= HEARTBEAT_MIN_WINDOW_S,
        }


def pod_facts() -> dict:
    pod = json.loads(kubectl("-n", NS, "get", "pod", "viewer", "-o", "json"))
    return {
        "uid": pod["metadata"]["uid"],
        "restarts": {c["name"]: c["restartCount"] for c in pod["status"]["containerStatuses"]},
        "started": {c["name"]: c["state"].get("running", {}).get("startedAt") for c in pod["status"]["containerStatuses"]},
    }


def exec_in(container: str, script: str) -> str:
    return kubectl("-n", NS, "exec", "viewer", "-c", container, "--", "sh", "-c", script)


def save_logs(out: Path, tag: str) -> None:
    for container in ("nmos-node", "supervisor"):
        text = kubectl("-n", NS, "logs", "viewer", "-c", container, check=False)
        (out / f"{tag}-{container}.log").write_text(text)
    (out / f"{tag}-nmos-registry.log").write_text(kubectl("-n", NS, "logs", "nmos-registry", check=False))


def deploy_viewer(supervise: bool) -> None:
    text = (HERE / "manifests" / "viewer.yaml").read_text()
    text = text.replace("__SUPERVISE__", "1" if supervise else "0")
    text = text.replace("__STATIC_FLOW_ID__", "" if supervise else FLOW["A"])
    kubectl("-n", NS, "delete", "pod", "viewer", "--ignore-not-found", "--wait=true")
    kubectl("apply", "-f", "-", input_=text)
    kubectl("-n", NS, "wait", "--for=condition=Ready", "pod/viewer", "--timeout=180s")


def deploy_base() -> None:
    kubectl("apply", "-f", "-", input_=kubectl("create", "namespace", NS, "--dry-run=client", "-o", "yaml"))
    # Pods are refused until the namespace's default service account exists.
    deadline = time.time() + 60
    while not kubectl("-n", NS, "get", "serviceaccount", "default", "--ignore-not-found", "-o", "name").strip():
        if time.time() > deadline:
            raise RuntimeError(f"namespace {NS} never got its default service account")
        time.sleep(0.5)
    args = ["-n", NS, "create", "configmap", "mxl-is05-supervisor", "--dry-run=client", "-o", "yaml",
            f"--from-file={HERE / 'supervisor' / 'mxl-is05-supervisor.py'}"]
    kubectl("apply", "-f", "-", input_=kubectl(*args))
    flows = {}
    for k in "AB":
        flow = json.loads((HERE / "flows" / f"flow-{k.lower()}.json").read_text())
        flow["id"] = FLOW[k]
        flows[f"flow-{k.lower()}.json"] = json.dumps(flow, indent=2)
    kubectl("apply", "-f", "-", input_=json.dumps({
        "apiVersion": "v1", "kind": "ConfigMap",
        "metadata": {"name": "mxl-flows", "namespace": NS}, "data": flows,
    }))
    kubectl("apply", "-f", str(HERE / "manifests" / "base.yaml"))
    # The node resolves registry_address on every request. On the local kind
    # cluster each cluster-DNS lookup took 3-6 s, longer than the heartbeat
    # timeout, so the node lost its registration. Using the Service ClusterIP
    # keeps the heartbeat check about the node, not about cluster DNS.
    registry_ip = kubectl(
        "-n", NS, "get", "svc", "nmos-registry", "-o", "jsonpath={.spec.clusterIP}"
    ).strip()
    data = {"registry.json": (HERE / "nmos" / "registry.json").read_text()}
    for name in ("node.json", *SENDER_CONFIG.values()):
        node = json.loads((HERE / "nmos" / name).read_text())
        node["registry_address"] = registry_ip
        data[name] = json.dumps(node, indent=2)
    cm = {
        "apiVersion": "v1", "kind": "ConfigMap",
        "metadata": {"name": "nmos-config", "namespace": NS},
        "data": data,
    }
    kubectl("apply", "-f", "-", input_=json.dumps(cm))
    # Generous: a fresh cluster pulls every image, and the pulls are slow on a
    # loaded host. The timed checks start only after everything is Ready.
    for pod in ("nmos-registry", "source-a", "source-b"):
        kubectl("-n", NS, "wait", "--for=condition=Ready", f"pod/{pod}", "--timeout=600s")


def sender_ids(k: str) -> dict:
    """IDs the fixture-only sender node for source k registers (nmos-cpp's own derivation)."""
    seed = uuid.UUID(json.loads((HERE / "nmos" / SENDER_CONFIG[k]).read_text())["seed_id"])
    return {t: str(uuid.uuid5(seed, f"/x-nmos/node/{t}/xv0")) for t in ("node", "flow", "sender")}


def load_line(name: str | None) -> tuple[str, dict]:
    lines = json.loads((HERE / "crosspoint" / "lines.json").read_text())
    name = name or lines["default"]
    line = lines["lines"][name]
    repo, _, digest = line["image"].partition("@sha256:")
    if not digest or len(digest) != 64 or ":" in repo.rsplit("/", 1)[-1]:
        raise ValueError(f"line {name}: image must be repository@sha256:<digest> with no tag, got {line['image']}")
    return name, line


def deploy_crosspoint(line: dict) -> tuple[str, dict]:
    """Senders and the controller. Returns the fixture login's password and the checked facts."""
    registry_ip = kubectl(
        "-n", NS, "get", "svc", "nmos-registry", "-o", "jsonpath={.spec.clusterIP}"
    ).strip()
    password = secrets.token_hex(16)
    users = {
        "users": {CROSSPOINT_USER: {"password": hashlib.sha256(password.encode()).hexdigest(), "groups": ["user"]}},
        "permissions": {
            scope: {
                "allowRead": {"users": [], "groups": ["user"]},
                "allowWrite": {"users": [], "groups": ["user"]},
                "denyRead": {"users": [], "groups": []},
                "denyWrite": {"users": [], "groups": []},
            } for scope in ("global", "public")
        },
    }
    # As with the nodes, the registry is addressed by ClusterIP: cluster DNS
    # on the local kind cluster is too slow for a heartbeat-driven client.
    settings = {
        "staticNmosRegistries": [{"ip": registry_ip, "port": 8010, "priority": 10, "domain": ""}],
        "logFiles": "./log/",
        "logOutput": True,
        "server": {"port": CROSSPOINT_PORT, "address": "0.0.0.0"},
        "nmos": {"registryVersions": ["v1.3"], "connectVersions": ["v1.2", "v1.1", "v1.0"]},
    }
    kubectl("apply", "-f", "-", input_=json.dumps({
        "apiVersion": "v1", "kind": "Secret", "type": "Opaque",
        "metadata": {"name": "crosspoint-seed", "namespace": NS},
        "stringData": {"settings.json": json.dumps(settings, indent=2), "users.json": json.dumps(users, indent=2)},
    }))
    text = (HERE / "manifests" / "crosspoint.yaml").read_text().replace("__CONTROLLER_IMAGE__", line["image"])
    kubectl("apply", "-f", "-", input_=text)
    for pod in ("sender-a", "sender-b", "crosspoint"):
        kubectl("-n", NS, "wait", "--for=condition=Ready", f"pod/{pod}", "--timeout=600s")

    facts: dict = {"senders": {}}
    pod = json.loads(kubectl("-n", NS, "get", "pod", "crosspoint", "-o", "json"))
    facts["controller_image"] = pod["spec"]["containers"][0]["image"]
    facts["controller_image_id"] = pod["status"]["containerStatuses"][0]["imageID"]
    facts["controller_uid"] = exec_pod("crosspoint", "id -u").strip()
    for k in "AB":
        want = sender_ids(k)
        with port_forward(f"pod/sender-{k.lower()}", 8020) as node:
            deadline = time.time() + 60
            while True:
                try:
                    flows = [f["id"] for f in get_json(f"{node}/x-nmos/node/v1.3/flows")]
                    senders = get_json(f"{node}/x-nmos/node/v1.3/senders")
                    active = get_json(f"{node}/x-nmos/connection/v1.2/single/senders/{want['sender']}/active")
                    break
                except (RuntimeError, ValueError):
                    if time.time() > deadline:
                        raise
                    time.sleep(1)
        leg = active["transport_params"][0]
        facts["senders"][k] = {
            "sender_id": want["sender"],
            "is04_flows": flows,
            "is04_sender_flow_id": [x["flow_id"] for x in senders if x["id"] == want["sender"]],
            "transport": [x["transport"] for x in senders if x["id"] == want["sender"]],
            "active": {"master_enable": active["master_enable"], **leg},
            "pass": (
                flows == [FLOW[k]]
                and [x["flow_id"] for x in senders if x["id"] == want["sender"]] == [FLOW[k]]
                and leg.get("mxl_flow_id") == FLOW[k]
                and leg.get("mxl_domain_id") == DOMAIN_ID
                and active["master_enable"] is True
            ),
        }
    facts["pass"] = all(v["pass"] for v in facts["senders"].values()) and facts["controller_uid"] != "0"
    return password, facts


def exec_pod(pod: str, script: str) -> str:
    return kubectl("-n", NS, "exec", pod, "--", "sh", "-c", script)


class DirectPatch:
    """#624 baseline: the driver PATCHes the viewer's receiver itself."""

    name = "patch"

    def attach(self, stack: contextlib.ExitStack, viewer: "Viewer") -> None:
        pass

    def switch(self, viewer: "Viewer", want: str) -> dict:
        return viewer.patch(FLOW[want])


class CrosspointSwitch:
    """#629 slice: the controller's own connection call PATCHes the receiver.

    Each run opens a fresh controller session after the viewer is deployed and
    waits until the controller lists the viewer's receiver at the pod's
    current address and both senders. The driver never PATCHes.
    """

    name = "crosspoint"

    def __init__(self, password: str):
        self.password = password
        self.calls: list[dict] = []
        self.xp: Crosspoint | None = None

    def attach(self, stack: contextlib.ExitStack, viewer: "Viewer") -> None:
        url = stack.enter_context(port_forward("pod/crosspoint", CROSSPOINT_PORT))
        viewer_ip = kubectl("-n", NS, "get", "pod", "viewer", "-o", "jsonpath={.status.podIP}").strip()
        deadline = time.time() + 120
        while True:
            try:
                self.xp = Crosspoint(url, CROSSPOINT_USER, self.password)
                break
            except (OSError, TimeoutError, ConnectionError):
                if time.time() > deadline:
                    raise
                time.sleep(1)
        stack.callback(self.xp.close)
        while True:
            self.xp.refresh()
            self.destination = self.xp.find(viewer.receiver_id, "receivers")
            self.sources = {k: self.xp.find(sender_ids(k)["sender"], "senders") for k in "AB"}
            # The controller must also know the viewer's current address: after
            # the negative case redeploys the viewer, its control href changes.
            nmos = self.xp.refresh("nmos")
            rx = (nmos.get("receivers") or {}).get(viewer.receiver_id) or {}
            dev = (nmos.get("devices") or {}).get(rx.get("device_id")) or {}
            hrefs = [c.get("href", "") for c in dev.get("controls") or []]
            current = any(f"//{viewer_ip}:" in h for h in hrefs)
            if self.destination and all(self.sources.values()) and current:
                return
            if time.time() > deadline:
                raise RuntimeError(f"controller never listed receiver and senders: dst={self.destination} "
                                   f"src={self.sources} control_hrefs={hrefs} viewer_ip={viewer_ip}")
            time.sleep(1)

    def switch(self, viewer: "Viewer", want: str) -> dict:
        data = {"source": self.sources[want], "destination": self.destination, "preview": False}
        sent = time.time()
        reply = self.xp.request("makeconnection", data)
        done = time.time()
        conns = (reply.get("data") or {}).get("connections") or []
        ok = reply.get("status") == 200 and len(conns) == 1 and conns[0].get("status") == "ok"
        call = {
            "request": data,
            "sent_at": sent,
            "response_at": done,
            "status": 200 if ok else reply.get("status"),
            "controller_ok": ok,
            "response": {
                "status": reply.get("status"),
                "message": reply.get("message"),
                "connections": [{"status": c.get("status"), "detail": c.get("detail")} for c in conns],
            },
            "staged": get_json(f"{viewer.rx}/staged"),
            "active": get_json(f"{viewer.rx}/active"),
            "to": want,
        }
        self.calls.append({k: call[k] for k in ("request", "sent_at", "to", "controller_ok")})
        return call


def capture_patches(out: Path, receiver_id: str, controller_ip: str, calls: list[dict]) -> dict:
    """Read the PATCHes the viewer's node received from the packet capture.

    Each must be the controller's BCP-007-03 receiver PATCH: sender_id of the
    selected source, one leg with mxl_domain_id and mxl_flow_id, master_enable
    true, immediate activation, and no transport file or SDP anywhere in it.
    """
    raw = kubectl(
        "-n", NS, "exec", "capture", "--", "tshark", "-r", "/capture/is05.pcap",
        "-d", "tcp.port==8020,http", "-o", "tcp.relative_sequence_numbers:FALSE",
        "-Y", 'http.request.method == "PATCH"', "-T", "json",
        "-e", "frame.time_epoch", "-e", "ip.src", "-e", "ip.dst", "-e", "tcp.srcport", "-e", "tcp.seq",
        "-e", "http.request.uri", "-e", "http.file_data",
    )
    seen, patches = set(), []
    for pkt in json.loads(raw or "[]"):
        f = {k: v[0] for k, v in pkt["_source"]["layers"].items()}
        key = (f.get("ip.src"), f.get("tcp.srcport"), f.get("tcp.seq"))
        if key in seen:
            continue  # the same segment, seen on both ends of the node's veth pair
        seen.add(key)
        body_text = f.get("http.file_data", "")
        if body_text and not body_text.lstrip().startswith(("{", "[")):
            with contextlib.suppress(ValueError):
                body_text = bytes.fromhex(body_text.replace(":", "")).decode()
        try:
            body = json.loads(body_text)
        except ValueError:
            body = None
        patches.append({
            "t": float(f.get("frame.time_epoch", 0)), "src": f.get("ip.src"), "dst": f.get("ip.dst"),
            "uri": f.get("http.request.uri"), "body_text": body_text, "body": body,
        })
    # Capture times come from the node's clock, not the driver's, so PATCHes
    # pair with connection calls by order only.
    patches.sort(key=lambda x: x["t"])
    to_rx = [x for x in patches if (x["uri"] or "").endswith(f"/single/receivers/{receiver_id}/staged")]
    flow_to_label = {v: k for k, v in FLOW.items()}
    rows, ok = [], len(to_rx) == len(calls)
    for x, call in zip(to_rx, calls):
        b = x["body"] or {}
        legs = b.get("transport_params") or []
        leg = legs[0] if legs else {}
        row = {
            "from_controller": x["src"] == controller_ip,
            "flow": flow_to_label.get(leg.get("mxl_flow_id"), leg.get("mxl_flow_id")),
            "expected_flow": call["to"],
            "sender_id_matches": b.get("sender_id") == sender_ids(call["to"])["sender"],
            "one_leg_mxl_only": len(legs) == 1 and set(leg) == {"mxl_domain_id", "mxl_flow_id"},
            "domain": leg.get("mxl_domain_id"),
            "master_enable": b.get("master_enable"),
            "activation_mode": (b.get("activation") or {}).get("mode"),
            "no_transport_file": "transport_file" not in b,
            "no_sdp": "v=0" not in x["body_text"] and "application/sdp" not in x["body_text"],
        }
        row["pass"] = (
            row["from_controller"] and row["flow"] == call["to"] and row["sender_id_matches"]
            and row["one_leg_mxl_only"] and row["domain"] in ("auto", DOMAIN_ID)
            and row["master_enable"] is True and row["activation_mode"] == "activate_immediate"
            and row["no_transport_file"] and row["no_sdp"]
        )
        ok = ok and row["pass"]
        rows.append(row)
    (out / "captured-patches.json").write_text(json.dumps(patches, indent=2))
    return {
        "patches_to_receiver": len(to_rx),
        "controller_calls": len(calls),
        "first_patch_as_received": to_rx[0]["body"] if to_rx else None,
        "rows": rows,
        "pass": ok and bool(rows),
    }


def check_flows_readable(out: Path) -> dict:
    """Item 1: both source flows advance, read from inside the viewer pod."""
    script = (
        "for f in %s %s; do mxl-info \"mxl:///home/mxl/domain?id=$f\" 2>&1 "
        "| sed -n 's/.*Head index: *//p'; done" % (FLOW["A"], FLOW["B"])
    )
    first = exec_in("supervisor", script).split()
    time.sleep(1.0)
    second = exec_in("supervisor", script).split()
    listing = exec_in("supervisor", "mxl-info -d /home/mxl/domain -l 2>&1")
    heads = {
        k: {"t0": int(a), "t1": int(b), "advanced": int(b) > int(a)}
        for k, a, b in zip("AB", first, second)
    }
    # Read frames from both flows at once: two sinks side by side for 4 s.
    tail = ("video/x-raw,width=320,height=180 ! videorate drop-only=true max-rate=5 ! jpegenc ! "
            "multifilesink location=/tmp/preview/concurrent-%s.jpg max-files=1 post-messages=false sync=false")
    both = " & ".join(
        f"MXL_GST_VIDEO_SINK='{tail % k}' timeout 4 mxl-gst-sink -d /home/mxl/domain -v {FLOW[k]} >/dev/null 2>&1"
        for k in "AB"
    )
    exec_in("supervisor", f"rm -f /tmp/preview/concurrent-*.jpg; {both} & wait")
    concurrent = {}
    for k in "AB":
        raw = base64.b64decode(exec_in("supervisor", f"base64 /tmp/preview/concurrent-{k}.jpg 2>/dev/null || true"))
        if raw:
            (out / f"concurrent-{k.lower()}.jpg").write_bytes(raw)
        concurrent[k] = classify(raw) if raw else "none"
    return {
        "listing": listing.strip().splitlines(),
        "head_index": heads,
        "concurrent_read_labels": concurrent,
        "pass": (
            len(heads) == 2
            and all(h["advanced"] for h in heads.values())
            and concurrent == {"A": "A", "B": "B"}
        ),
    }


def check_selection(step: dict, flow_id: str) -> dict:
    staged = step["staged"]["transport_params"][0]
    active = step["active"]["transport_params"][0]
    ok = (
        step["status"] in (200, 202)
        and step.get("controller_ok", True)
        and staged["mxl_flow_id"] == flow_id
        and staged["mxl_domain_id"] in ("auto", DOMAIN_ID)
        and active["mxl_flow_id"] == flow_id
        and active["mxl_domain_id"] == DOMAIN_ID
        and step["active"]["master_enable"] is True
    )
    return {"staged": staged, "active": active, "pass": ok}


def check_non_root() -> dict:
    node_uid = exec_in("nmos-node", "id -u").strip()
    sup_uid = exec_in("supervisor", "id -u").strip()
    child = exec_in(
        "supervisor",
        "for p in /proc/[0-9]*; do c=$(tr '\\0' ' ' < $p/cmdline 2>/dev/null); "
        "case \"$c\" in mxl-gst-sink*) awk '/^Uid:/{print $2}' $p/status;; esac; done",
    ).split()
    spec = json.loads(kubectl("-n", NS, "get", "pod", "viewer", "-o", "jsonpath={.spec.securityContext}"))
    ok = node_uid != "0" and sup_uid != "0" and child and all(u != "0" for u in child) and spec.get("runAsNonRoot") is True
    return {"nmos_node_uid": node_uid, "supervisor_uid": sup_uid, "preview_child_uids": child, "pod_security_context": spec, "pass": bool(ok)}


def run_main(out: Path, switcher) -> dict:
    obs: dict = {"switch_via": switcher.name}
    deploy_viewer(supervise=True)
    with contextlib.ExitStack() as stack:
        viewer = Viewer(stack)
        switcher.attach(stack, viewer)
        registry = stack.enter_context(port_forward("svc/nmos-registry", 8010))
        node_id = viewer.node_id()
        obs["receiver_id"] = viewer.receiver_id
        obs["node_id"] = node_id
        deadline = time.time() + 60
        while http("GET", f"{registry}/x-nmos/query/v1.3/nodes/{node_id}")[0] != 200:
            if time.time() > deadline:
                raise RuntimeError("viewer node never registered")
            time.sleep(1)
        hb = HeartbeatWatch(registry, node_id)
        hb.start()

        obs["flows_readable"] = check_flows_readable(out)
        before = pod_facts()
        sup_before = viewer.status()

        # A, then B (the acceptance pair), then more A/B pairs so the interval
        # is a distribution, not one sample.
        sequence = ["A", "B"] + ["A", "B"] * EXTRA_PAIRS
        switches = []
        for i, want in enumerate(sequence):
            log(f"switch {i + 1}/{len(sequence)}: {switcher.name} to flow {want}")
            step = switcher.switch(viewer, want)
            sel = check_selection(step, FLOW[want])
            seen = wait_for_pattern(viewer, want, step["sent_at"], SWITCH_BOUND_S, out, f"{i + 1:02d}-{want.lower()}")
            switches.append({"to": want, "patch_sent_at": step["sent_at"], "http_status": step["status"],
                             "selection": sel, "preview": seen,
                             "controller": step.get("response") if switcher.name == "crosspoint" else None})
            if i == 1:
                obs["patch_a"] = {**switches[0]["selection"], "http_status": switches[0]["http_status"]}
                obs["patch_b"] = {**sel, "http_status": step["status"]}
            time.sleep(SETTLE_S)  # start each switch from a live picture
        after_status = viewer.status()
        after = pod_facts()

        remaining = HEARTBEAT_MIN_WINDOW_S - (time.time() - hb.codes[0][0])
        if remaining > 0:
            time.sleep(remaining + 1)
        obs["heartbeat"] = hb.summary()
        obs["non_root"] = check_non_root()
        save_logs(out, "main")

    events = [e for e in after_status["events"] if e["reason"] == "active-changed"]
    obs["supervisor_events"] = events
    pids = [e["new_pid"] for e in events]
    chained = all(events[i]["old_pid"] == pids[i - 1] for i in range(1, len(events)))
    obs["pod_identity"] = {
        "before": before,
        "after": after,
        "supervisor_pid": [sup_before["supervisor_pid"], after_status["supervisor_pid"]],
        "supervisor_started_at": [sup_before["started_at"], after_status["started_at"]],
        "preview_child_pids": pids,
        "other_child_exits": [e for e in after_status["events"] if e["reason"] != "active-changed"],
        "pass": (
            before["uid"] == after["uid"]
            and before["restarts"] == after["restarts"]
            and before["started"] == after["started"]
            and sup_before["supervisor_pid"] == after_status["supervisor_pid"]
            and sup_before["started_at"] == after_status["started_at"]
            and len(events) == len(sequence)
            and all(pids)
            and len(set(pids)) == len(pids)
            and chained
            and after_status["child_starts"] == len(sequence)
        ),
    }
    rows = []
    for sw, ev in zip(switches, events):
        rows.append({
            "to": sw["to"],
            "shown_after_patch_s": sw["preview"].get("seconds_from_patch"),
            "interruption_s": sw["preview"].get("interruption_s"),
            "supervisor_observed_after_patch_s": round(ev["observed_at"] - sw["patch_sent_at"], 3),
            "child_stop_s": round(ev["stopped_at"] - ev["observed_at"], 3),
            "selection_pass": sw["selection"]["pass"],
            **({"controller": sw["controller"]} if sw["controller"] else {}),
        })
    obs["switches"] = rows
    shown = sorted(r["shown_after_patch_s"] for r in rows if r["shown_after_patch_s"] is not None)
    gaps = sorted(r["interruption_s"] for r in rows[1:] if r["interruption_s"] is not None)
    obs["switch"] = {
        "bound_s": SWITCH_BOUND_S,
        "count": len(rows),
        "sample_period_s": 0.1,
        "shown_after_patch_s": {"min": shown[0], "median": shown[len(shown) // 2], "max": shown[-1]} if shown else None,
        "interruption_s": {"min": gaps[0], "median": gaps[len(gaps) // 2], "max": gaps[-1]} if gaps else None,
        "pass": all(sw["preview"]["reached"] for sw in switches) and all(r["selection_pass"] for r in rows),
    }
    return obs


def run_negative(out: Path, switcher) -> dict:
    """Same switches with the supervisor in static mode: /active moves, the picture must not."""
    deploy_viewer(supervise=False)
    with contextlib.ExitStack() as stack:
        viewer = Viewer(stack)
        switcher.attach(stack, viewer)
        start_a = wait_for_pattern(viewer, "A", time.time(), 30.0, out, "negative-start")
        step_a = switcher.switch(viewer, "A")
        step_b = switcher.switch(viewer, "B")
        sel_b = check_selection(step_b, FLOW["B"])
        result = wait_for_pattern(viewer, "B", step_b["sent_at"], SWITCH_BOUND_S, out, "negative-b")
        labels = sorted({s["label"] for s in result["samples"]})
        save_logs(out, "negative")
        return {
            "static_preview_showed_a": start_a["reached"],
            "patch_a_http_status": step_a["status"],
            "active_after_patch_b": sel_b,
            "preview_reached_b": result["reached"],
            "preview_labels_seen": labels,
            # Pass = /active did select B, yet the preview assertion could not be met.
            "pass": start_a["reached"] and sel_b["pass"] and not result["reached"],
        }


def run_nmos_testing(out: Path, sender_pods: tuple[str, ...] = ()) -> dict:
    """Second gate, warn mode: IS-05-01 and BCP-007-03-01 against the viewer node.

    The crosspoint slice also runs BCP-007-03-01 against a sender node, so the
    sender-side cases that had no resources in #624 have one.
    """
    deploy_viewer(supervise=True)
    ip = kubectl("-n", NS, "get", "pod", "viewer", "-o", "jsonpath={.status.podIP}").strip()
    suites = {
        "IS-05-01": ("IS-05-01", ["--host", ip, "--port", "8020", "--version", "v1.2"], ip),
        "BCP-007-03-01": ("BCP-007-03-01", ["--host", ip, ip, "--port", "8020", "8020", "--version", "v1.3", "v1.2"], ip),
    }
    for pod in sender_pods:
        pip = kubectl("-n", NS, "get", "pod", pod, "-o", "jsonpath={.status.podIP}").strip()
        suites[f"BCP-007-03-01-{pod}"] = (
            "BCP-007-03-01", ["--host", pip, pip, "--port", "8020", "8020", "--version", "v1.3", "v1.2"], pip)
    results = {"image": NMOS_TESTING_IMAGE, "suites": {}}
    for label, (suite, args, ip) in suites.items():
        name = "nmos-testing-" + label.lower()
        cmd = " ".join(
            ["python3", "nmos-test.py", "suite", suite, *args, "--output", "/tmp/result.json"]
        )
        script = f"{cmd} > /tmp/log.txt 2>&1; echo \"exit=$?\"; echo '-----JSON-----'; cat /tmp/result.json"
        kubectl("-n", NS, "delete", "pod", name, "--ignore-not-found", "--wait=true")
        kubectl(
            "-n", NS, "run", name, "--restart=Never", f"--image={NMOS_TESTING_IMAGE}",
            "--command", "--", "sh", "-c", script,
        )
        phase = ""
        deadline = time.time() + 900
        while time.time() < deadline and phase not in ("Succeeded", "Failed"):
            time.sleep(5)
            phase = kubectl("-n", NS, "get", "pod", name, "-o", "jsonpath={.status.phase}", check=False).strip()
        # Saved results are shared as a reference, so drop the pod address.
        logs = kubectl("-n", NS, "logs", name, check=False).replace(ip, "node-under-test-ip")
        head, _, body = logs.partition("-----JSON-----")
        (out / f"{name}.raw.txt").write_text(logs)
        try:
            data = json.loads(body)
        except ValueError:
            results["suites"][label] = {"error": "no JSON result", "pod_phase": phase, "head": head.strip()[-2000:]}
            continue
        (out / f"{name}.json").write_text(json.dumps(data, indent=2))
        counts: dict[str, int] = {}
        for r in data.get("results", []):
            counts[r["state"]] = counts.get(r["state"], 0) + 1
        results["suites"][label] = {
            "counts": counts,
            "cases": [{"name": r["name"], "state": r["state"], "detail": r.get("detail", "")} for r in data.get("results", [])],
        }
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--skip-nmos-testing", action="store_true")
    parser.add_argument("--switch-via", choices=["patch", "crosspoint"], default="patch",
                        help="patch: the driver PATCHes the receiver (#624); "
                             "crosspoint: the controller's connection call does (#629)")
    parser.add_argument("--controller-line", help="line in crosspoint/lines.json (default: its 'default')")
    args = parser.parse_args()
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)

    report: dict = {"started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    nodes = json.loads(kubectl("get", "nodes", "-o", "json"))["items"]
    report["cluster"] = [
        {"arch": n["status"]["nodeInfo"]["architecture"], "kubelet": n["status"]["nodeInfo"]["kubeletVersion"],
         "os": n["status"]["nodeInfo"]["osImage"], "kernel": n["status"]["nodeInfo"]["kernelVersion"]}
        for n in nodes
    ]
    report["switch_via"] = args.switch_via
    if args.switch_via == "crosspoint":
        line_name, line = load_line(args.controller_line)
        report["controller_line"] = {"name": line_name, **line}
        FLOW.update({k: sender_ids(k)["flow"] for k in "AB"})
    log("deploying registry and sources")
    deploy_base()
    switcher = DirectPatch()
    if args.switch_via == "crosspoint":
        log(f"deploying fixture senders and the {line_name} controller {line['image']}")
        password, facts = deploy_crosspoint(line)
        report["controller"] = facts
        kubectl("apply", "-f", str(HERE / "manifests" / "capture.yaml"))
        kubectl("-n", NS, "wait", "--for=condition=Ready", "pod/capture", "--timeout=600s")
        switcher = CrosspointSwitch(password)
    log("main run (supervised)")
    report["main"] = run_main(out, switcher)
    log("negative case (supervisor ignores /active)")
    report["negative"] = run_negative(out, switcher)
    if args.switch_via == "crosspoint":
        controller_ip = kubectl("-n", NS, "get", "pod", "crosspoint", "-o", "jsonpath={.status.podIP}").strip()
        report["captured_patch"] = capture_patches(out, report["main"]["receiver_id"], controller_ip, switcher.calls)
        (out / "crosspoint.log").write_text(kubectl("-n", NS, "logs", "crosspoint", check=False))
    if not args.skip_nmos_testing:
        log("nmos-testing (warn mode)")
        try:
            report["nmos_testing"] = run_nmos_testing(
                out, ("sender-a",) if args.switch_via == "crosspoint" else ())
        except Exception as exc:  # noqa: BLE001 - warn mode never fails the run
            report["nmos_testing"] = {"error": str(exc)}

    m = report["main"]
    report["acceptance"] = {
        "1_two_flows_readable_in_viewer_domain": m["flows_readable"]["pass"],
        "2_staged_and_active_carry_selection": m["patch_a"]["pass"] and m["patch_b"]["pass"],
        "3_pod_uid_kept_only_preview_child_replaced": m["pod_identity"]["pass"],
        "4_preview_changes_a_to_b_within_bound": m["switch"]["pass"],
        "5_node_keeps_heartbeating": m["heartbeat"]["pass"],
        "6_negative_case_cannot_satisfy_preview": report["negative"]["pass"],
        "7_node_and_supervisor_non_root": m["non_root"]["pass"],
    }
    if args.switch_via == "crosspoint":
        report["slice_acceptance"] = {
            "controller_by_digest_non_root_senders_advertise_source_flows": report["controller"]["pass"],
            "controller_patch_is_bcp_007_03_without_sdp_or_transport_file": report["captured_patch"]["pass"],
        }
    report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    (out / "observations.json").write_text(json.dumps(report, indent=2))
    for item, ok in report["acceptance"].items():
        log(f"{'PASS' if ok else 'FAIL'}  {item}")
    log(f"switches: {m['switch']['count']}, shown after PATCH {m['switch']['shown_after_patch_s']} s, "
        f"interruption {m['switch']['interruption_s']} s (bound {SWITCH_BOUND_S} s)")
    for suite, res in report.get("nmos_testing", {}).get("suites", {}).items():
        log(f"nmos-testing {suite}: {res.get('counts', res)}")
    for item, ok in report.get("slice_acceptance", {}).items():
        log(f"{'PASS' if ok else 'FAIL'}  slice: {item}")
    passed = all(report["acceptance"].values()) and all(report.get("slice_acceptance", {}).values())
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
