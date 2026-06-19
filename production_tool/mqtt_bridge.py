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

# Bridge self-version. SemVer is updated manually; git short hash is overwritten
# by scripts/embed_git_hash.sh before USB distribution. When the embed script
# is skipped the version reports "<semver>+unknown" (FR-001 fallback).
BRIDGE_SEMVER = "1.0.0"
BRIDGE_GIT_HASH = "unknown"

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
    """Return a copy of *cfg* with optional observability keys filled in.

    Preserves every existing entry (FR-012). Unknown keys are passed through
    unchanged so future config additions don't crash older bridge versions.
    """
    out = dict(cfg)
    out.setdefault("log_level", "info")
    out.setdefault("log_max_bytes", 1048576)
    out.setdefault("log_backup_count", 3)
    return out

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
    "uptime_seconds",
    "version",
)


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

    # --- counters (monotonically non-decreasing) ---

    def on_scan_retry(self):
        self.scan_retries_total += 1

    def on_wisun_reconnect(self):
        self.wisun_reconnects_total += 1

    def on_mqtt_reconnect(self):
        self.mqtt_reconnects_total += 1

    def on_erxudp_timeout(self):
        self.erxudp_timeouts_total += 1

    # --- timestamps ---

    def on_poll_success(self, now):
        self.last_poll_success_ts = now

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

SCAN_DURATION_BASE = 4
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
    if 0xE8 in props and len(props[0xE8]) >= 4:
        r, t = struct.unpack(">hh", bytes(props[0xE8][:4]))
        result["current_r_a"] = r / 10.0
        result["current_t_a"] = t / 10.0

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

def send_el_get(fd, ipv6, tid):
    frame = build_el_get(tid, EPCS)
    # SKSENDTO expects 4-hex-digit payload length and trailing CRLF after raw data.
    cmd = "SKSENDTO 1 {} 0E1A 1 0 {:04X} ".format(ipv6, len(frame))
    serial_write(fd, cmd)
    serial_write(fd, frame)
    serial_write(fd, b"\r\n")

def read_erxudp(fd, timeout=15):
    """Wait for ERXUDP and return payload as bytearray, or None."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = serial_readline(fd, timeout=max(0.5, deadline - time.time()))
        if line is None:
            continue
        if line.startswith("ERXUDP"):
            parts = line.split()
            # Tail fields are stable: ... <secured> <side> <datalen> <data>
            if len(parts) >= 10:
                hex_data = parts[-1].strip()
                if not hex_data.startswith("1081"):
                    continue
                try:
                    return bytearray(binascii.unhexlify(hex_data))
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

class MQTTClient(object):
    def __init__(self, host, port, client_id, username=None, password=None,
                 on_reconnect=None):
        self.host         = host
        self.port         = port
        self.client_id    = client_id
        self.username     = username
        self.password     = password
        self.sock         = None
        self._out_queue   = collections.deque()
        self.on_reconnect = on_reconnect  # called after a successful re-connect

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
                   + b"\x00\x3C")   # keep-alive 60s

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

        # flush any queued messages
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
    ("uptime_seconds",         "Uptime",              "s",  None,        "measurement",      "diagnostic"),
    ("version",                "Bridge Version",      None, None,        None,               "diagnostic"),
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

    # Diagnostics aggregator. Counter / timestamp updates feed into MQTT
    # diag/* topics with retain=true (FR-001).
    diag_state = DiagState(start_time=time.time(), version=bridge_version())

    # Connect MQTT. The on_reconnect callback bumps the diag counter every
    # time the broker session is re-established.
    mqtt = MQTTClient(ha_host, ha_port, "cubej1_{}".format(device_id),
                      username=ha_user, password=ha_pass,
                      on_reconnect=diag_state.on_mqtt_reconnect)
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

    while True:
        try:
            orig_led = led_read()
            led_rgb(0, 0, 255)
            try:
                send_el_get(fd, ipv6, tid)
                tid = (tid + 1) & 0xFFFF
                data = read_erxudp(fd, timeout=15)
                now = time.time()
                if data:
                    props = parse_el_response(data)
                    m     = decode_measurements(props)
                    m     = apply_energy_scale(m, coeff, unit_kwh)
                    if "coefficient" in m:
                        coeff = m["coefficient"]
                    if "unit_kwh" in m:
                        unit_kwh = m["unit_kwh"]
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
                # Publish diag snapshot once per poll cycle (FR-004).
                # best-effort: never let diag failure block the measurement
                # path (Constitution IV / FR-005).
                try:
                    publish_diag(mqtt, device_id, diag_state.snapshot(now))
                except Exception as e:
                    log("publish_diag error: {}".format(e))
            finally:
                led_rgb(*orig_led)

            if time.time() - last_ping > 50:
                mqtt.ping()
                last_ping = time.time()

            time.sleep(poll_interval)

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
