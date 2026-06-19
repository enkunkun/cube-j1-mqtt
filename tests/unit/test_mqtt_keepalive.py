"""MQTT CONNECT packet must carry the configured keep-alive value.

Cube J1 のメインループが ECHONET Lite poll で詰まると 60s keepalive を踏み
越えて broker 側から切断される（実測 約 12 分間隔）。keepalive を config
から渡せるようにして、デフォルトを 300s に引き上げる。
"""
import socket
import struct
import threading

import mqtt_bridge as mb


class _StubBroker(object):
    """Accept a single CONNECT, capture it, then reply with CONNACK ok."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.received = b""
        self._thread = threading.Thread(target=self._serve)
        self._thread.daemon = True
        self._thread.start()

    def _serve(self):
        conn, _ = self.sock.accept()
        try:
            while len(self.received) < 64:
                chunk = conn.recv(64)
                if not chunk:
                    break
                self.received += chunk
                if len(self.received) >= 16:
                    break
            conn.sendall(b"\x20\x02\x00\x00")
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


def _keepalive_from_connect(pkt):
    # Fixed header: 0x10 + remaining length (1 byte for small packets)
    # Variable header: 2-byte proto name length + "MQTT" + protocol level (1) + flags (1) + keepalive (2 bytes BE)
    idx = pkt.index(b"MQTT")
    return struct.unpack(">H", pkt[idx + 4 + 1 + 1:idx + 4 + 1 + 1 + 2])[0]


def test_connect_sends_default_keepalive_60_when_unspecified():
    broker = _StubBroker()
    try:
        client = mb.MQTTClient("127.0.0.1", broker.port, "test-default")
        client.connect()
    finally:
        broker.close()
    assert _keepalive_from_connect(broker.received) == 60


def test_connect_sends_custom_keepalive_when_passed():
    broker = _StubBroker()
    try:
        client = mb.MQTTClient(
            "127.0.0.1", broker.port, "test-custom", keepalive=300
        )
        client.connect()
    finally:
        broker.close()
    assert _keepalive_from_connect(broker.received) == 300
