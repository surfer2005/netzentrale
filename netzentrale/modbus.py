"""Minimaler Modbus-TCP-Client (nur Standardbibliothek)."""
from __future__ import annotations

import socket
import struct

FUNCTIONS = {
    3: "Holding Register (FC3)",
    4: "Input Register (FC4)",
    1: "Coils (FC1)",
    2: "Diskrete Eingänge (FC2)",
}
EXCEPTIONS = {
    1: "Funktion wird nicht unterstützt", 2: "Adresse ungültig", 3: "Wert ungültig",
    4: "Gerätefehler", 5: "Bestätigt, Bearbeitung läuft", 6: "Gerät beschäftigt",
    10: "Gateway: Pfad nicht verfügbar", 11: "Gateway: Zielgerät antwortet nicht",
}


class ModbusError(Exception):
    pass


class ModbusClient:
    def __init__(self, host: str, port: int = 502, unit: int = 1, timeout: float = 3.0):
        self.host, self.port, self.unit, self.timeout = host, port, unit, timeout
        self._tid = 0

    def _request(self, pdu: bytes) -> bytes:
        self._tid = (self._tid + 1) & 0xFFFF
        frame = struct.pack(">HHHB", self._tid, 0, len(pdu) + 1, self.unit) + pdu
        with socket.create_connection((self.host, self.port), timeout=self.timeout) as s:
            s.sendall(frame)
            head = self._recv(s, 7)
            tid, proto, length, unit = struct.unpack(">HHHB", head)
            body = self._recv(s, length - 1)
        if body[0] & 0x80:
            code = body[1] if len(body) > 1 else 0
            raise ModbusError(f"Modbus-Fehler {code}: {EXCEPTIONS.get(code, 'unbekannt')}")
        return body

    @staticmethod
    def _recv(s, n) -> bytes:
        buf = b""
        while len(buf) < n:
            part = s.recv(n - len(buf))
            if not part:
                raise ModbusError("Verbindung vom Gerät geschlossen")
            buf += part
        return buf

    def read(self, fc: int, address: int, count: int) -> list[int]:
        if fc not in FUNCTIONS:
            raise ValueError("Funktion nicht unterstützt")
        maxc = 125 if fc in (3, 4) else 2000
        if not 1 <= count <= maxc:
            raise ValueError(f"Anzahl 1–{maxc}")
        body = self._request(struct.pack(">BHH", fc, address, count))
        data = body[2:2 + body[1]]
        if fc in (3, 4):
            return list(struct.unpack(f">{len(data) // 2}H", data))[:count]
        bits = []
        for byte in data:
            bits.extend((byte >> i) & 1 for i in range(8))
        return bits[:count]

    def write_register(self, address: int, value: int) -> None:
        self._request(struct.pack(">BHH", 6, address, value & 0xFFFF))

    def write_coil(self, address: int, on: bool) -> None:
        self._request(struct.pack(">BHH", 5, address, 0xFF00 if on else 0))


def as_float32(regs: list[int], i: int, swap: bool = False) -> float | None:
    if i + 1 >= len(regs):
        return None
    a, b = regs[i], regs[i + 1]
    if swap:
        a, b = b, a
    return struct.unpack(">f", struct.pack(">HH", a, b))[0]
