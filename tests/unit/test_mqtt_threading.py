"""MQTTClient threading mode: send + keepalive workers decoupled from main.

spec 005-mqtt-threading: メインスレッドが何秒詰まっても broker session が
維持されるよう、 publish と PINGREQ をデーモンスレッドに分離する。
"""
import socket
import struct
import threading
import time

import pytest

import mqtt_bridge as mb


class _StubBroker(object):
    """Persistent broker stub. Accepts CONNECT, replies CONNACK, then records
    every subsequent packet (PUBLISH / PINGREQ / DISCONNECT) until closed."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.received_packets = []  # list of fixed-header bytes
        self.pingreqs = 0
        self.publishes = 0
        self._closed = threading.Event()
        self._thread = threading.Thread(target=self._serve)
        self._thread.daemon = True
        self._thread.start()

    def _serve(self):
        try:
            conn, _ = self.sock.accept()
        except Exception:
            return
        try:
            conn.settimeout(0.2)
            # Read CONNECT (variable length)
            self._drain_one(conn)
            conn.sendall(b"\x20\x02\x00\x00")  # CONNACK ok
            # Now stream subsequent packets
            while not self._closed.is_set():
                try:
                    head = conn.recv(1)
                except socket.timeout:
                    continue
                except Exception:
                    break
                if not head:
                    break
                fixed = head[0] if isinstance(head[0], int) else ord(head[0])
                # Read remaining-length VBI
                remaining, _ = self._read_vbi(conn)
                payload = b""
                while len(payload) < remaining:
                    chunk = conn.recv(remaining - len(payload))
                    if not chunk:
                        break
                    payload += chunk
                self.received_packets.append((fixed, payload))
                if fixed == 0xC0:  # PINGREQ
                    self.pingreqs += 1
                    try:
                        conn.sendall(b"\xD0\x00")  # PINGRESP
                    except Exception:
                        break
                elif (fixed & 0xF0) == 0x30:  # PUBLISH
                    self.publishes += 1
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _drain_one(self, conn):
        head = conn.recv(1)
        if not head:
            return
        remaining, _ = self._read_vbi(conn)
        got = b""
        while len(got) < remaining:
            chunk = conn.recv(remaining - len(got))
            if not chunk:
                break
            got += chunk

    def _read_vbi(self, conn):
        n = 0
        m = 1
        consumed = 0
        while True:
            b = conn.recv(1)
            if not b:
                return n, consumed
            consumed += 1
            v = b[0] if isinstance(b[0], int) else ord(b[0])
            n += (v & 0x7F) * m
            if not (v & 0x80):
                return n, consumed
            m *= 128

    def close(self):
        self._closed.set()
        try:
            self.sock.close()
        except Exception:
            pass


def test_publish_is_non_blocking_in_threaded_mode():
    """publish() must return without doing socket I/O on the caller's thread."""
    broker = _StubBroker()
    try:
        client = mb.MQTTClient(
            "127.0.0.1", broker.port, "test-nonblock",
            keepalive=300, threading_enabled=True,
        )
        client.connect()
        try:
            t0 = time.time()
            for _ in range(100):
                client.publish("t/x", "{}")
            elapsed = time.time() - t0
            assert elapsed < 0.05, "100 publish() calls took {:.3f}s".format(elapsed)
        finally:
            client.shutdown()
    finally:
        broker.close()


def test_sender_worker_flushes_queued_publishes():
    broker = _StubBroker()
    try:
        client = mb.MQTTClient(
            "127.0.0.1", broker.port, "test-flush",
            keepalive=300, threading_enabled=True,
        )
        client.connect()
        try:
            for i in range(5):
                client.publish("t/x", '{"n":%d}' % i)
            # wait up to 1s for sender to drain
            deadline = time.time() + 1.0
            while broker.publishes < 5 and time.time() < deadline:
                time.sleep(0.02)
            assert broker.publishes >= 5
        finally:
            client.shutdown()
    finally:
        broker.close()


def test_keepalive_thread_sends_pingreq_while_main_blocked():
    """SC-006: メインが時間を消費しても PINGREQ が独立して送られる."""
    broker = _StubBroker()
    try:
        # keepalive=2 → PINGREQ every 1s
        client = mb.MQTTClient(
            "127.0.0.1", broker.port, "test-ping",
            keepalive=2, threading_enabled=True,
        )
        client.connect()
        try:
            # Simulate main thread stuck for 2.5s; in that time at least one
            # PINGREQ must be sent by the keepalive worker.
            time.sleep(2.5)
            assert broker.pingreqs >= 1, "no PINGREQ seen in 2.5s"
        finally:
            client.shutdown()
    finally:
        broker.close()


def test_send_queue_drops_oldest_when_full():
    """Edge: queue 飽和で FIFO drop + counter increment."""
    # Use a tiny queue and a broker that never reads to force saturation.
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    accepted = []

    def _slow_accept():
        try:
            conn, _ = listener.accept()
            # Send CONNACK but then never read PUBLISH frames.
            try:
                # Drain CONNECT
                conn.recv(4096)
                conn.sendall(b"\x20\x02\x00\x00")
            except Exception:
                pass
            accepted.append(conn)
        except Exception:
            pass

    t = threading.Thread(target=_slow_accept)
    t.daemon = True
    t.start()

    try:
        client = mb.MQTTClient(
            "127.0.0.1", port, "test-drop",
            keepalive=300, threading_enabled=True,
            send_queue_maxsize=3,
        )
        client.connect()
        try:
            # Fire many publishes faster than worker can drain to a broker
            # that won't read. Worker will block on send; queue saturates.
            for i in range(100):
                client.publish("t/x", "x" * 16)
            time.sleep(0.2)
            assert client.publish_dropped_total > 0
        finally:
            client.shutdown()
    finally:
        try:
            listener.close()
        except Exception:
            pass
        for c in accepted:
            try:
                c.close()
            except Exception:
                pass


def test_legacy_mode_still_sends_synchronously():
    """SC-005 fallback: threading_enabled=False は従来パスで動く."""
    broker = _StubBroker()
    try:
        client = mb.MQTTClient(
            "127.0.0.1", broker.port, "test-legacy",
            keepalive=300, threading_enabled=False,
        )
        client.connect()
        client.publish("t/x", "{}")
        deadline = time.time() + 0.5
        while broker.publishes < 1 and time.time() < deadline:
            time.sleep(0.02)
        assert broker.publishes >= 1
        # No worker threads should be alive in legacy mode
        assert getattr(client, "_sender_thread", None) is None
    finally:
        broker.close()
