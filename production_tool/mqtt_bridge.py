#!/usr/bin/python
# -*- coding: utf-8 -*-
"""
mqtt_bridge.py  -  Wi-SUN B-route -> ECHONET Lite -> Home Assistant MQTT
Python 2.7 stdlib only: termios, fcntl, select, socket, struct, json, os
"""

from __future__ import print_function

import os
import sys
import json
import time
import struct
import socket
import select
import binascii
import termios
import fcntl
import collections
import re
import threading
import logging
import logging.handlers
import base64
import hmac
import tempfile
import subprocess

try:
    # Python 2.7
    import BaseHTTPServer
    import urlparse
except ImportError:
    # Python 3 (for host tests)
    from http import server as BaseHTTPServer
    import urllib.parse as urlparse

# Bridge self-version. SemVer is updated manually; git short hash is overwritten
# by scripts/embed_git_hash.sh before USB distribution. When the embed script
# is skipped the version reports "<semver>+unknown" (FR-001 fallback).
BRIDGE_SEMVER = "1.0.0"
BRIDGE_GIT_HASH = "unknown"

# Python 2/3 compatible string type tuple. json.loads returns `unicode` on
# Python 2, plain `str` on Python 3 — both must be accepted as "text".
try:
    _TEXT_TYPES = (str, unicode)  # noqa: F821 (unicode only exists on Py2)
except NameError:
    _TEXT_TYPES = (str,)

CONFIG_PATH = "/data/local/config.json"
LOG_PATH    = "/data/local/mqtt_bridge.log"

LED_R = "/sys/class/leds/red/brightness"
LED_G = "/sys/class/leds/green/brightness"
LED_B = "/sys/class/leds/blue/brightness"

def led_rgb(r, g, b):
    for path, val in ((LED_R, r), (LED_G, g), (LED_B, b)):
        try:
            with open(path, 'w') as f:
                f.write(str(val) + '\n')
        except Exception:
            pass

def led_read():
    result = []
    for path in (LED_R, LED_G, LED_B):
        try:
            with open(path) as f:
                result.append(int(f.read().strip()))
        except Exception:
            result.append(0)
    return tuple(result)

LOGGER = None  # Set in main() once config is loaded.


def log(msg):
    """Legacy plain-text log function used by call sites that haven't been
    converted to named events yet. Routes through the JSON logger when
    available, falling back to stderr otherwise (preserves the old fallback
    behaviour from FR-009).
    """
    if LOGGER is not None:
        try:
            LOGGER.info(event="log", msg=msg)
            return
        except Exception:
            pass
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    sys.stderr.write("[{}] {}\n".format(ts, msg))
    sys.stderr.flush()

def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def apply_defaults(cfg):
    """Return a copy of *cfg* with optional observability + admin keys filled in.

    Preserves every existing entry (FR-012 / 003 FR-018). Unknown keys are
    passed through unchanged so future config additions don't crash older
    bridge versions.
    """
    out = dict(cfg)
    out.setdefault("log_level", "info")
    out.setdefault("log_max_bytes", 1048576)
    out.setdefault("log_backup_count", 3)
    out.setdefault("admin_ui_enabled", False)
    out.setdefault("admin_ui_port", 8080)
    out.setdefault("admin_user", "")
    out.setdefault("admin_password", "")
    # Number of consecutive ERXUDP timeouts before the bridge forces a
    # Wi-SUN re-join. 0 = disabled (legacy behaviour). Default 5 corresponds
    # to ~5 minutes of silence at the default poll_interval.
    out.setdefault("erxudp_timeout_force_reconnect_threshold", 5)
    # MQTT keep-alive (seconds). Cube J1 のメインループは ECHONET Lite
    # の同期 poll で詰まることがあり、上流デフォルトの 60s では broker から
    # 切断される（実測 約 12 分間隔）。300s なら poll が一時的に滞っても
    # PINGREQ をギリギリ間に合わせられる。
    out.setdefault("mqtt_keepalive", 300)
    # MQTT クライアントを送信ワーカ + keepalive ワーカに分離 (spec 005)。
    # メインループの ECHONET poll が詰まっても broker session を維持する。
    # 切り戻し用の fallback として False で従来パスに戻せる。
    out.setdefault("mqtt_threading_enabled", True)
    out.setdefault("mqtt_send_queue_maxsize", 1000)
    # spec 010: periodic EEDSCAN to track 920MHz noise floor. 5 min default
    # is a balance between visibility and meter-poll interference (~12s
    # sweep stops meter traffic for that cycle).
    out.setdefault("eedscan_enabled", True)
    out.setdefault("eedscan_interval_sec", 300)
    # spec 011: ERXUDP resilience. timeout 30s + 2 intra-cycle retries
    # with 2s backoff covers the p95 5s tail and the rare 10-20s outliers.
    out.setdefault("erxudp_timeout_sec", 30)
    out.setdefault("erxudp_intra_cycle_retries", 2)
    out.setdefault("erxudp_retry_backoff_sec", 2)
    # spec 013: poll_interval default 60s + ARIB STD-T108 floor 30s.
    # Faster polling risks exceeding the 360s/hour duty cycle once
    # retries/reconnects are factored in.
    out.setdefault("poll_interval", 60)
    if int(out["poll_interval"]) < MIN_POLL_INTERVAL_SEC:
        log("WARN: poll_interval={} below floor, clamping to {}".format(
            out["poll_interval"], MIN_POLL_INTERVAL_SEC))
        out["poll_interval"] = MIN_POLL_INTERVAL_SEC
    return out


def should_retry_in_cycle(attempt, max_retries):
    """True while `attempt` (0-indexed try number) is inside the retry
    budget. attempt=0 means the first SKSENDTO failed; we return True up to
    and including `max_retries - 1`. Negative attempt is treated as 0."""
    return int(attempt) < int(max_retries)


# ---------------------------------------------------------------------------
# ProbeState: high-frequency RTT sampling toggle (spec 009)
# ---------------------------------------------------------------------------

class ProbeState(object):
    """In-memory probe-mode flag. Time-limited; auto-disables once the
    `deadline_ts` is crossed. The main loop checks `is_active(now)` before
    every cycle to decide which EPC list / interval to use."""

    def __init__(self):
        self.active = False
        self.interval_sec = 0
        self.deadline_ts = 0.0

    def start(self, interval_sec, duration_sec, now):
        if int(interval_sec) <= 0:
            raise ValueError("interval_sec must be > 0")
        if int(duration_sec) <= 0:
            raise ValueError("duration_sec must be > 0")
        self.active = True
        self.interval_sec = int(interval_sec)
        self.deadline_ts = float(now) + float(duration_sec)

    def stop(self):
        self.active = False
        self.interval_sec = 0
        self.deadline_ts = 0.0

    def is_active(self, now):
        return bool(self.active and now < self.deadline_ts)

    def snapshot(self, now):
        active = self.is_active(now)
        remaining = max(0, int(self.deadline_ts - now)) if active else 0
        return {
            "active": active,
            "interval_sec": self.interval_sec if active else 0,
            "deadline_ts": self.deadline_ts,
            "remaining_sec": remaining,
        }


# ---------------------------------------------------------------------------
# EEDSCAN: 920MHz Energy Detection Scan (spec 010)
# ---------------------------------------------------------------------------

def parse_eedscan(payload):
    """Parse the EEDSCAN data line `0 <ch> <energy> <ch> <energy> ...` into a
    `{channel: energy}` dict. Both channel and energy are 1-byte hex. The
    leading 0 is a status byte; the first hex token is treated as that
    status and skipped. A trailing dangling channel token (no paired
    energy) is dropped. Non-hex garbage tokens are skipped silently."""
    if not payload:
        return {}
    tokens = payload.split()
    out = {}
    i = 0
    seen_status = False
    while i < len(tokens):
        t = tokens[i]
        try:
            int(t, 16)
        except ValueError:
            i += 1
            continue
        if not seen_status:
            seen_status = True
            i += 1
            continue
        if i + 1 >= len(tokens):
            break
        try:
            ch = int(t, 16)
            energy = int(tokens[i + 1], 16)
            out[ch] = energy
            i += 2
        except ValueError:
            i += 1
    return out


class EedScanState(object):
    """Last EEDSCAN result + a short rolling history. The main loop polls
    `should_run(now)` to decide when to fire another sweep; the diag
    publisher reads `snapshot()` to emit 920MHz noise-floor metrics."""

    HISTORY_MAX = 100

    def __init__(self, interval_sec=300):
        self.interval_sec = int(interval_sec)
        self.last_run_ts = 0.0
        self.last_result = {}
        self.recent = collections.deque(maxlen=self.HISTORY_MAX)

    def should_run(self, now):
        return (now - self.last_run_ts) >= self.interval_sec

    def record(self, result, ts):
        self.last_run_ts = float(ts)
        self.last_result = dict(result)
        self.recent.append((float(ts), dict(result)))

    def snapshot(self, pan_channel=None):
        if not self.last_result:
            return {}
        energies = list(self.last_result.values())
        out = {
            "eedscan_max_energy": max(energies),
            "eedscan_min_energy": min(energies),
        }
        if pan_channel is not None and pan_channel in self.last_result:
            out["eedscan_pan_channel_energy"] = self.last_result[pan_channel]
        return out


def decide_cycle_kind(probe_active, last_normal_start, now, poll_interval):
    """Pick "probe" or "normal" for the next poll cycle. In probe mode we
    interleave fast 0x80 probes with the usual 0xE7 measurements so HA
    still gets power values at the regular cadence.

    Spec 009: a normal cycle runs whenever the previous one was at least
    poll_interval seconds ago. The very first cycle of a probe session is
    also normal so the rolling window starts with a fresh measurement.
    """
    if not probe_active:
        return "normal"
    if last_normal_start <= 0:
        return "normal"
    if (now - last_normal_start) >= float(poll_interval):
        return "normal"
    return "probe"


def compute_next_poll_sleep(last_poll_start, now, poll_interval):
    """How long to sleep so the next poll begins `poll_interval` seconds after
    the *start* of the previous one (deadline-based pacing). Clamped to 0 so
    that an overrun (ERXUDP timeout) does not waste an extra cycle of silence.
    """
    deadline = last_poll_start + float(poll_interval)
    remaining = deadline - now
    if remaining <= 0:
        return 0.0
    return remaining


# ---------------------------------------------------------------------------
# Embedded admin UI: pure helpers
# ---------------------------------------------------------------------------

# Path to the live config the bridge reads on startup. The admin UI reads /
# atomically rewrites this file so the next bridge restart picks up the new
# values. Tests can override these constants.
ADMIN_CONFIG_PATH = "/data/local/config.json"
ADMIN_BRIDGE_PATH = "/data/local/mqtt_bridge.py"
ADMIN_WPA_PATH    = "/data/misc/wifi/wpa_supplicant.conf"
ADMIN_LOG_PATH    = LOG_PATH


class AdminConfig(object):
    """Immutable admin UI configuration loaded from config.json."""

    def __init__(self, enabled, port, user, password):
        self.enabled  = bool(enabled)
        self.port     = int(port)
        self.user     = user or ""
        self.password = password or ""

    def is_active(self):
        return bool(self.enabled and self.user and self.password)

    def match_basic_auth(self, header_value):
        """Constant-time compare an `Authorization: Basic ...` header value.

        Returns False for any malformed input rather than raising.
        """
        if not header_value or not isinstance(header_value, str):
            return False
        if not header_value.startswith("Basic "):
            return False
        encoded = header_value[6:].strip()
        try:
            # Constitution II — Python 2.7 stdlib only. The `validate` kwarg
            # only landed in CPython 2.7.6, but Cube J1 ships 2.7.13 *without*
            # it on this build, so stay on the lowest-common-denominator API.
            # An accidentally-junk decode just yields junk and fails the
            # subsequent string compare anyway.
            decoded = base64.b64decode(encoded.encode("ascii")).decode("utf-8")
        except Exception:
            return False
        if ":" not in decoded:
            return False
        sent_user, _, sent_pwd = decoded.partition(":")
        # Use compare_digest on bytes so the same-length check is constant.
        u_ok = hmac.compare_digest(
            sent_user.encode("utf-8"), self.user.encode("utf-8"))
        p_ok = hmac.compare_digest(
            sent_pwd.encode("utf-8"), self.password.encode("utf-8"))
        return bool(u_ok and p_ok)


class AtomicWriter(object):
    """File writes via temp + os.rename so the target is never half-written."""

    @staticmethod
    def write_bytes(path, data):
        dir_ = os.path.dirname(os.path.abspath(path)) or "."
        fd, tmp = tempfile.mkstemp(prefix=".tmp.", dir=dir_)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass  # fsync may fail on some FS — best-effort
            os.rename(tmp, path)
            tmp = None  # rename consumed it
        finally:
            if tmp is not None and os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    @staticmethod
    def write_json(path, obj):
        AtomicWriter.write_bytes(
            path, json.dumps(obj, indent=2, separators=(",", ": ")).encode("utf-8"))


_VALID_LOG_LEVELS = ("debug", "info", "warn", "error")

# spec 013: ARIB STD-T108 920MHz duty cycle (360s/hour) safety floor.
# Polling faster than 30s risks exceeding the transmit-time limit once
# retries/reconnects are factored in.
MIN_POLL_INTERVAL_SEC = 30

_POSITIVE_INT_KEYS = (
    "mqtt_port", "log_max_bytes", "log_backup_count",
    "admin_ui_port",
)


def validate_config_patch(patch, current):
    """Merge *patch* into *current* with type/range checks (FR-010).

    Returns (merged_dict, None) on success, (None, error_message) on failure.
    Unknown keys are passed through.
    """
    merged = dict(current)
    for key, value in patch.items():
        if key in _POSITIVE_INT_KEYS:
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                return None, "{} must be a positive integer".format(key)
        elif key == "poll_interval":
            if (not isinstance(value, int) or isinstance(value, bool)
                    or value < MIN_POLL_INTERVAL_SEC):
                return None, ("poll_interval must be an integer >= {} seconds "
                              "(ARIB STD-T108 920MHz duty cycle)"
                              .format(MIN_POLL_INTERVAL_SEC))
        elif key == "log_level":
            if value not in _VALID_LOG_LEVELS:
                return None, ("log_level must be one of {}"
                              .format("/".join(_VALID_LOG_LEVELS)))
        elif key == "admin_ui_enabled":
            if not isinstance(value, bool):
                return None, "admin_ui_enabled must be true or false"
        elif key == "admin_password" and value == "***":
            value = current.get("admin_password", "")
        merged[key] = value
    return merged, None


def validate_wifi_patch(payload):
    """Verify SSID/PSK shape per WPA2 PSK spec.

    Returns (normalized_dict, None) on success, (None, error_message)
    on failure. PSK must be 8..63 characters per IEEE 802.11i.
    """
    ssid = payload.get("ssid")
    psk = payload.get("psk")
    # Accept both Python 2 `str`/`unicode` and Python 3 `str`. json.loads
    # on Py2 always returns `unicode` for string values, so a plain
    # isinstance(ssid, str) check incorrectly rejects every JSON body
    # arriving on the Cube J1 (Python 2.7.13).
    if not isinstance(ssid, _TEXT_TYPES) or not ssid.strip():
        return None, "ssid is required"
    if not isinstance(psk, _TEXT_TYPES):
        return None, "psk must be a string"
    if len(psk) < 8 or len(psk) > 63:
        return None, "psk must be 8-63 characters"
    return {"ssid": ssid, "psk": psk}, None


def read_config_masked(path):
    """Read config.json and replace admin_password with '***' if present."""
    with open(path) as f:
        cfg = json.load(f)
    if "admin_password" in cfg:
        cfg["admin_password"] = "***"
    return cfg


_TAIL_LOG_MAX = 1000
_TAIL_LOG_READ_CHUNK = 4096


def tail_log(path, n):
    """Return the last `n` non-empty lines of `path` (clamped to 1..1000).

    Reads the file backwards in CHUNK-sized blocks so it stays cheap even for
    multi-megabyte rotated logs. Missing file returns an empty list.
    """
    if not isinstance(n, int) or n < 1:
        n = 1
    if n > _TAIL_LOG_MAX:
        n = _TAIL_LOG_MAX
    try:
        f = open(path, "rb")
    except (IOError, OSError):
        return []
    try:
        f.seek(0, os.SEEK_END)
        end = f.tell()
        buf = b""
        lines = []
        pos = end
        while pos > 0 and len(lines) <= n:
            read_size = min(_TAIL_LOG_READ_CHUNK, pos)
            pos -= read_size
            f.seek(pos)
            buf = f.read(read_size) + buf
            lines = buf.split(b"\n")
        decoded = [line.decode("utf-8", errors="replace") for line in lines]
        non_empty = [line for line in decoded if line.strip()]
        return non_empty[-n:]
    finally:
        f.close()


# ---------------------------------------------------------------------------
# Embedded admin UI: HTTP handler / server
# ---------------------------------------------------------------------------

ADMIN_HTML = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<title>Cube J1 Admin</title>
<style>
body { font-family: -apple-system, system-ui, sans-serif; margin: 2rem auto;
       max-width: 760px; color: #222; }
h1 { font-size: 1.4rem; }
fieldset { border: 1px solid #ccc; padding: 1rem; margin: 1rem 0; }
legend { padding: 0 .4rem; font-weight: 600; }
label { display: block; margin: .4rem 0; font-size: .9rem; }
input, select { width: 100%; box-sizing: border-box; padding: .4rem .6rem;
                 font: inherit; }
button { padding: .55rem 1.2rem; font: inherit; cursor: pointer; }
.ok { color: #186a3b; } .err { color: #b71c1c; }
pre { background: #f4f4f4; padding: .6rem; font-size: .8rem;
      max-height: 12rem; overflow: auto; }
.notice { background: #fff8e1; border-left: 3px solid #f57f17; padding: .5rem;
          font-size: .85rem; margin: .5rem 0; }
</style>
</head>
<body>
<h1>Cube J1 Admin <a href="/wisun" style="font-size:14px;font-weight:normal;color:#5af">[ Wi-SUN Quality (real-time) ]</a></h1>
<div id="status"></div>
<fieldset><legend>Config</legend>
  <form id="config-form"></form>
  <button id="config-save" type="button">Save Config</button>
</fieldset>
<fieldset><legend>Wi-Fi</legend>
  <p class="notice">&#9888; Wi-Fi 変更後に AP に再接続できないと Cube J1 は
     LAN から見えなくなります。USB 経由で復旧する覚悟がある場合のみ実行してください.</p>
  <form id="wifi-form">
    <label>SSID <input name="ssid" required></label>
    <label>PSK  <input name="psk" type="password" required minlength="8" maxlength="63"></label>
    <button type="submit">Save Wi-Fi</button>
  </form>
</fieldset>
<fieldset><legend>AP Mode (CubeJ-...)</legend>
  <p class="notice">&#9888; CubeJ-* AP はデフォルトで 12345678 で公開されている。使わない間は OFF にしておいた方が安全。自宅 Wi-Fi 経由でいつでも戻せる.</p>
  <p>state: <strong id="ap_state">...</strong>
     <span id="ap_iface" style="font-family:monospace;font-size:.8rem;color:#888"></span></p>
  <button id="ap_toggle" type="button" disabled>--</button>
  <span id="ap_msg" style="font-size:.85rem;color:#888;margin-left:.6rem"></span>
</fieldset>
<fieldset><legend>Bridge Update</legend>
  <form id="update-form" enctype="multipart/form-data">
    <label><input type="file" name="update_file" accept=".py" required></label>
    <button type="submit">Upload &amp; Restart</button>
  </form>
</fieldset>
<fieldset><legend>Diagnostics</legend>
  <button id="btn-diag" type="button">Refresh Diag</button>
  <button id="btn-restart" type="button">Restart Bridge</button>
  <pre id="diag-output">(press refresh)</pre>
</fieldset>
<script>
var status = document.getElementById('status');
function showOk(msg) { status.innerHTML = '<p class="ok">' + msg + '</p>'; }
function showErr(msg) { status.innerHTML = '<p class="err">' + msg + '</p>'; }
function renderConfig(cfg) {
  var form = document.getElementById('config-form');
  form.innerHTML = '';
  Object.keys(cfg).sort().forEach(function(k) {
    var v = cfg[k];
    var row = document.createElement('label');
    row.textContent = k + ' ';
    var inp = document.createElement('input');
    inp.name = k;
    if (typeof v === 'boolean') {
      inp.type = 'checkbox'; inp.checked = v;
    } else {
      inp.value = v == null ? '' : String(v);
    }
    row.appendChild(inp);
    form.appendChild(row);
  });
}
function fetchConfig() {
  fetch('/api/config').then(function(r){ return r.json(); }).then(renderConfig);
}
document.getElementById('config-save').addEventListener('click', function() {
  var form = document.getElementById('config-form');
  var patch = {};
  Array.from(form.elements).forEach(function(el) {
    if (!el.name) return;
    if (el.type === 'checkbox') {
      patch[el.name] = el.checked;
    } else if (/^(mqtt_port|poll_interval|log_max_bytes|log_backup_count|admin_ui_port)$/.test(el.name)) {
      patch[el.name] = parseInt(el.value, 10);
    } else {
      patch[el.name] = el.value;
    }
  });
  fetch('/api/config', { method: 'PUT', headers: {'Content-Type':'application/json'},
                          body: JSON.stringify(patch) })
    .then(function(r){ return r.json().then(function(j){ return [r.status, j]; }); })
    .then(function(pair){ if(pair[0]===200) showOk('Config saved'); else showErr(pair[1].error || 'Save failed'); });
});
document.getElementById('wifi-form').addEventListener('submit', function(ev) {
  ev.preventDefault();
  var f = ev.target;
  var body = JSON.stringify({ ssid: f.ssid.value, psk: f.psk.value });
  fetch('/api/wifi', { method: 'PUT', headers:{'Content-Type':'application/json'}, body: body })
    .then(function(r){ return r.json().then(function(j){ return [r.status, j]; }); })
    .then(function(pair){ if(pair[0]===200) showOk('Wi-Fi updated'); else showErr(pair[1].error||'Failed'); });
});
document.getElementById('update-form').addEventListener('submit', function(ev) {
  ev.preventDefault();
  var fd = new FormData(ev.target);
  fetch('/api/update', { method:'POST', body: fd })
    .then(function(r){ return r.json().then(function(j){ return [r.status, j]; }); })
    .then(function(pair){ if(pair[0]===200) showOk('Bridge updated, restarting...'); else showErr(pair[1].error||'Upload failed'); });
});
document.getElementById('btn-diag').addEventListener('click', function() {
  fetch('/api/diag').then(function(r){return r.json();}).then(function(j){
    document.getElementById('diag-output').textContent = JSON.stringify(j, null, 2);
  });
});
document.getElementById('btn-restart').addEventListener('click', function() {
  if (!confirm('Restart bridge process?')) return;
  fetch('/api/restart', { method:'POST' })
    .then(function(r){ return r.json(); })
    .then(function(){ showOk('Restart requested'); });
});
fetchConfig();

// ---- AP toggle (spec 008) ----
function apRender(state) {
  var el = document.getElementById('ap_state');
  var btn = document.getElementById('ap_toggle');
  document.getElementById('ap_iface').textContent = state.interface || '';
  if (state.enabled === true) {
    el.textContent = 'ON'; el.style.color = '#186a3b';
    btn.textContent = 'Turn OFF'; btn.disabled = false; btn.dataset.next = 'false';
  } else if (state.enabled === false) {
    el.textContent = 'OFF'; el.style.color = '#555';
    btn.textContent = 'Turn ON'; btn.disabled = false; btn.dataset.next = 'true';
  } else {
    el.textContent = 'unknown'; el.style.color = '#b71c1c';
    btn.textContent = '--'; btn.disabled = true;
  }
}
function apRefresh() {
  fetch('/api/ap_state', {cache:'no-store'})
    .then(function(r){ return r.json(); })
    .then(apRender)
    .catch(function(){ document.getElementById('ap_msg').textContent = 'offline'; });
}
document.getElementById('ap_toggle').addEventListener('click', function() {
  var btn = this, next = btn.dataset.next === 'true';
  btn.disabled = true; btn.textContent = '...';
  document.getElementById('ap_msg').textContent = 'applying...';
  fetch('/api/ap_state', {
    method: 'PUT',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({enabled: next})
  })
    .then(function(r){ return r.json().then(function(j){ return [r.status, j]; }); })
    .then(function(pair){
      if (pair[0] !== 200) throw new Error(pair[1].error || ('http '+pair[0]));
      apRender(pair[1]);
      document.getElementById('ap_msg').textContent = 'ok';
    })
    .catch(function(e){
      document.getElementById('ap_msg').textContent = 'failed: ' + e.message;
      setTimeout(apRefresh, 500);
    });
});
apRefresh();
setInterval(apRefresh, 10000);
</script>
</body>
</html>"""


def _restart_bridge_async():
    """Schedule a stop/start of mqtt_ha_bridge 200 ms in the future.

    Run from a timer thread so the HTTP response can be written first.
    """
    def _do():
        try:
            subprocess.Popen(["stop", "mqtt_ha_bridge"]).wait()
            time.sleep(1)
            subprocess.Popen(["start", "mqtt_ha_bridge"]).wait()
        except Exception:
            pass
    threading.Timer(0.2, _do).start()


class AdminHandler(BaseHTTPServer.BaseHTTPRequestHandler):
    """Handler injected with config / paths / lock by start_admin_server."""

    # Class attributes set by start_admin_server before HTTPServer construction.
    admin_config = None        # AdminConfig
    diag_state_provider = None # callable() -> DiagState-like
    config_path = ADMIN_CONFIG_PATH
    bridge_path = ADMIN_BRIDGE_PATH
    wpa_supplicant_path = ADMIN_WPA_PATH
    log_path = ADMIN_LOG_PATH
    lock = None                # threading.Lock

    server_version = "CubeJ1Admin/1.0"

    # ------------------------------------------------------------------
    # framework hooks
    # ------------------------------------------------------------------

    def log_message(self, fmt, *args):
        if LOGGER is not None:
            try:
                LOGGER.debug(event="admin_http",
                             msg=fmt % args,
                             context={"client": self.address_string()})
                return
            except Exception:
                pass
        # Fallback: suppress noisy stderr (default would print every request).

    # ------------------------------------------------------------------
    # auth + helpers
    # ------------------------------------------------------------------

    def _authenticate(self):
        header = self.headers.get("Authorization")
        if self.admin_config is None:
            return False
        if not self.admin_config.match_basic_auth(header):
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="cubej"')
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error": "Authentication required"}')
            return False
        return True

    def _send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, status, text, content_type="text/plain; charset=utf-8"):
        # Python 2: str IS bytes — calling .encode("utf-8") on str holding
        # raw UTF-8 bytes triggers an implicit str→unicode decode through
        # the ASCII codec and blows up on any non-ASCII byte. Detect bytes
        # via `isinstance(text, bytes)` which holds on both interpreters
        # (Py2: bytes == str, Py3: bytes != str).
        if isinstance(text, bytes):
            body = text
        else:
            body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            return None
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None

    def _wrap(self, fn):
        try:
            return fn()
        except Exception as e:
            if LOGGER is not None:
                try:
                    LOGGER.error(event="admin_unhandled_error",
                                 context={"path": self.path,
                                          "error": str(e)})
                except Exception:
                    pass
            self._send_json(500, {"error": "internal server error"})

    # ------------------------------------------------------------------
    # GET dispatch
    # ------------------------------------------------------------------

    def do_GET(self):
        if not self._authenticate():
            return
        self._wrap(self._do_get_dispatch)

    def _do_get_dispatch(self):
        parsed = urlparse.urlparse(self.path)
        path = parsed.path
        if path == "/" or path == "":
            self._send_text(200, ADMIN_HTML,
                            content_type="text/html; charset=utf-8")
            return
        if path == "/api/config":
            cfg = read_config_masked(self.config_path)
            self._send_json(200, cfg)
            return
        if path == "/api/diag":
            try:
                ds = self.diag_state_provider()
                snap = ds.snapshot(time.time())
                # spec 010: merge EEDSCAN noise-floor metrics
                try:
                    eed = self.eedscan_state_provider()
                    snap.update(eed.snapshot(
                        pan_channel=getattr(ds, "pan_channel", None)))
                except Exception:
                    pass
            except Exception as e:
                self._send_json(500, {"error": str(e)})
                return
            self._send_json(200, snap)
            return
        if path == "/api/log":
            qs = urlparse.parse_qs(parsed.query)
            try:
                n = int(qs.get("lines", ["100"])[0])
            except ValueError:
                n = 100
            lines = tail_log(self.log_path, n)
            body = ("\n".join(lines) + "\n") if lines else ""
            self._send_text(200, body,
                            content_type="application/x-ndjson; charset=utf-8")
            return
        if path == "/api/erxudp_raw":
            try:
                ds = self.diag_state_provider()
                line = ds.last_erxudp_raw_line
            except Exception as e:
                self._send_json(500, {"error": str(e)})
                return
            tokens = line.split() if line else []
            self._send_json(200, {
                "raw": line,
                "token_count": len(tokens),
                "tokens": tokens,
            })
            return
        if path == "/wisun":
            self._send_text(200, WISUN_HTML,
                            content_type="text/html; charset=utf-8")
            return
        if path == "/api/ap_state":
            try:
                state = self.ap_controller.get()
            except Exception as e:
                self._send_json(500, {"error": str(e)})
                return
            self._send_json(200, state)
            return
        if path == "/api/probe":
            try:
                ps = self.probe_state_provider()
                self._send_json(200, ps.snapshot(time.time()))
            except Exception as e:
                self._send_json(500, {"error": str(e)})
            return
        if path == "/api/wisun_quality":
            try:
                ds = self.diag_state_provider()
                samples = [float(v) for v in ds.erxudp_latency_ms_recent]
                snap = ds.snapshot(time.time()) if hasattr(ds, "snapshot") else {}
                last_raw = getattr(ds, "last_erxudp_raw_line", None)
            except Exception as e:
                self._send_json(500, {"error": str(e)})
                return
            if samples:
                ordered = sorted(samples)
                p50 = round(_percentile(ordered, 50), 2)
                p95 = round(_percentile(ordered, 95), 2)
                mx = round(ordered[-1], 2)
            else:
                p50 = p95 = mx = None
            self._send_json(200, {
                "samples": samples,
                "sample_count": len(samples),
                "p50_ms": p50,
                "p95_ms": p95,
                "max_ms": mx,
                "uptime_seconds": snap.get("uptime_seconds") if isinstance(snap, dict) else None,
                "last_erxudp_raw": last_raw,
            })
            return
        self._send_json(404, {"error": "not found"})

    # ------------------------------------------------------------------
    # PUT dispatch
    # ------------------------------------------------------------------

    def do_PUT(self):
        if not self._authenticate():
            return
        self._wrap(self._do_put_dispatch)

    def _do_put_dispatch(self):
        path = urlparse.urlparse(self.path).path
        if path == "/api/config":
            patch = self._read_json_body()
            if patch is None:
                self._send_json(400, {"error": "invalid JSON body"})
                return
            with self.lock:
                with open(self.config_path) as f:
                    current = json.load(f)
                merged, err = validate_config_patch(patch, current)
                if err:
                    self._send_json(400, {"error": err})
                    return
                AtomicWriter.write_json(self.config_path, merged)
            self._send_json(200, {"status": "ok"})
            return
        if path == "/api/wifi":
            body = self._read_json_body()
            if body is None:
                self._send_json(400, {"error": "invalid JSON body"})
                return
            normalized, err = validate_wifi_patch(body)
            if err:
                self._send_json(400, {"error": err})
                return
            with self.lock:
                content = _rewrite_wpa_supplicant(
                    self.wpa_supplicant_path, normalized["ssid"],
                    normalized["psk"])
                AtomicWriter.write_bytes(
                    self.wpa_supplicant_path, content.encode("utf-8"))
            wpa_output = _run_wpa_reconfigure()
            self._send_json(200, {"status": "ok",
                                   "wpa_cli_output": wpa_output})
            return
        if path == "/api/ap_state":
            body = self._read_json_body()
            if body is None or not isinstance(body, dict) or "enabled" not in body:
                self._send_json(400, {"error": "expected { enabled: bool }"})
                return
            wants_enabled = bool(body["enabled"])
            try:
                state = (self.ap_controller.enable()
                         if wants_enabled else self.ap_controller.disable())
            except Exception as e:
                self._send_json(500, {"error": str(e)})
                return
            self._send_json(200, state)
            return
        if path == "/api/probe":
            body = self._read_json_body()
            if body is None or not isinstance(body, dict) or "enabled" not in body:
                self._send_json(400, {"error": "expected { enabled, interval_sec?, duration_sec? }"})
                return
            ps = self.probe_state_provider()
            now = time.time()
            if not body["enabled"]:
                ps.stop()
                # Keep the rolling latency window meaningful: on stop we
                # leave the deque alone (post-probe samples will refill it).
                self._send_json(200, ps.snapshot(now))
                return
            try:
                interval = int(body.get("interval_sec", 5))
                duration = int(body.get("duration_sec", 300))
                ps.start(interval_sec=interval, duration_sec=duration, now=now)
            except (ValueError, TypeError) as e:
                self._send_json(400, {"error": str(e)})
                return
            # Clear the deque so the sparkline shows only probe samples
            # from this run.
            try:
                ds = self.diag_state_provider()
                if hasattr(ds, "erxudp_latency_ms_recent"):
                    ds.erxudp_latency_ms_recent.clear()
            except Exception as e:
                log("probe deque clear failed: {}".format(e))
            self._send_json(200, ps.snapshot(now))
            return
        self._send_json(404, {"error": "not found"})

    # ------------------------------------------------------------------
    # POST dispatch (US2)
    # ------------------------------------------------------------------

    def do_POST(self):
        if not self._authenticate():
            return
        self._wrap(self._do_post_dispatch)

    def _do_post_dispatch(self):
        path = urlparse.urlparse(self.path).path
        if path == "/api/restart":
            self._send_json(200, {"status": "restarting"})
            try:
                self.wfile.flush()
            except Exception:
                pass
            _restart_bridge_async()
            return
        if path == "/api/update":
            self._handle_update()
            return
        self._send_json(404, {"error": "not found"})

    def _handle_update(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            self._send_json(400, {"error": "missing Content-Length"})
            return
        if length > 100 * 1024:
            self._send_json(413, {"error": "File too large (max 100KB)"})
            return
        ctype = self.headers.get("Content-Type", "")
        if not ctype.startswith("multipart/form-data"):
            self._send_json(400,
                {"error": "expected multipart/form-data"})
            return
        try:
            body = self.rfile.read(length)
        except Exception as e:
            self._send_json(400, {"error": "read failed: {}".format(e)})
            return
        filename, data, err = _parse_multipart_file(body, ctype, "update_file")
        if err:
            self._send_json(400, {"error": err})
            return
        if not filename.endswith(".py"):
            self._send_json(415, {"error": "Only .py files are accepted"})
            return
        # syntax check via py_compile (does NOT execute the code)
        import py_compile
        dir_ = os.path.dirname(self.bridge_path) or "."
        try:
            fd, tmp = tempfile.mkstemp(suffix=".py", dir=dir_, prefix=".upload.")
        except (IOError, OSError) as e:
            self._send_json(500, {"error": "cannot create temp file: {}".format(e)})
            return
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            try:
                py_compile.compile(tmp, doraise=True)
            except py_compile.PyCompileError as e:
                self._send_json(400, {"error": str(e).strip()})
                return
            with self.lock:
                os.rename(tmp, self.bridge_path)
                tmp = None
        finally:
            if tmp is not None and os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        self._send_json(200, {"status": "ok", "restarting": True})
        try:
            self.wfile.flush()
        except Exception:
            pass
        _restart_bridge_async()


_MULTIPART_BOUNDARY_RE = re.compile(r'boundary=(?:"([^"]+)"|([^;\s]+))')
_MULTIPART_FILENAME_RE = re.compile(r'filename="([^"]*)"')
_MULTIPART_NAME_RE = re.compile(r'name="([^"]+)"')


def _parse_multipart_file(body, content_type, field_name):
    """Extract a single file upload from a multipart/form-data body.

    Returns (filename, data, None) on success or (None, None, error_message).
    Pure stdlib (re only) — works on both Python 2.7 (Cube J1 runtime) and
    Python 3 (host test runner) where `cgi.FieldStorage` is unavailable.
    """
    m = _MULTIPART_BOUNDARY_RE.search(content_type)
    if not m:
        return None, None, "missing multipart boundary"
    boundary = (m.group(1) or m.group(2)).encode("ascii")
    sep = b"--" + boundary
    parts = body.split(sep)
    for part in parts:
        if not part or part in (b"", b"--", b"--\r\n"):
            continue
        if part.startswith(b"\r\n"):
            part = part[2:]
        idx = part.find(b"\r\n\r\n")
        if idx == -1:
            continue
        headers_blob = part[:idx].decode("utf-8", errors="replace")
        payload = part[idx + 4:]
        # strip trailing CRLF that precedes the next boundary
        if payload.endswith(b"\r\n"):
            payload = payload[:-2]
        name_match = _MULTIPART_NAME_RE.search(headers_blob)
        if not name_match or name_match.group(1) != field_name:
            continue
        fn_match = _MULTIPART_FILENAME_RE.search(headers_blob)
        filename = fn_match.group(1) if fn_match else ""
        return filename, payload, None
    return None, None, "field '{}' not found".format(field_name)


def _rewrite_wpa_supplicant(path, ssid, psk):
    """Replace the ssid= and psk= lines in the existing template, preserving
    other directives (`freq_list`, `scan_ssid`, etc).

    Falls back to a minimal template if the file is missing.
    """
    try:
        with open(path) as f:
            existing = f.read()
    except (IOError, OSError):
        return ('ctrl_interface=/data/misc/wifi/sockets\n'
                'update_config=1\n\n'
                'network={{\n'
                '        ssid="{}"\n'
                '        psk="{}"\n'
                '        key_mgmt=WPA-PSK\n'
                '}}\n').format(ssid, psk)
    lines = existing.splitlines()
    out_lines = []
    in_network = False
    saw_ssid = False
    saw_psk = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("network={"):
            in_network = True
            out_lines.append(line)
            continue
        if in_network and stripped == "}":
            if not saw_ssid:
                out_lines.append('        ssid="{}"'.format(ssid))
            if not saw_psk:
                out_lines.append('        psk="{}"'.format(psk))
            out_lines.append(line)
            in_network = False
            continue
        if in_network and stripped.startswith("ssid="):
            out_lines.append('        ssid="{}"'.format(ssid))
            saw_ssid = True
            continue
        if in_network and stripped.startswith("psk="):
            out_lines.append('        psk="{}"'.format(psk))
            saw_psk = True
            continue
        out_lines.append(line)
    return "\n".join(out_lines) + "\n"


def _run_wpa_reconfigure():
    """Invoke wpa_cli reconfigure on wlan0. Returns the captured stdout."""
    try:
        proc = subprocess.Popen(
            ["wpa_cli", "-p", "/data/misc/wifi/sockets",
             "-i", "wlan0", "reconfigure"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, _ = proc.communicate()
        return (out or b"").decode("utf-8", errors="replace").strip()
    except Exception as e:
        return "wpa_cli failed: {}".format(e)


class _ReusingHTTPServer(BaseHTTPServer.HTTPServer):
    allow_reuse_address = True


class AdminServer(object):
    """HTTPServer + dedicated thread wrapper."""

    def __init__(self, httpd, port):
        self.httpd = httpd
        self.port = port
        self.thread = threading.Thread(
            target=httpd.serve_forever,
            name="cubej-admin-http",
        )
        self.thread.daemon = True

    def start(self):
        self.thread.start()

    def stop(self):
        try:
            self.httpd.shutdown()
            self.httpd.server_close()
        except Exception:
            pass
        if self.thread.is_alive():
            self.thread.join(timeout=2)


def start_admin_server(port, user, password, diag_state_provider,
                       config_path=ADMIN_CONFIG_PATH,
                       bridge_path=ADMIN_BRIDGE_PATH,
                       wpa_supplicant_path=ADMIN_WPA_PATH,
                       log_path=ADMIN_LOG_PATH,
                       ap_controller=None,
                       probe_state_provider=None,
                       eedscan_state_provider=None):
    """Construct and start an AdminServer with the given settings.

    Returns the running AdminServer.
    """
    admin_config = AdminConfig(enabled=True, port=port,
                                user=user, password=password)
    AdminHandler.admin_config = admin_config
    # staticmethod prevents the function from being bound to instances
    # (so `self.diag_state_provider()` doesn't pass a phantom self).
    AdminHandler.diag_state_provider = staticmethod(diag_state_provider)
    AdminHandler.config_path = config_path
    AdminHandler.bridge_path = bridge_path
    AdminHandler.wpa_supplicant_path = wpa_supplicant_path
    AdminHandler.log_path = log_path
    AdminHandler.ap_controller = ap_controller or ApController()
    AdminHandler.probe_state_provider = staticmethod(
        probe_state_provider or (lambda: ProbeState()))
    AdminHandler.eedscan_state_provider = staticmethod(
        eedscan_state_provider or (lambda: EedScanState()))
    AdminHandler.lock = threading.Lock()

    httpd = _ReusingHTTPServer(("", port), AdminHandler)
    server = AdminServer(httpd, port)
    server.start()
    return server

# ---------------------------------------------------------------------------
# Pure helpers (no I/O) shared by observability features
# ---------------------------------------------------------------------------

def format_iso8601_utc(epoch):
    """Format epoch seconds as `YYYY-MM-DDTHH:MM:SSZ` in UTC.

    Drops fractional seconds (truncates via int()). Pure function so it can
    be unit-tested without a clock.
    """
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(epoch)))


def bridge_version():
    """Return the bridge self-version string `<semver>+<git_hash>`."""
    return "{}+{}".format(BRIDGE_SEMVER, BRIDGE_GIT_HASH)


def should_force_wisun_reconnect(consecutive, threshold):
    """Decide whether to force a Wi-SUN re-join after consecutive ERXUDP timeouts.

    `threshold <= 0` opts out of the safety net (legacy behaviour where the
    bridge only reconnects on uncaught exceptions in the main loop).
    """
    if not isinstance(threshold, int) or threshold <= 0:
        return False
    return consecutive >= threshold

# ---------------------------------------------------------------------------
# Structured JSON Lines logger
# ---------------------------------------------------------------------------

_LEVEL_NAME_TO_LOGGING = {
    "debug": logging.DEBUG,
    "info":  logging.INFO,
    "warn":  logging.WARNING,
    "error": logging.ERROR,
}

_LOGGING_TO_LEVEL_NAME = {
    logging.DEBUG:   "debug",
    logging.INFO:    "info",
    logging.WARNING: "warn",
    logging.ERROR:   "error",
}


class _JsonFormatter(logging.Formatter):
    """logging.Formatter that emits one JSON object per line.

    The required record attributes (ts/level/event) and the optional
    `msg`/`context` payload are pulled from the LogRecord. Producers attach
    them via `extra={...}` when calling Logger.<level>(...).
    """

    def format(self, record):
        out = collections.OrderedDict()
        out["ts"] = format_iso8601_utc(record.created)
        out["level"] = _LOGGING_TO_LEVEL_NAME.get(record.levelno, "info")
        out["event"] = getattr(record, "event", "log")
        msg = getattr(record, "msg_text", None)
        if msg:
            out["msg"] = msg
        context = getattr(record, "context", None)
        if context:
            out["context"] = context
        return json.dumps(out, separators=(",", ":"))


class JsonLogger(object):
    """Structured JSON Lines logger backed by logging.handlers.RotatingFileHandler.

    Falls back to stderr if the file handler cannot be constructed (FR-009).
    Single-process / single-thread main loop, so no extra locking needed.
    """

    DEFAULT_MAX_BYTES = 1024 * 1024
    DEFAULT_BACKUP_COUNT = 3

    def __init__(self, path, level="info",
                 max_bytes=None, backup_count=None):
        if max_bytes is None:
            max_bytes = self.DEFAULT_MAX_BYTES
        if backup_count is None:
            backup_count = self.DEFAULT_BACKUP_COUNT

        level_num = _LEVEL_NAME_TO_LOGGING.get(level, logging.INFO)
        self._logger = logging.Logger(
            "cubej_bridge_{}".format(id(self)),
            level=level_num,
        )
        # Avoid bubbling up to root logger (and double-printing).
        self._logger.propagate = False

        try:
            handler = logging.handlers.RotatingFileHandler(
                path,
                maxBytes=max_bytes,
                backupCount=backup_count,
                delay=False,
            )
        except (IOError, OSError) as e:
            sys.stderr.write(
                "JsonLogger: file handler unavailable ({}), falling back to "
                "stderr\n".format(e))
            handler = logging.StreamHandler(sys.stderr)

        handler.setFormatter(_JsonFormatter())
        self._logger.addHandler(handler)
        self._handler = handler

    def _emit(self, level_num, event, msg=None, context=None):
        # logging.Logger expects an actual message string; pass an empty
        # placeholder and attach our payload via `extra` so the formatter
        # can render the JSON.
        extra = {"event": event}
        if msg is not None:
            extra["msg_text"] = msg
        if context is not None:
            extra["context"] = context
        self._logger.log(level_num, "", extra=extra)

    def debug(self, event="log", msg=None, context=None):
        self._emit(logging.DEBUG, event, msg=msg, context=context)

    def info(self, event="log", msg=None, context=None):
        self._emit(logging.INFO, event, msg=msg, context=context)

    def warn(self, event="log", msg=None, context=None):
        self._emit(logging.WARNING, event, msg=msg, context=context)

    def error(self, event="log", msg=None, context=None):
        self._emit(logging.ERROR, event, msg=msg, context=context)

    def close(self):
        try:
            self._logger.removeHandler(self._handler)
            self._handler.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Named event emitters (FR-010)
# ---------------------------------------------------------------------------

def emit_bridge_start(logger, device_id, version):
    logger.info(event="bridge_start",
                context={"device_id": device_id, "version": version})


def emit_mqtt_connected(logger, host, port):
    logger.info(event="mqtt_connected",
                context={"host": host, "port": port})


def emit_mqtt_reconnect(logger):
    logger.warn(event="mqtt_reconnect")


def emit_wisun_joined(logger, pan, ipv6):
    logger.info(event="wisun_joined", context={
        "channel": pan.get("Channel"),
        "pan_id":  pan.get("Pan ID"),
        "mac":     pan.get("Addr"),
        "lqi":     pan.get("LQI"),
        "ipv6":    ipv6,
    })


def emit_wisun_join_failed(logger, reason):
    logger.error(event="wisun_join_failed", context={"reason": str(reason)})


def emit_scan_retry(logger, duration):
    logger.warn(event="scan_retry", context={"duration": duration})


def emit_poll_success(logger, measurements):
    # Keep only the publish-relevant keys to avoid bloating the log line.
    summary = {}
    for key in ("power_w", "energy_forward_kwh", "energy_reverse_kwh",
                "current_r_a", "current_t_a"):
        if key in measurements:
            summary[key] = measurements[key]
    logger.info(event="poll_success", context=summary)


def emit_poll_failure(logger, reason):
    logger.warn(event="poll_failure", context={"reason": str(reason)})

# ---------------------------------------------------------------------------
# DiagState: bridge self-diagnostics aggregated into MQTT publish payloads
# ---------------------------------------------------------------------------

# Order is fixed for deterministic publish/snapshot iteration.
_DIAG_SNAPSHOT_KEYS = (
    "last_poll_success_ts",
    "last_poll_failure_ts",
    "lqi",
    "pan_channel",
    "scan_retries_total",
    "wisun_reconnects_total",
    "mqtt_reconnects_total",
    "erxudp_timeouts_total",
    "erxudp_intra_cycle_retries_total",
    "erxudp_recovered_by_retry_total",
    "uptime_seconds",
    "version",
)

# Spec 006: Wi-SUN health observability. EVENT and FAIL ids that we expose
# as named counters. Anything outside this list is still counted in memory
# (sk_event_counts) but not published — keeps HA discovery noise-free.
_PUBLISHED_SK_EVENT_IDS = ("22", "24", "25", "26", "28", "29", "32", "33")
_PUBLISHED_SK_ERROR_CODES = ("05", "09", "10")


def _percentile(sorted_samples, pct):
    """Linear-interpolation percentile on a pre-sorted list. Spec 006."""
    if not sorted_samples:
        return 0.0
    if len(sorted_samples) == 1:
        return float(sorted_samples[0])
    k = (len(sorted_samples) - 1) * (pct / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(sorted_samples) - 1)
    frac = k - lo
    return sorted_samples[lo] + (sorted_samples[hi] - sorted_samples[lo]) * frac


# ---------------------------------------------------------------------------
# AP (Wi-Fi Direct GO mode) toggle — spec 008
#
# Cube J1 の `CubeJ-*` AP は wpa_supplicant 配下の P2P GO group。
# wpa_cli の p2p_group_remove / p2p_group_add で manual 制御できる。
# 状態は getprop net.wifi.ap.state で観測可能。
# ---------------------------------------------------------------------------

_WPA_CLI_SOCKET = "/data/misc/wifi/sockets"


def build_wpa_cli_cmd(interface, action):
    if action == "disable":
        return ["wpa_cli", "-p", _WPA_CLI_SOCKET, "-i", interface,
                "p2p_group_remove", interface]
    if action == "enable":
        return ["wpa_cli", "-p", _WPA_CLI_SOCKET, "-i", interface,
                "p2p_group_add", "persistent=0", "freq=2412"]
    raise ValueError("unknown ap action: {}".format(action))


def parse_ap_state(text):
    """Map `getprop net.wifi.ap.state` output to True / False / None."""
    if not text:
        return None
    s = text.strip().lower()
    if not s:
        return None
    if s in ("created", "enabled", "started", "running"):
        return True
    if s in ("disabled", "removed", "stopped", "uninitialized"):
        return False
    return None


def _default_subprocess_runner(cmd, timeout=5):
    import subprocess
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        out, _err = proc.communicate(timeout=timeout)
    except TypeError:
        # Py2 communicate has no timeout
        out, _err = proc.communicate()
    if isinstance(out, bytes):
        out = out.decode("utf-8", "replace")
    return out


class ApController(object):
    """Toggle the Wi-Fi Direct GO mode AP via wpa_cli + getprop.

    `runner(cmd, timeout=5) -> str` is injectable so unit tests don't spawn
    real subprocesses. Production callers pass nothing and pick up the
    default subprocess runner.
    """

    def __init__(self, interface=None, runner=None):
        self._interface = interface
        self._runner = runner or _default_subprocess_runner

    def _resolve_interface(self):
        if self._interface:
            return self._interface
        out = self._runner(["getprop", "net.wifi.ap.interface"], timeout=2)
        iface = (out or "").strip()
        return iface or "p2p-wlan0-0"

    def get(self):
        iface = self._resolve_interface()
        out = self._runner(["getprop", "net.wifi.ap.state"], timeout=2)
        return {"enabled": parse_ap_state(out), "interface": iface}

    def disable(self):
        iface = self._resolve_interface()
        self._runner(build_wpa_cli_cmd(iface, "disable"), timeout=5)
        return self.get()

    def enable(self):
        iface = self._resolve_interface()
        self._runner(build_wpa_cli_cmd(iface, "enable"), timeout=5)
        return self.get()


def render_sparkline(samples, width, height):
    """Return an SVG path string sketching *samples* over a (width, height)
    box. Min sample sits near y=height (bottom), max near y=0 (top). Returns
    "" for empty input. For 1 sample returns 'M w/2 h/2' (a centered dot).

    Used by the /wisun real-time quality page (spec 007)."""
    if not samples:
        return ""
    if len(samples) == 1:
        return "M {:.1f} {:.1f}".format(width / 2.0, height / 2.0)
    lo = min(samples)
    hi = max(samples)
    rng = hi - lo if hi > lo else 1.0
    n = len(samples)
    step = width / float(n - 1)
    parts = []
    for i, v in enumerate(samples):
        x = i * step
        y = height - (v - lo) / rng * height
        cmd = "M" if i == 0 else "L"
        parts.append("{} {:.1f} {:.1f}".format(cmd, x, y))
    return " ".join(parts)


# spec 007: real-time Wi-SUN quality page. Lives on the admin UI so the
# existing Basic Auth covers it. Vanilla JS only — Constitution II forbids
# external assets.
WISUN_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Wi-SUN Quality</title>
<style>
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,sans-serif;
background:#111;color:#eee;padding:16px}
h1{font-size:18px;margin:0 0 12px;color:#aaa}
.row{display:flex;gap:12px;margin-bottom:16px;flex-wrap:wrap}
.stat{flex:1;min-width:140px;padding:16px;border-radius:10px;
text-align:center;background:#222}
.stat .label{font-size:11px;color:#888;text-transform:uppercase;letter-spacing:1px}
.stat .v{font-size:48px;font-weight:700;line-height:1.1;margin-top:4px}
.stat .unit{font-size:14px;color:#888;margin-left:4px}
.bg-ok{background:#0a5}.bg-warn{background:#a80}
.bg-bad{background:#a40}.bg-crit{background:#902}
.bg-na{background:#333}
.sparkline{background:#181818;border-radius:10px;padding:10px;margin-bottom:16px}
.meta{font-size:12px;color:#888}
.meta code{background:#222;padding:2px 6px;border-radius:4px;color:#bbb;
word-break:break-all;display:inline-block;max-width:100%}
.offline{color:#f55}
</style>
</head>
<body>
<h1>Wi-SUN Quality (real-time)</h1>
<div class="sparkline" style="padding:14px 16px;display:flex;align-items:center;
     gap:12px;flex-wrap:wrap;margin-bottom:16px">
  <strong style="color:#aaa">Probe Mode:</strong>
  <span id="probe_state" style="color:#888">--</span>
  <select id="probe_interval" style="background:#222;color:#eee;border:1px solid #333;
          border-radius:6px;padding:4px 8px">
    <option value="2">2s</option><option value="3">3s</option>
    <option value="5" selected>5s</option><option value="10">10s</option>
  </select>
  <select id="probe_duration" style="background:#222;color:#eee;border:1px solid #333;
          border-radius:6px;padding:4px 8px">
    <option value="60">1 min</option><option value="180">3 min</option>
    <option value="300" selected>5 min</option><option value="600">10 min</option>
  </select>
  <button id="probe_start" style="background:#5af;border:0;border-radius:6px;
          padding:6px 14px;cursor:pointer;font-weight:600">Start Probe</button>
  <button id="probe_stop" style="background:#a40;color:#fff;border:0;border-radius:6px;
          padding:6px 14px;cursor:pointer;font-weight:600" disabled>Stop</button>
  <span id="probe_msg" style="color:#888;font-size:11px"></span>
</div>
<div class="row">
  <div class="stat" id="card_p50"><div class="label">p50 RTT</div>
    <div class="v"><span id="p50">--</span><span class="unit">ms</span></div></div>
  <div class="stat" id="card_p95"><div class="label">p95 RTT</div>
    <div class="v"><span id="p95">--</span><span class="unit">ms</span></div></div>
  <div class="stat" id="card_max"><div class="label">max RTT</div>
    <div class="v"><span id="vmax">--</span><span class="unit">ms</span></div></div>
</div>
<div class="sparkline">
  <svg id="spark" width="100%" height="80" viewBox="0 0 600 80"
       preserveAspectRatio="none">
    <path d="" stroke="#5af" stroke-width="2" fill="none" id="path"/>
  </svg>
</div>
<div class="meta">
  samples: <span id="n">0</span> /
  uptime: <span id="up">--</span>s /
  status: <span id="status">loading...</span><br><br>
  last raw ERXUDP: <code id="raw">--</code>
</div>
<script>
function cls(v){
  if(v===null||v===undefined)return 'bg-na';
  if(v<200)return 'bg-ok';
  if(v<500)return 'bg-warn';
  if(v<1000)return 'bg-bad';
  return 'bg-crit';
}
function fmt(v){return v===null||v===undefined?'--':Math.round(v);}
function spark(samples){
  if(!samples.length)return '';
  if(samples.length===1)return 'M 300 40';
  var lo=Math.min.apply(null,samples), hi=Math.max.apply(null,samples);
  var rng=hi>lo?hi-lo:1;
  var step=600/(samples.length-1);
  var d='';
  for(var i=0;i<samples.length;i++){
    var x=i*step;
    var y=80-(samples[i]-lo)/rng*80;
    d+=(i?' L ':'M ')+x.toFixed(1)+' '+y.toFixed(1);
  }
  return d;
}
function setCard(id,v){
  var card=document.getElementById('card_'+id);
  card.className='stat '+cls(v);
}
async function tick(){
  try{
    var r=await fetch('/api/wisun_quality',{cache:'no-store'});
    if(!r.ok)throw new Error('http '+r.status);
    var j=await r.json();
    document.getElementById('p50').textContent=fmt(j.p50_ms);
    document.getElementById('p95').textContent=fmt(j.p95_ms);
    document.getElementById('vmax').textContent=fmt(j.max_ms);
    setCard('p50',j.p50_ms);
    setCard('p95',j.p95_ms);
    setCard('max',j.max_ms);
    document.getElementById('n').textContent=j.sample_count;
    document.getElementById('up').textContent=j.uptime_seconds;
    document.getElementById('raw').textContent=j.last_erxudp_raw||'--';
    document.getElementById('status').textContent='ok';
    document.getElementById('status').className='';
    document.getElementById('path').setAttribute('d',spark(j.samples));
  }catch(e){
    document.getElementById('status').textContent='offline ('+e.message+')';
    document.getElementById('status').className='offline';
  }
}
tick();
setInterval(tick,1500);

// ---- Probe mode (spec 009) ----
async function probeRefresh(){
  try{
    var r=await fetch('/api/probe',{cache:'no-store'});
    var j=await r.json();
    var el=document.getElementById('probe_state');
    var btnStart=document.getElementById('probe_start');
    var btnStop=document.getElementById('probe_stop');
    if(j.active){
      el.textContent='ON ('+j.interval_sec+'s, '+j.remaining_sec+'s left)';
      el.style.color='#0fc';
      btnStart.disabled=true;btnStop.disabled=false;
    }else{
      el.textContent='OFF';el.style.color='#888';
      btnStart.disabled=false;btnStop.disabled=true;
    }
  }catch(e){
    document.getElementById('probe_msg').textContent='offline';
  }
}
document.getElementById('probe_start').addEventListener('click',async function(){
  var iv=parseInt(document.getElementById('probe_interval').value,10);
  var du=parseInt(document.getElementById('probe_duration').value,10);
  document.getElementById('probe_msg').textContent='starting...';
  try{
    var r=await fetch('/api/probe',{method:'PUT',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({enabled:true,interval_sec:iv,duration_sec:du})});
    if(!r.ok){
      var j=await r.json().catch(function(){return{};});
      throw new Error(j.error||('http '+r.status));
    }
    document.getElementById('probe_msg').textContent='probe started';
    probeRefresh();
  }catch(e){
    document.getElementById('probe_msg').textContent='failed: '+e.message;
  }
});
document.getElementById('probe_stop').addEventListener('click',async function(){
  document.getElementById('probe_msg').textContent='stopping...';
  try{
    var r=await fetch('/api/probe',{method:'PUT',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({enabled:false})});
    if(!r.ok)throw new Error('http '+r.status);
    document.getElementById('probe_msg').textContent='stopped';
    probeRefresh();
  }catch(e){
    document.getElementById('probe_msg').textContent='failed: '+e.message;
  }
});
probeRefresh();
setInterval(probeRefresh,2000);
</script>
</body>
</html>
"""


def classify_sk_line(line):
    """Return ('erxudp', payload_hex) / ('event', id_hex_upper) /
    ('error', code_hex_upper) / None for a single SK output line."""
    if not line:
        return None
    if line.startswith("ERXUDP"):
        parts = line.split()
        if len(parts) >= 10:
            return ("erxudp", parts[-1].strip())
        return None
    if line.startswith("EVENT "):
        rest = line[6:].strip().split()
        if not rest:
            return None
        return ("event", rest[0].upper())
    if line.startswith("FAIL ER"):
        rest = line[7:].strip().split()
        if not rest:
            return None
        return ("error", rest[0].upper())
    return None


class DiagState(object):
    """Single-thread mutable diagnostics aggregator (main loop only)."""

    def __init__(self, start_time, version):
        self.start_time = start_time
        self.version = version
        self.last_poll_success_ts = None  # epoch seconds, formatted on snapshot
        self.last_poll_failure_ts = None
        self.lqi = None                   # int (decimal)
        self.pan_channel = None           # int (decimal)
        self.scan_retries_total = 0
        self.wisun_reconnects_total = 0
        self.mqtt_reconnects_total = 0
        self.erxudp_timeouts_total = 0
        # Consecutive (not total) ERXUDP timeouts since the last successful
        # poll. Drives auto-reconnect when the smart meter stops answering.
        self.consecutive_erxudp_timeouts = 0
        # Spec 011: intra-cycle retry counters.
        self.erxudp_intra_cycle_retries_total = 0
        self.erxudp_recovered_by_retry_total = 0
        # Spec 006: Wi-SUN health observability. Rolling window of recent
        # SKSENDTO→ERXUDP latencies (ms) for percentile reporting.
        self.erxudp_latency_ms_recent = collections.deque(maxlen=200)
        # SK EVENT and FAIL counters keyed by 2-digit hex (upper). All
        # observed ids are counted; only the ids in _PUBLISHED_SK_EVENT_IDS
        # / _PUBLISHED_SK_ERROR_CODES are emitted by snapshot().
        self.sk_event_counts = {}
        self.sk_error_counts = {}
        # Most recent raw ERXUDP line as the SKSTACK printed it. Used by the
        # admin UI to inspect firmware-specific token layout (RSSI / LQI
        # placement varies across SKSTACK builds).
        self.last_erxudp_raw_line = None

    # --- counters (monotonically non-decreasing) ---

    def on_scan_retry(self):
        self.scan_retries_total += 1

    def on_wisun_reconnect(self):
        self.wisun_reconnects_total += 1

    def on_mqtt_reconnect(self):
        self.mqtt_reconnects_total += 1

    def on_erxudp_timeout(self):
        self.erxudp_timeouts_total += 1
        self.consecutive_erxudp_timeouts += 1

    # Spec 011: intra-cycle retry counters.

    def on_erxudp_intra_cycle_retry(self):
        self.erxudp_intra_cycle_retries_total += 1

    def on_erxudp_recovered_by_retry(self):
        self.erxudp_recovered_by_retry_total += 1

    # Spec 006: Wi-SUN health observability — rolling RTT + event/error tallies.

    def on_erxudp_latency(self, ms):
        self.erxudp_latency_ms_recent.append(float(ms))

    def on_sk_event(self, event_id):
        key = str(event_id).upper()
        self.sk_event_counts[key] = self.sk_event_counts.get(key, 0) + 1

    def on_sk_error(self, error_code):
        key = str(error_code).upper()
        self.sk_error_counts[key] = self.sk_error_counts.get(key, 0) + 1

    def on_erxudp_raw(self, line):
        self.last_erxudp_raw_line = line

    # --- timestamps ---

    def on_poll_success(self, now):
        self.last_poll_success_ts = now
        self.consecutive_erxudp_timeouts = 0

    def on_poll_failure(self, now):
        self.last_poll_failure_ts = now

    # --- PAN info from SKSCAN ---

    def on_wisun_joined(self, pan_info):
        lqi_hex = pan_info.get("LQI")
        if lqi_hex is not None:
            self.lqi = int(lqi_hex, 16)
        chan_hex = pan_info.get("Channel")
        if chan_hex is not None:
            self.pan_channel = int(chan_hex, 16)

    # --- snapshot for MQTT publish ---

    def snapshot(self, now):
        uptime = int(now - self.start_time)
        if uptime < 0:
            uptime = 0
        raw = {
            "last_poll_success_ts":
                format_iso8601_utc(self.last_poll_success_ts)
                if self.last_poll_success_ts is not None else None,
            "last_poll_failure_ts":
                format_iso8601_utc(self.last_poll_failure_ts)
                if self.last_poll_failure_ts is not None else None,
            "lqi": self.lqi,
            "pan_channel": self.pan_channel,
            "scan_retries_total": self.scan_retries_total,
            "wisun_reconnects_total": self.wisun_reconnects_total,
            "mqtt_reconnects_total": self.mqtt_reconnects_total,
            "erxudp_timeouts_total": self.erxudp_timeouts_total,
            "erxudp_intra_cycle_retries_total":
                self.erxudp_intra_cycle_retries_total,
            "erxudp_recovered_by_retry_total":
                self.erxudp_recovered_by_retry_total,
            "uptime_seconds": uptime,
            "version": self.version,
        }
        # Preserve declared order; drop None entries so HA keeps "unknown".
        out = collections.OrderedDict()
        for key in _DIAG_SNAPSHOT_KEYS:
            value = raw[key]
            if value is None:
                continue
            out[key] = value

        # Spec 006: latency percentiles. Omit when no samples so HA does not
        # see a misleading "0 ms" entity at boot.
        samples = list(self.erxudp_latency_ms_recent)
        if samples:
            ordered = sorted(samples)
            out["erxudp_latency_p50_ms"] = round(_percentile(ordered, 50), 2)
            out["erxudp_latency_p95_ms"] = round(_percentile(ordered, 95), 2)
            out["erxudp_latency_max_ms"] = round(ordered[-1], 2)

        # Spec 006: named EVENT / ER counters. Omit zero-count entries
        # so HA discovery does not advertise sensors that have never fired.
        for eid in _PUBLISHED_SK_EVENT_IDS:
            n = self.sk_event_counts.get(eid, 0)
            if n > 0:
                out["sk_event_{}_total".format(eid)] = n
        for code in _PUBLISHED_SK_ERROR_CODES:
            n = self.sk_error_counts.get(code, 0)
            if n > 0:
                out["sk_error_ER{}_total".format(code)] = n
        return out

# ---------------------------------------------------------------------------
# Serial port (termios, no pyserial)
# ---------------------------------------------------------------------------

def open_serial(port, baud=115200):
    fd = os.open(port, os.O_RDWR | os.O_NOCTTY)

    attrs = list(termios.tcgetattr(fd))
    iflag, oflag, cflag, lflag = attrs[0], attrs[1], attrs[2], attrs[3]

    # raw input
    iflag &= ~(termios.IGNBRK | termios.BRKINT | termios.PARMRK |
               termios.ISTRIP | termios.INLCR  | termios.IGNCR  |
               termios.ICRNL  | termios.IXON)
    oflag &= ~termios.OPOST
    cflag &= ~(termios.CSIZE | termios.PARENB)
    cflag |=  termios.CS8 | termios.CREAD | termios.CLOCAL
    lflag &= ~(termios.ECHO | termios.ECHONL | termios.ICANON |
               termios.ISIG | termios.IEXTEN)

    baud_map = {
        9600:   termios.B9600,
        19200:  termios.B19200,
        38400:  termios.B38400,
        57600:  termios.B57600,
        115200: termios.B115200,
    }
    baud_const = baud_map.get(baud, termios.B115200)

    cc = attrs[6]
    # attrs[6] must be returned in the same type tcgetattr gave us.
    # On this device Python 2.7 it is a list of 32 ints; tcsetattr rejects bytes.
    if isinstance(cc, list):
        cc_list = list(cc)
        cc_list[termios.VMIN]  = 1
        cc_list[termios.VTIME] = 0
        attrs[6] = cc_list
    else:
        # bytes/bytearray path
        cc_arr = bytearray(cc)
        cc_arr[termios.VMIN]  = 1
        cc_arr[termios.VTIME] = 0
        attrs[6] = bytes(cc_arr)

    attrs[0], attrs[1], attrs[2], attrs[3] = iflag, oflag, cflag, lflag
    attrs[4] = baud_const
    attrs[5] = baud_const

    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    termios.tcflush(fd, termios.TCIOFLUSH)
    return fd

def serial_write(fd, data):
    if isinstance(data, bytes):
        os.write(fd, data)
    else:
        os.write(fd, data.encode("ascii"))

def serial_readline(fd, timeout=10):
    """Read one CRLF-terminated line; return decoded str or None on timeout."""
    buf = b""
    deadline = time.time() + timeout
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        r, _, _ = select.select([fd], [], [], min(remaining, 0.5))
        if not r:
            continue
        ch = os.read(fd, 1)
        if not ch:
            continue
        buf += ch
        if buf.endswith(b"\r\n"):
            return buf[:-2].decode("ascii", errors="replace")
    return buf.decode("ascii", errors="replace") if buf else None

def _led_blink(stop_event, colors, interval=0.2):
    i = 0
    while not stop_event.is_set():
        led_rgb(*colors[i % len(colors)])
        i += 1
        stop_event.wait(interval)

def skcommand(fd, cmd, timeout=10):
    """Send one SKSTACK command; return list of response lines (up to OK/FAIL)."""
    orig_led = led_read()
    stop_event = threading.Event()
    t = threading.Thread(target=_led_blink,
                         args=(stop_event, [(0, 255, 0), (0, 0, 255)]))
    t.daemon = True
    t.start()

    serial_write(fd, cmd + "\r\n")
    lines = []
    deadline = time.time() + timeout
    try:
        while time.time() < deadline:
            line = serial_readline(fd, timeout=max(0.5, deadline - time.time()))
            if line is None:
                break
            lines.append(line)
            if line in ("OK", ) or line.startswith("FAIL"):
                break
    finally:
        stop_event.set()
        t.join(timeout=1)
        led_rgb(*orig_led)
    return lines

# ---------------------------------------------------------------------------
# Scan settings
# ---------------------------------------------------------------------------

# ROHM SKSCAN duration: scan dwell time = (192 * 2^duration + 1) symbol times.
# duration=4 → 約 8 秒、 5 → 16 秒、 6 → 32 秒、 7 → 64 秒。
# 4 は最小推奨だが弱信号環境では PAN を取りこぼし scan_retries が嵩む。
# 6 にすると初回 scan が 32 秒、 LQI 推定精度も上がる。
SCAN_DURATION_BASE = 6
SCAN_RETRY_LIMIT = 10

# ---------------------------------------------------------------------------
# SKSTACK-IP / Wi-SUN B-route connection
# ---------------------------------------------------------------------------

def skscan(fd, diag_state=None):
    """Active scan with retries; returns best PAN info dict or empty dict.

    `diag_state` is optional. When given, its `on_scan_retry()` is invoked
    each time the scan widens its duration. Wrapped in try/except so a diag
    bug never blocks the measurement path (Constitution IV).
    """
    duration = SCAN_DURATION_BASE
    
    while duration <= SCAN_RETRY_LIMIT:
        # Clear stale lines from previous command/scan cycle.
        termios.tcflush(fd, termios.TCIFLUSH)

        log("SKSCAN try duration={}".format(duration))
        # BP35C0 style scan command: <mode> <channel_mask> <duration> <side>
        serial_write(fd, "SKSCAN 2 FFFFFFFF {} 0\r\n".format(duration))

        pan_list  = []
        current   = {}
        scan_done = False
        deadline  = time.time() + duration
        while time.time() < deadline:
            line = serial_readline(fd, timeout=2)
            if line is None:
                continue
            if line.startswith("EVENT 20"):
                if current:
                    pan_list.append(current)
                current = {}
            elif line.startswith("EVENT 22"):
                if current:
                    pan_list.append(current)
                scan_done = True
                break  # Exit loop once EVENT 22 received
            elif ":" in line and not line.startswith("EVENT"):
                key, _, val = line.strip().partition(":")
                current[key.strip()] = val.strip()

        if pan_list:
            log("SKSCAN found {} PAN(s), selecting best LQI".format(len(pan_list)))
            pan_list.sort(key=lambda p: int(p.get("LQI", "0"), 16), reverse=True)
            return pan_list[0]

        if LOGGER is not None:
            emit_scan_retry(LOGGER, duration=duration + 1)
        else:
            log("SKSCAN no PAN found, retrying with longer duration")
        if diag_state is not None:
            try:
                diag_state.on_scan_retry()
            except Exception as e:
                log("diag on_scan_retry error: {}".format(e))
        duration += 1

    return {}

def skll64(fd, mac):
    """Convert MAC address to IPv6 link-local address.

    Reads lines until an IPv6-like substring (hex digits + colons) is found
    and validated. Returns the candidate string or None on timeout.
    """
    serial_write(fd, "SKLL64 {}\r\n".format(mac))
    deadline = time.time() + 10
    while time.time() < deadline:
        line = serial_readline(fd, timeout=2)
        if not line:
            continue
        # skip echoes and obvious non-data lines
        if line.startswith("SKLL64") or line.strip() == "":
            continue
        # extract only hex+colon runs (length threshold to avoid short noise)
        m = re.search(r'([0-9A-Fa-f:]{15,})', line)
        if not m:
            continue
        candidate = m.group(1)
        # validate with inet_pton if available
        try:
            socket.inet_pton(socket.AF_INET6, candidate)
            return candidate
        except Exception:
            # not valid IPv6; continue waiting for a proper response
            log("skll64: received candidate but validation failed: {}".format(candidate))
            continue
    return None

def wisun_connect(fd, br_id, br_pwd, diag_state=None):
    """Full SKSTACK-IP join sequence. Returns IPv6 address of meter.

    `diag_state` is forwarded to skscan and the PAN info is recorded onto it
    once a usable PAN is selected.
    """
    log("SKRESET")
    skcommand(fd, "SKRESET", timeout=5)
    time.sleep(1)

    # SKVER は起動時の identity log として残す (debug 用、 副作用なし)。
    try:
        ver = skcommand(fd, "SKVER", timeout=2)
        log("SKVER: {}".format(ver))
    except Exception as e:
        log("SKVER failed: {}".format(e))

    log("SKSETPWD")
    skcommand(fd, "SKSETPWD C {}".format(br_pwd))

    log("SKSETRBID")
    skcommand(fd, "SKSETRBID {}".format(br_id))

    # Force ASCII-hex ERXUDP payload format so parser stays stable.
    skcommand(fd, "WOPT 1")

    log("SKSCAN (may take up to 60s)")
    pan = skscan(fd, diag_state=diag_state)
    if not pan.get("Channel") or not pan.get("Pan ID") or not pan.get("Addr"):
        raise RuntimeError("SKSCAN: no PAN found ({})".format(pan))

    channel = pan["Channel"]
    pan_id  = pan["Pan ID"]
    mac     = pan["Addr"]
    log("PAN found: ch={} panId={} mac={}".format(channel, pan_id, mac))
    if diag_state is not None:
        try:
            diag_state.on_wisun_joined(pan)
        except Exception as e:
            log("diag on_wisun_joined error: {}".format(e))

    ipv6 = skll64(fd, mac)
    if not ipv6:
        raise RuntimeError("SKLL64 failed")
    log("Meter IPv6: {}".format(ipv6))

    skcommand(fd, "SKSREG S2 {}".format(channel))
    skcommand(fd, "SKSREG S3 {}".format(pan_id))

    log("SKJOIN {}".format(ipv6))
    serial_write(fd, "SKJOIN {}\r\n".format(ipv6))

    orig_led = led_read()
    stop_event = threading.Event()
    t = threading.Thread(target=_led_blink,
                         args=(stop_event, [(0, 255, 0), (0, 0, 255)]))
    t.daemon = True
    t.start()
    try:
        deadline = time.time() + 90
        while time.time() < deadline:
            line = serial_readline(fd, timeout=2)
            if line is None:
                continue
            if "EVENT 25" in line:
                if LOGGER is not None:
                    emit_wisun_joined(LOGGER, pan=pan, ipv6=ipv6)
                else:
                    log("SKJOIN: connected")
                # spec 010 で undocumented `SKSCAN 0 ... 0` (SIDE arg) が
                # EEDSCAN を返すと確定したので、 起動時 sweep は外し、
                # main loop で eedscan_state による定期実行に統一。
                return ipv6
            if "EVENT 24" in line:
                if LOGGER is not None:
                    emit_wisun_join_failed(LOGGER,
                                           reason="PANA authentication failed (EVENT 24)")
                raise RuntimeError("SKJOIN: PANA authentication failed (EVENT 24)")
    finally:
        stop_event.set()
        t.join(timeout=1)
        led_rgb(*orig_led)

    raise RuntimeError("SKJOIN: timeout")

# ---------------------------------------------------------------------------
# ECHONET Lite frame builder / parser
# ---------------------------------------------------------------------------

EPCS = [0xD3, 0xE1, 0xE7, 0xE0, 0xE3, 0xE8]

# Spec 011 C: EPC tier rotation. Cycle-by-cycle EPC selection keeps each
# SKSENDTO payload small (= faster meter response) and lets HA receive
# what changes the most (power) at full rate while less-volatile data is
# refreshed at a lower cadence.
TIER1_EPCS = [0xE7, 0xE8]         # 瞬時電力、 瞬時電流 — real-time
TIER2_EPCS = [0xE0, 0xE3]         # 積算電力量 (forward / reverse) — slow
TIER3_EPCS = [0xD3, 0xE1]         # 係数 / 単位 — near-static


def decide_epc_tier(cycle_number, tier2_every=5, tier3_every=60):
    """Pick which EPC tier to query for cycle *cycle_number*. tier3 wins
    over tier2 when both intervals align so the rarest data still refreshes
    on schedule."""
    if cycle_number % int(tier3_every) == 0:
        return "tier3"
    if cycle_number % int(tier2_every) == 0:
        return "tier2"
    return "tier1"


def epcs_for_tier(tier):
    if tier == "tier2":
        return TIER2_EPCS
    if tier == "tier3":
        return TIER3_EPCS
    return TIER1_EPCS

def build_el_get(tid, epcs):
    frame = bytearray()
    frame += b"\x10\x81"                     # EHD1, EHD2
    frame += struct.pack(">H", tid & 0xFFFF) # TID
    frame += b"\x05\xFF\x01"                 # SEOJ: controller
    frame += b"\x02\x88\x01"                 # DEOJ: smart meter
    frame += b"\x62"                         # ESV: Get
    frame += struct.pack("B", len(epcs))     # OPC
    for epc in epcs:
        frame += struct.pack("BB", epc, 0)   # EPC, PDC=0
    return bytes(frame)

def parse_el_response(data):
    """Returns dict {epc_int: bytearray}."""
    if len(data) < 12:
        return {}
    esv = data[10] if isinstance(data[10], int) else ord(data[10])
    opc = data[11] if isinstance(data[11], int) else ord(data[11])
    # Accept Get_Res (0x72) or Get_SNA (0x52)
    if esv not in (0x72, 0x52):
        return {}
    result = {}
    pos = 12
    for _ in range(opc):
        if pos + 2 > len(data):
            break
        epc = data[pos] if isinstance(data[pos], int) else ord(data[pos])
        pdc = data[pos+1] if isinstance(data[pos+1], int) else ord(data[pos+1])
        pos += 2
        if pos + pdc > len(data):
            break
        result[epc] = bytearray(data[pos:pos+pdc])
        pos += pdc
    return result

def decode_measurements(props):
    result = {}

    # D3: coefficient (4-byte unsigned)
    if 0xD3 in props and len(props[0xD3]) >= 4:
        result["coefficient"] = struct.unpack(">I", bytes(props[0xD3][:4]))[0]

    # E1: unit exponent byte
    if 0xE1 in props and len(props[0xE1]) >= 1:
        unit_byte = props[0xE1][0]
        unit_map = {0x00: 1.0, 0x01: 0.1,  0x02: 0.01,   0x03: 0.001, 0x04: 0.0001,
                    0x0A: 10.0, 0x0B: 100.0, 0x0C: 1000.0, 0x0D: 10000.0}
        result["unit_kwh"] = unit_map.get(unit_byte, 1.0)

    # E7: instantaneous power W (4-byte signed)
    if 0xE7 in props and len(props[0xE7]) >= 4:
        result["power_w"] = struct.unpack(">i", bytes(props[0xE7][:4]))[0]

    # E0: cumulative forward kWh (4-byte unsigned × coeff × unit)
    if 0xE0 in props and len(props[0xE0]) >= 4:
        result["energy_forward_raw"] = struct.unpack(">I", bytes(props[0xE0][:4]))[0]

    # E3: cumulative reverse kWh (4-byte unsigned × coeff × unit)
    if 0xE3 in props and len(props[0xE3]) >= 4:
        result["energy_reverse_raw"] = struct.unpack(">I", bytes(props[0xE3][:4]))[0]

    # E8: instantaneous current R,T phase (2×signed short, 0.1A)
    #
    # ECHONET Lite low-voltage smart meter class defines:
    #   - valid range 0x8001..0x7FFD (-3276.7 .. +3276.5 A)
    #   - 0x7FFE = "not measured" / phase not connected
    #   - 0x7FFF = reserved
    # Drop both sentinels so a 単相2線式 contract or unwired T phase shows up
    # as "unknown" in HA rather than the literal 3276.6 A.
    if 0xE8 in props and len(props[0xE8]) >= 4:
        r_raw, t_raw = struct.unpack(">HH", bytes(props[0xE8][:4]))
        if r_raw not in (0x7FFE, 0x7FFF):
            r_signed = struct.unpack(">h", struct.pack(">H", r_raw))[0]
            result["current_r_a"] = r_signed / 10.0
        if t_raw not in (0x7FFE, 0x7FFF):
            t_signed = struct.unpack(">h", struct.pack(">H", t_raw))[0]
            result["current_t_a"] = t_signed / 10.0

    return result

def apply_energy_scale(measurements, coeff, unit_kwh):
    c = measurements.get("coefficient", coeff)
    u = measurements.get("unit_kwh", unit_kwh)
    if "energy_forward_raw" in measurements:
        measurements["energy_forward_kwh"] = measurements["energy_forward_raw"] * c * u
    if "energy_reverse_raw" in measurements:
        measurements["energy_reverse_kwh"] = measurements["energy_reverse_raw"] * c * u
    return measurements

# ---------------------------------------------------------------------------
# Send ECHONET Lite Get via SKSENDTO
# ---------------------------------------------------------------------------

def send_el_get(fd, ipv6, tid, epc_list=None):
    """Send an ECHONET Get request to *ipv6*. Defaults to the full
    measurement EPC set; pass `epc_list=[0x80]` for a lightweight probe
    (spec 009)."""
    epcs = EPCS if epc_list is None else list(epc_list)
    frame = build_el_get(tid, epcs)
    # SKSENDTO expects 4-hex-digit payload length and trailing CRLF after raw data.
    cmd = "SKSENDTO 1 {} 0E1A 1 0 {:04X} ".format(ipv6, len(frame))
    serial_write(fd, cmd)
    serial_write(fd, frame)
    serial_write(fd, b"\r\n")


def _run_eedscan_sweep(fd, eedscan_state, diag_state):
    """Fire one EEDSCAN (SKSCAN mode 0) sweep and parse the EEDSCAN line.

    `SKSCAN 0 0FFFFFFF 4 0` covers BP35CX channels 33-60 (ch33 = bit 0 of
    `0FFFFFFF`). Duration code 4 → ~12 s end-to-end. Records the result
    onto *eedscan_state* on success."""
    serial_write(fd, "SKSCAN 0 0FFFFFFF 4 0\r\n")
    deadline = time.time() + 30.0
    saw_ok = False
    payload = None
    while time.time() < deadline:
        line = serial_readline(fd, timeout=2)
        if line is None:
            continue
        # EVENT/FAIL go to the existing health counters where applicable.
        kind = classify_sk_line(line)
        if kind is not None and diag_state is not None:
            try:
                if kind[0] == "event":
                    diag_state.on_sk_event(kind[1])
                elif kind[0] == "error":
                    diag_state.on_sk_error(kind[1])
            except Exception:
                pass
        if line.startswith("OK"):
            saw_ok = True
            continue
        if line.startswith("FAIL"):
            log("EEDSCAN rejected: {}".format(line))
            return
        if line.startswith("EEDSCAN"):
            # The data line follows; next readline holds the pairs.
            next_line = serial_readline(fd, timeout=2)
            payload = (next_line or "").strip()
            break
    if not saw_ok or payload is None:
        log("EEDSCAN: no data line received")
        return
    result = parse_eedscan(payload)
    if not result:
        log("EEDSCAN: parse returned empty for {!r}".format(payload))
        return
    eedscan_state.record(result, time.time())
    log("EEDSCAN OK: {} channels, max={:02X} min={:02X}".format(
        len(result), max(result.values()), min(result.values())))


# Lightweight EPC set used during probe mode. 0x80 (operation status) is
# a static 1-byte property the meter returns from internal flags, so RTT
# is closer to the pure Wi-SUN round-trip than 0xE7 (instant power, which
# requires the meter to take a fresh measurement).
PROBE_EPCS = [0x80]

def read_erxudp(fd, timeout=15, diag_state=None):
    """Wait for ERXUDP and return payload as bytearray, or None.

    When *diag_state* is supplied, EVENT/FAIL lines observed before the
    ERXUDP are dispatched into Wi-SUN health counters (spec 006).
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = serial_readline(fd, timeout=max(0.5, deadline - time.time()))
        if line is None:
            continue
        classified = classify_sk_line(line)
        if classified is None:
            continue
        kind, value = classified
        if kind == "event":
            if diag_state is not None:
                try:
                    diag_state.on_sk_event(value)
                except Exception as e:
                    log("diag on_sk_event error: {}".format(e))
            continue
        if kind == "error":
            if diag_state is not None:
                try:
                    diag_state.on_sk_error(value)
                except Exception as e:
                    log("diag on_sk_error error: {}".format(e))
            continue
        if kind == "erxudp":
            if diag_state is not None:
                try:
                    diag_state.on_erxudp_raw(line)
                except Exception as e:
                    log("diag on_erxudp_raw error: {}".format(e))
            if not value.startswith("1081"):
                continue
            try:
                return bytearray(binascii.unhexlify(value))
            except Exception as e:
                log("ERXUDP hex decode error: {}".format(e))
    return None

# ---------------------------------------------------------------------------
# Minimal MQTT 3.1.1 client (raw socket, no paho)
# ---------------------------------------------------------------------------

def _encode_remaining(n):
    buf = b""
    while True:
        byte = n % 128
        n //= 128
        if n > 0:
            byte |= 0x80
        buf += struct.pack("B", byte)
        if n == 0:
            break
    return buf

def _encode_str(s):
    b = s.encode("utf-8")
    return struct.pack(">H", len(b)) + b

try:
    import Queue as _queue_mod  # Py2
except ImportError:
    import queue as _queue_mod  # Py3


class MQTTClient(object):
    def __init__(self, host, port, client_id, username=None, password=None,
                 on_reconnect=None, keepalive=60,
                 threading_enabled=False, send_queue_maxsize=1000):
        self.host              = host
        self.port              = port
        self.client_id         = client_id
        self.username          = username
        self.password          = password
        self.keepalive         = int(keepalive)
        self.threading_enabled = bool(threading_enabled)
        self.sock              = None
        self._out_queue        = collections.deque()  # legacy fallback only
        self.on_reconnect      = on_reconnect  # called after a successful re-connect

        # Threaded-mode primitives (spec 005). Allocated even in legacy mode
        # so that diagnostic snapshots (publish_dropped_total など) never
        # raise AttributeError.
        self.send_queue              = _queue_mod.Queue(maxsize=int(send_queue_maxsize))
        self.publish_dropped_total   = 0
        self.ping_failures_total     = 0
        self._send_lock              = threading.Lock()
        self._stop_event             = threading.Event()
        self._reconnect_event        = threading.Event()
        self._sender_thread          = None
        self._keepalive_thread       = None

    def connect(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(30)
        # Enable TCP keepalive where available
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            # platform-specific options
            for opt_name, opt_val in (('TCP_KEEPIDLE', 60), ('TCP_KEEPINTVL', 10), ('TCP_KEEPCNT', 3)):
                if hasattr(socket, opt_name):
                    try:
                        s.setsockopt(socket.IPPROTO_TCP, getattr(socket, opt_name), opt_val)
                    except Exception:
                        pass
        except Exception:
            pass

        s.connect((self.host, self.port))

        flags = 0x02  # clean session
        if self.username: flags |= 0x80
        if self.password: flags |= 0x40

        var_hdr = (b"\x00\x04MQTT"
                   + b"\x04"
                   + struct.pack("B", flags)
                   + struct.pack(">H", self.keepalive))

        payload = _encode_str(self.client_id)
        if self.username: payload += _encode_str(self.username)
        if self.password: payload += _encode_str(self.password)

        remaining = var_hdr + payload
        pkt = b"\x10" + _encode_remaining(len(remaining)) + remaining
        s.sendall(pkt)

        # read CONNACK
        s.settimeout(10)
        ack = b""
        while len(ack) < 4:
            chunk = s.recv(4 - len(ack))
            if not chunk:
                break
            ack += chunk
        s.settimeout(None)

        if len(ack) < 4 or (ack[0] if isinstance(ack[0], int) else ord(ack[0])) != 0x20:
            raise RuntimeError("MQTT: bad CONNACK ({})".format(binascii.hexlify(ack)))
        rc = ack[3] if isinstance(ack[3], int) else ord(ack[3])
        if rc != 0:
            raise RuntimeError("MQTT: connection refused code {}".format(rc))

        self.sock = s
        if LOGGER is not None:
            emit_mqtt_connected(LOGGER, host=self.host, port=self.port)
        else:
            log("MQTT connected to {}:{}".format(self.host, self.port))

        if self.threading_enabled:
            self._reconnect_event.clear()
            self._start_workers_if_needed()
        else:
            # legacy: flush in-memory deque
            try:
                self._flush_queue()
            except Exception as e:
                log("MQTT flush queue error: {}".format(e))

    def _make_pkt(self, topic, payload, retain=False):
        if isinstance(payload, dict):
            payload = json.dumps(payload, separators=(",", ":"))
        topic_b = topic.encode("utf-8")
        payload_b = payload.encode("utf-8") if isinstance(payload, str) else payload
        fixed = 0x30 | (0x01 if retain else 0x00)
        var_hdr = struct.pack(">H", len(topic_b)) + topic_b
        remaining = var_hdr + payload_b
        return struct.pack("B", fixed) + _encode_remaining(len(remaining)) + remaining

    def publish(self, topic, payload, retain=False):
        if self.threading_enabled:
            self._enqueue(topic, payload, retain)
            return
        # ---- legacy synchronous path (fallback when threading is disabled) ----
        pkt = self._make_pkt(topic, payload, retain)
        try:
            if not self.sock:
                raise RuntimeError("No MQTT socket")
            self.sock.sendall(pkt)
            return
        except Exception as e:
            log("MQTT publish error: {}".format(e))
            # try reconnect and resend
            try:
                self._reconnect()
            except Exception as e2:
                log("MQTT reconnect failed after publish error: {}".format(e2))
                # queue the message for later delivery
                try:
                    self._out_queue.append((topic, payload, retain))
                except Exception:
                    pass
                return

            try:
                self.sock.sendall(pkt)
                return
            except Exception as e3:
                log("MQTT publish retry failed: {}".format(e3))
                try:
                    self._out_queue.append((topic, payload, retain))
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Threaded mode helpers (spec 005-mqtt-threading)
    # ------------------------------------------------------------------

    def _enqueue(self, topic, payload, retain):
        """Non-blocking enqueue. On saturation drops the OLDEST entry (FIFO)
        and bumps publish_dropped_total so the most-recent value survives —
        important for HA dashboards that display "now"."""
        item = (topic, payload, retain)
        try:
            self.send_queue.put_nowait(item)
        except _queue_mod.Full:
            try:
                self.send_queue.get_nowait()
                self.publish_dropped_total += 1
            except _queue_mod.Empty:
                pass
            try:
                self.send_queue.put_nowait(item)
            except _queue_mod.Full:
                # Worker is faster than the drop loop expected; bump anyway.
                self.publish_dropped_total += 1

    def _start_workers_if_needed(self):
        if self._sender_thread is None or not self._sender_thread.is_alive():
            self._sender_thread = threading.Thread(
                target=self._sender_loop, name="mqtt-sender")
            self._sender_thread.daemon = True
            self._sender_thread.start()
        if self._keepalive_thread is None or not self._keepalive_thread.is_alive():
            self._keepalive_thread = threading.Thread(
                target=self._keepalive_loop, name="mqtt-keepalive")
            self._keepalive_thread.daemon = True
            self._keepalive_thread.start()

    def _sender_loop(self):
        """Pull from send_queue, send under _send_lock. On send failure or
        on a reconnect_event from the keepalive worker, perform an in-thread
        reconnect (also under lock so PINGREQ can't race)."""
        while not self._stop_event.is_set():
            if self._reconnect_event.is_set():
                self._reconnect_event.clear()
                try:
                    self._reconnect_under_lock()
                except Exception as e:
                    log("MQTT sender reconnect failed: {}".format(e))
            try:
                item = self.send_queue.get(timeout=1.0)
            except _queue_mod.Empty:
                continue
            if item is None:
                # stop sentinel
                self.send_queue.task_done()
                break
            topic, payload, retain = item
            pkt = self._make_pkt(topic, payload, retain)
            ok = self._send_under_lock(pkt)
            self.send_queue.task_done()
            if not ok:
                # Reconnect synchronously inside this thread; then retry once.
                try:
                    self._reconnect_under_lock()
                    self._send_under_lock(pkt)  # best-effort retry, no further loop
                except Exception as e:
                    log("MQTT sender post-reconnect retry failed: {}".format(e))

    def _keepalive_loop(self):
        """Send PINGREQ every keepalive/2 seconds. On send failure signal the
        sender thread to reconnect; do not attempt the reconnect here to keep
        the connect serialization simple."""
        interval = max(1.0, self.keepalive / 2.0)
        while not self._stop_event.wait(interval):
            ok = self._send_under_lock(b"\xC0\x00")
            if not ok:
                self.ping_failures_total += 1
                self._reconnect_event.set()

    def _send_under_lock(self, pkt):
        with self._send_lock:
            sock = self.sock
            if sock is None:
                return False
            try:
                sock.sendall(pkt)
                return True
            except Exception as e:
                log("MQTT socket send failed: {}".format(e))
                return False

    def _reconnect_under_lock(self):
        with self._send_lock:
            self._reconnect_socket_unlocked()

    def _reconnect_socket_unlocked(self):
        if LOGGER is not None:
            emit_mqtt_reconnect(LOGGER)
        else:
            log("MQTT reconnecting …")
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass
        self.sock = None
        # Re-establish; connect() itself does not take _send_lock so it is
        # safe to call from here.  Keep retrying until success or shutdown.
        while not self._stop_event.is_set():
            try:
                self._do_connect_socket_only()
                if self.on_reconnect is not None:
                    try:
                        self.on_reconnect()
                    except Exception as e:
                        log("on_reconnect callback error: {}".format(e))
                return
            except Exception as e:
                log("MQTT reconnect failed: {} - retry in 15s".format(e))
                # Use stop_event so shutdown wakes us promptly.
                if self._stop_event.wait(15):
                    return

    def _do_connect_socket_only(self):
        """Re-execute the TCP+CONNECT handshake without starting new workers
        (we are *inside* the sender worker)."""
        prev_flag = self.threading_enabled
        self.threading_enabled = False
        try:
            self.connect()
        finally:
            self.threading_enabled = prev_flag

    def shutdown(self, timeout=5.0):
        self._stop_event.set()
        try:
            # nudge sender out of the blocking get()
            self.send_queue.put_nowait(None)
        except Exception:
            pass
        for t in (self._sender_thread, self._keepalive_thread):
            if t is not None and t.is_alive():
                try:
                    t.join(timeout)
                except Exception:
                    pass
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass
        self.sock = None

    def _flush_queue(self):
        while self._out_queue and self.sock:
            topic, payload, retain = self._out_queue[0]
            try:
                pkt = self._make_pkt(topic, payload, retain)
                self.sock.sendall(pkt)
                self._out_queue.popleft()
            except Exception as e:
                log("MQTT queued publish failed: {}".format(e))
                break

    def ping(self):
        if self.threading_enabled:
            # The keepalive worker owns PINGREQ in threaded mode.
            return
        try:
            self.sock.sendall(b"\xC0\x00")
        except Exception as e:
            log("MQTT ping error: {}".format(e))
            self._reconnect()
            return
        # wait for PINGRESP (should be 0xD0 0x00)
        try:
            r, _, _ = select.select([self.sock], [], [], 5)
            if r:
                resp = self.sock.recv(2)
                if not resp:
                    log("MQTT ping: no response (empty)")
                    self._reconnect()
                elif len(resp) < 2:
                    log("MQTT ping: incomplete response (len={})".format(len(resp)))
                    self._reconnect()
                else:
                    first_byte = resp[0] if isinstance(resp[0], int) else ord(resp[0])
                    if first_byte != 0xD0:
                        log("MQTT ping: unexpected response first_byte=0x{:02X}".format(first_byte))
                        self._reconnect()
            else:
                log("MQTT ping: timeout (no data within 5s)")
                self._reconnect()
        except Exception as e:
            log("MQTT ping recv error: {}".format(e))
            self._reconnect()

    def _reconnect(self):
        if LOGGER is not None:
            emit_mqtt_reconnect(LOGGER)
        else:
            log("MQTT reconnecting …")
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass
        self.sock = None
        while True:
            try:
                self.connect()
                if self.on_reconnect is not None:
                    try:
                        self.on_reconnect()
                    except Exception as e:
                        log("on_reconnect callback error: {}".format(e))
                return
            except Exception as e:
                log("MQTT reconnect failed: {} - retry in 15s".format(e))
                time.sleep(15)

# ---------------------------------------------------------------------------
# Home Assistant MQTT auto-discovery
# ---------------------------------------------------------------------------

SENSOR_DEFS = [
    ("power",          "Instantaneous Power",  "W",   "power",   "measurement"),
    ("energy_forward", "Cumulative Energy Fwd", "kWh", "energy",  "total_increasing"),
    ("energy_reverse", "Cumulative Energy Rev", "kWh", "energy",  "total_increasing"),
    ("current_r",      "Current R Phase",       "A",   "current", "measurement"),
    ("current_t",      "Current T Phase",       "A",   "current", "measurement"),
]

def _device_dict(device_id):
    return {
        "identifiers": [device_id],
        "name":         "Cube J1 Smart Meter",
        "model":        "Cube J1",
        "manufacturer": "NextDrive",
    }


# (key, name, unit, device_class, state_class, entity_category)
# Mirrors spec data-model.md.
DIAG_SENSOR_DEFS = [
    ("last_poll_success_ts",   "Last Poll Success",   None, "timestamp", None,               "diagnostic"),
    ("last_poll_failure_ts",   "Last Poll Failure",   None, "timestamp", None,               "diagnostic"),
    ("lqi",                    "LQI",                 None, None,        "measurement",      "diagnostic"),
    ("pan_channel",            "PAN Channel",         None, None,        "measurement",      "diagnostic"),
    ("scan_retries_total",     "Scan Retries",        None, None,        "total_increasing", "diagnostic"),
    ("wisun_reconnects_total", "Wi-SUN Reconnects",   None, None,        "total_increasing", "diagnostic"),
    ("mqtt_reconnects_total",  "MQTT Reconnects",     None, None,        "total_increasing", "diagnostic"),
    ("erxudp_timeouts_total",  "ERXUDP Timeouts",     None, None,        "total_increasing", "diagnostic"),
    ("erxudp_intra_cycle_retries_total", "ERXUDP Intra-Cycle Retries", None, None, "total_increasing", "diagnostic"),
    ("erxudp_recovered_by_retry_total",  "ERXUDP Recovered by Retry",  None, None, "total_increasing", "diagnostic"),
    ("uptime_seconds",         "Uptime",              "s",  None,        "measurement",      "diagnostic"),
    ("version",                "Bridge Version",      None, None,        None,               "diagnostic"),
    # Spec 006: Wi-SUN health observability — RTT distribution + EVENT/FAIL.
    ("erxudp_latency_p50_ms",  "ERXUDP Latency p50",  "ms", None,        "measurement",      "diagnostic"),
    ("erxudp_latency_p95_ms",  "ERXUDP Latency p95",  "ms", None,        "measurement",      "diagnostic"),
    ("erxudp_latency_max_ms",  "ERXUDP Latency Max",  "ms", None,        "measurement",      "diagnostic"),
    ("sk_event_22_total",      "SK EVENT 22 (PANA OK)",       None, None, "total_increasing", "diagnostic"),
    ("sk_event_24_total",      "SK EVENT 24 (PANA Failed)",   None, None, "total_increasing", "diagnostic"),
    ("sk_event_25_total",      "SK EVENT 25 (PANA Done)",     None, None, "total_increasing", "diagnostic"),
    ("sk_event_26_total",      "SK EVENT 26 (Re-auth)",       None, None, "total_increasing", "diagnostic"),
    ("sk_event_28_total",      "SK EVENT 28 (Session End)",   None, None, "total_increasing", "diagnostic"),
    ("sk_event_29_total",      "SK EVENT 29 (Session Timeout)", None, None, "total_increasing", "diagnostic"),
    ("sk_event_32_total",      "SK EVENT 32 (Scan Done)",     None, None, "total_increasing", "diagnostic"),
    ("sk_event_33_total",      "SK EVENT 33 (Scan Started)",  None, None, "total_increasing", "diagnostic"),
    ("sk_error_ER05_total",    "SK FAIL ER05",                None, None, "total_increasing", "diagnostic"),
    ("sk_error_ER09_total",    "SK FAIL ER09",                None, None, "total_increasing", "diagnostic"),
    ("sk_error_ER10_total",    "SK FAIL ER10",                None, None, "total_increasing", "diagnostic"),
    # Spec 010: EEDSCAN 920MHz noise floor.
    ("eedscan_pan_channel_energy", "EEDSCAN energy (PAN ch)", None, None, "measurement", "diagnostic"),
    ("eedscan_max_energy",     "EEDSCAN max energy",          None, None, "measurement", "diagnostic"),
    ("eedscan_min_energy",     "EEDSCAN min energy",          None, None, "measurement", "diagnostic"),
]


def _build_discovery_config(device_id, sid, name, unit, dev_class, state_class,
                            state_topic, entity_category=None):
    """Build a Home Assistant Auto-Discovery sensor config dict.

    Optional fields (unit_of_measurement, device_class, state_class,
    entity_category) are omitted when None so the payload stays minimal and
    HA doesn't reject unknown values.
    """
    cfg = {
        "name":        name,
        "unique_id":   "{}_{}".format(device_id, sid),
        "state_topic": state_topic,
        "device":      _device_dict(device_id),
    }
    if unit is not None:
        cfg["unit_of_measurement"] = unit
    if dev_class is not None:
        cfg["device_class"] = dev_class
    if state_class is not None:
        cfg["state_class"] = state_class
    if entity_category is not None:
        cfg["entity_category"] = entity_category
    return cfg


def publish_ha_discovery_diag(mqtt, device_id):
    """Publish HA Auto-Discovery configs for the 10 diagnostic sensors."""
    base = "cubej/{}".format(device_id)
    for sid, name, unit, dev_class, state_class, entity_category in DIAG_SENSOR_DEFS:
        topic = "homeassistant/sensor/{}/{}/config".format(device_id, sid)
        state_topic = "{}/diag/{}".format(base, sid)
        cfg = _build_discovery_config(
            device_id, sid, name, unit, dev_class, state_class,
            state_topic, entity_category=entity_category,
        )
        mqtt.publish(topic, cfg, retain=True)
        log("HA discovery: {}".format(topic))


def publish_ha_discovery(mqtt, device_id):
    base = "cubej/{}".format(device_id)
    for sid, name, unit, dev_class, state_class in SENSOR_DEFS:
        topic = "homeassistant/sensor/{}/{}/config".format(device_id, sid)
        state_topic = "{}/{}".format(base, sid)
        cfg = _build_discovery_config(
            device_id, sid, name, unit, dev_class, state_class, state_topic,
        )
        mqtt.publish(topic, cfg, retain=True)
        log("HA discovery: {}".format(topic))
    # Publish diagnostic sensor configs as part of the single entry point.
    publish_ha_discovery_diag(mqtt, device_id)


def publish_diag(mqtt, device_id, snapshot):
    """Publish each non-None snapshot entry to cubej/<id>/diag/<key>.

    All diagnostic topics are sent with retain=True so HA / broker restarts
    do not blank out the last known value.
    """
    base = "cubej/{}".format(device_id)
    for sid, _name, _unit, _dc, _sc, _ec in DIAG_SENSOR_DEFS:
        if sid not in snapshot:
            continue
        value = snapshot[sid]
        if value is None:
            continue
        mqtt.publish("{}/diag/{}".format(base, sid), str(value), retain=True)

def publish_measurements(mqtt, device_id, m):
    base = "cubej/{}".format(device_id)
    if "power_w" in m:
        mqtt.publish("{}/power".format(base), str(m["power_w"]))
    if "energy_forward_kwh" in m:
        mqtt.publish("{}/energy_forward".format(base), "{:.3f}".format(m["energy_forward_kwh"]))
    if "energy_reverse_kwh" in m:
        mqtt.publish("{}/energy_reverse".format(base), "{:.3f}".format(m["energy_reverse_kwh"]))
    if "current_r_a" in m:
        mqtt.publish("{}/current_r".format(base), "{:.1f}".format(m["current_r_a"]))
    if "current_t_a" in m:
        mqtt.publish("{}/current_t".format(base), "{:.1f}".format(m["current_t_a"]))

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    cfg           = apply_defaults(load_config())
    br_id         = cfg["br_id"]
    br_pwd        = cfg["br_pwd"]
    ha_host       = cfg["mqtt_host"]
    ha_port       = int(cfg.get("mqtt_port", 1883))
    ha_user       = cfg.get("mqtt_user", "")
    ha_pass       = cfg.get("mqtt_pass", "")
    device_id     = cfg.get("device_id", "cubej1")
    serial_port   = cfg.get("serial_port", "/dev/ttyS1")
    poll_interval = int(cfg.get("poll_interval", 60))

    global LOGGER
    LOGGER = JsonLogger(LOG_PATH, level=cfg["log_level"],
                        max_bytes=int(cfg["log_max_bytes"]),
                        backup_count=int(cfg["log_backup_count"]))
    emit_bridge_start(LOGGER, device_id=device_id, version=bridge_version())

    # Diagnostics aggregator declared early so the admin UI can read it.
    diag_state = DiagState(start_time=time.time(), version=bridge_version())

    # spec 009: high-frequency RTT probe mode. In-memory; resets at restart.
    probe_state = ProbeState()

    # spec 010: periodic EEDSCAN to track 920MHz noise floor.
    eedscan_state = EedScanState(
        interval_sec=int(cfg.get("eedscan_interval_sec", 300)))

    # Embedded admin UI (Constitution VI). Off by default; opted in via
    # config. Failure to start is logged but never aborts the bridge.
    if cfg.get("admin_ui_enabled") and cfg.get("admin_user") and cfg.get("admin_password"):
        try:
            start_admin_server(
                port=int(cfg.get("admin_ui_port", 8080)),
                user=cfg["admin_user"],
                password=cfg["admin_password"],
                diag_state_provider=lambda: diag_state,
                ap_controller=ApController(),
                probe_state_provider=lambda: probe_state,
                eedscan_state_provider=lambda: eedscan_state,
            )
            LOGGER.info(event="admin_ui_started",
                        context={"port": int(cfg.get("admin_ui_port", 8080))})
        except Exception as e:
            LOGGER.error(event="admin_ui_start_failed",
                         context={"error": str(e)})

    # Connect MQTT. The on_reconnect callback bumps the diag counter every
    # time the broker session is re-established.
    mqtt = MQTTClient(ha_host, ha_port, "cubej1_{}".format(device_id),
                      username=ha_user, password=ha_pass,
                      on_reconnect=diag_state.on_mqtt_reconnect,
                      keepalive=cfg["mqtt_keepalive"],
                      threading_enabled=cfg["mqtt_threading_enabled"],
                      send_queue_maxsize=cfg["mqtt_send_queue_maxsize"])
    while True:
        try:
            mqtt.connect()
            break
        except Exception as e:
            log("MQTT connect failed: {} - retry in 15s".format(e))
            time.sleep(15)

    publish_ha_discovery(mqtt, device_id)

    # Open serial port
    log("Opening serial {}".format(serial_port))
    fd = None
    while True:
        try:
            fd = open_serial(serial_port)
            break
        except Exception as e:
            log("Serial open failed: {} - retry in 10s".format(e))
            time.sleep(10)

    # Wi-SUN join (initial)
    ipv6 = None
    while True:
        try:
            ipv6 = wisun_connect(fd, br_id, br_pwd, diag_state=diag_state)
            break
        except Exception as e:
            log("Wi-SUN join failed: {} - retry in 60s".format(e))
            time.sleep(60)

    log("Meter connected at {}".format(ipv6))

    tid       = 1
    coeff     = 1
    unit_kwh  = 1.0
    last_ping = time.time()
    # spec 009 mixed pattern: track the last normal-EPCS cycle so probe
    # mode can interleave fast probes without starving HA of power values.
    last_normal_poll_start = 0.0
    # spec 011 C: count normal cycles to rotate EPC tier across the
    # tier1 (per cycle), tier2 (5 cycles), tier3 (60 cycles) schedule.
    normal_cycle_count = 0

    while True:
        try:
            last_poll_start = time.time()
            probing = probe_state.is_active(last_poll_start)
            kind = decide_cycle_kind(probing, last_normal_poll_start,
                                     last_poll_start, poll_interval)
            if kind == "probe":
                cycle_epcs = PROBE_EPCS
            else:
                # spec 011 C: tier rotation. Smaller per-cycle payload =
                # faster meter response + less data starvation on the
                # rare-but-needed tiers.
                tier = decide_epc_tier(normal_cycle_count)
                cycle_epcs = epcs_for_tier(tier)
                normal_cycle_count += 1
                last_normal_poll_start = last_poll_start
            # In probe mode, schedule the next cycle at the tight probe
            # interval; the normal mixed-in cycle uses the same interval so
            # we don't oversleep into the next probe slot.
            cycle_interval = probe_state.interval_sec if probing else poll_interval
            orig_led = led_read()
            led_rgb(0, 0, 255)
            try:
                # spec 011: ERXUDP resilience — extend timeout to handle
                # the p95 tail and add intra-cycle retries to mask single-
                # cycle drops from HA's perspective.
                _erxudp_timeout = int(cfg.get("erxudp_timeout_sec", 30))
                _max_retries = int(cfg.get("erxudp_intra_cycle_retries", 2))
                _backoff = float(cfg.get("erxudp_retry_backoff_sec", 2))
                send_el_get(fd, ipv6, tid, epc_list=cycle_epcs)
                tid = (tid + 1) & 0xFFFF
                t_send = time.time()
                data = read_erxudp(fd, timeout=_erxudp_timeout,
                                   diag_state=diag_state)
                attempt = 0
                while data is None and should_retry_in_cycle(attempt, _max_retries):
                    try:
                        diag_state.on_erxudp_intra_cycle_retry()
                    except Exception as e:
                        log("diag on_erxudp_intra_cycle_retry error: {}".format(e))
                    time.sleep(_backoff)
                    send_el_get(fd, ipv6, tid, epc_list=cycle_epcs)
                    tid = (tid + 1) & 0xFFFF
                    t_send = time.time()
                    data = read_erxudp(fd, timeout=_erxudp_timeout,
                                       diag_state=diag_state)
                    attempt += 1
                if data is not None and attempt > 0:
                    try:
                        diag_state.on_erxudp_recovered_by_retry()
                    except Exception as e:
                        log("diag on_erxudp_recovered_by_retry error: {}".format(e))
                now = time.time()
                if data:
                    try:
                        diag_state.on_erxudp_latency((now - t_send) * 1000.0)
                    except Exception as e:
                        log("diag on_erxudp_latency error: {}".format(e))
                    props = parse_el_response(data)
                    m     = decode_measurements(props)
                    m     = apply_energy_scale(m, coeff, unit_kwh)
                    if "coefficient" in m:
                        coeff = m["coefficient"]
                    if "unit_kwh" in m:
                        unit_kwh = m["unit_kwh"]
                    if kind == "normal":
                        # Probe responses (0x80 only) carry no power values;
                        # publish only on real measurement cycles.
                        publish_measurements(mqtt, device_id, m)
                    emit_poll_success(LOGGER, measurements=m)
                    try:
                        diag_state.on_poll_success(now)
                    except Exception as e:
                        log("diag on_poll_success error: {}".format(e))
                else:
                    emit_poll_failure(LOGGER, reason="erxudp_timeout")
                    try:
                        diag_state.on_erxudp_timeout()
                        diag_state.on_poll_failure(now)
                    except Exception as e:
                        log("diag poll_failure error: {}".format(e))
                    # Safety net: if the meter has stopped replying while the
                    # Wi-SUN session still looks alive, bail out of the main
                    # loop so the outer except runs wisun_connect again.
                    if should_force_wisun_reconnect(
                        diag_state.consecutive_erxudp_timeouts,
                        int(cfg.get("erxudp_timeout_force_reconnect_threshold", 5)),
                    ):
                        raise RuntimeError(
                            "erxudp timeout {} times in a row, forcing wisun reconnect"
                            .format(diag_state.consecutive_erxudp_timeouts))
                # Publish diag snapshot once per poll cycle (FR-004).
                # best-effort: never let diag failure block the measurement
                # path (Constitution IV / FR-005).
                try:
                    snap = diag_state.snapshot(now)
                    # spec 010: merge EEDSCAN metrics in for the same publish
                    # cycle so Grafana plots line up.
                    try:
                        snap.update(eedscan_state.snapshot(
                            pan_channel=diag_state.pan_channel))
                    except Exception as e2:
                        log("eedscan snapshot error: {}".format(e2))
                    publish_diag(mqtt, device_id, snap)
                except Exception as e:
                    log("publish_diag error: {}".format(e))
            finally:
                led_rgb(*orig_led)

            if time.time() - last_ping > 50:
                mqtt.ping()
                last_ping = time.time()

            # spec 010: periodic EEDSCAN sweep. ~12 s blocking, so we only
            # fire it when its deadline has passed AND we're not in probe
            # mode (probe mode wants tight RTT samples and a 12 s gap
            # would distort the sparkline). Failure is non-fatal.
            if (cfg.get("eedscan_enabled", True)
                    and not probing
                    and eedscan_state.should_run(time.time())):
                try:
                    _run_eedscan_sweep(fd, eedscan_state, diag_state)
                except Exception as e:
                    log("EEDSCAN sweep error: {}".format(e))

            # Deadline-based pacing: keep ~cycle_interval between *poll starts*
            # rather than between cycle ends, so ERXUDP timeouts don't push
            # the next measurement out by (timeout + interval). In probe mode
            # cycle_interval is the tight probe interval (e.g. 5 s).
            time.sleep(compute_next_poll_sleep(last_poll_start, time.time(), cycle_interval))

        except Exception as e:
            log("Main loop error: {} - reconnecting Wi-SUN in 30s".format(e))
            time.sleep(30)
            try:
                ipv6 = wisun_connect(fd, br_id, br_pwd, diag_state=diag_state)
                log("Wi-SUN reconnected at {}".format(ipv6))
                try:
                    diag_state.on_wisun_reconnect()
                except Exception as e3:
                    log("diag on_wisun_reconnect error: {}".format(e3))
            except Exception as e2:
                log("Wi-SUN reconnect failed: {}".format(e2))


if __name__ == "__main__":
    main()
