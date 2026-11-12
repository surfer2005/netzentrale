#!/usr/bin/env python3
"""LAN-Durchsatztest der Netzentrale – Gegenstelle und Client.

Gegenstelle auf einem anderen Gerät (z. B. Raspberry Pi) starten:
    python3 throughput.py              (lauscht auf TCP 5210)
Kommt ohne Zusatzpakete aus (nur Python 3).

Protokoll: Client sendet "NZT1 UP <s>\\n" oder "NZT1 DOWN <s>\\n".
UP:   Client sendet <s> Sekunden Daten, schließt Schreibrichtung;
      Gegenstelle antwortet "OK <bytes> <sekunden>\\n".
DOWN: Gegenstelle sendet <s> Sekunden Daten und schließt.
"""
from __future__ import annotations

import socket
import sys
import threading
import time

PORT = 5210
CHUNK = 256 * 1024
_BLOCK = b"\x00" * CHUNK


def _handle(conn: socket.socket, addr):
    with conn:
        f = conn.makefile("rb")
        head = f.readline(64).decode(errors="replace").split()
        if len(head) != 3 or head[0] != "NZT1":
            return
        mode, secs = head[1], min(60.0, float(head[2]))
        if mode == "UP":
            total, t0 = 0, None
            while True:
                data = f.read1(CHUNK) if hasattr(f, "read1") else conn.recv(CHUNK)
                if not data:
                    break
                if t0 is None:
                    t0 = time.perf_counter()
                total += len(data)
            dur = time.perf_counter() - (t0 or time.perf_counter())
            conn.sendall(f"OK {total} {dur:.4f}\n".encode())
            print(f"{addr[0]}: Upload {total * 8 / max(dur, 1e-6) / 1e6:.1f} Mbit/s")
        elif mode == "DOWN":
            end = time.perf_counter() + secs
            total = 0
            while time.perf_counter() < end:
                conn.sendall(_BLOCK)
                total += CHUNK
            print(f"{addr[0]}: Download {total / 1e6:.0f} MB in {secs:.0f} s gesendet")


def serve(port: int = PORT, stop_event: threading.Event | None = None, ready=None):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("", port))
    srv.listen(5)
    srv.settimeout(0.5)
    if ready:
        ready()
    try:
        while not (stop_event and stop_event.is_set()):
            try:
                conn, addr = srv.accept()
            except socket.timeout:
                continue
            conn.settimeout(None)
            threading.Thread(target=_handle, args=(conn, addr), daemon=True).start()
    finally:
        srv.close()


def run_test(host: str, mode: str, secs: float, port: int = PORT, progress=None) -> float:
    """Führt UP oder DOWN aus. Ergebnis in Mbit/s. progress(mbit_s) wird laufend aufgerufen."""
    with socket.create_connection((host, port), timeout=5) as s:
        s.settimeout(secs + 15)
        s.sendall(f"NZT1 {mode} {secs}\n".encode())
        if mode == "UP":
            end = time.perf_counter() + secs
            t0 = last = time.perf_counter()
            sent = 0
            while (now := time.perf_counter()) < end:
                s.sendall(_BLOCK)
                sent += CHUNK
                if progress and now - last > 0.5:
                    progress(sent * 8 / (now - t0) / 1e6)
                    last = now
            s.shutdown(socket.SHUT_WR)
            reply = s.makefile("rb").readline().decode().split()
            if len(reply) == 3 and reply[0] == "OK":
                return int(reply[1]) * 8 / max(float(reply[2]), 1e-6) / 1e6
            raise OSError("Gegenstelle hat das Ergebnis nicht gemeldet")
        total, t0, last = 0, None, time.perf_counter()
        while True:
            data = s.recv(CHUNK)
            if not data:
                break
            now = time.perf_counter()
            if t0 is None:
                t0 = now
            total += len(data)
            if progress and now - last > 0.5:
                progress(total * 8 / (now - t0) / 1e6)
                last = now
        return total * 8 / max(time.perf_counter() - (t0 or 0), 1e-6) / 1e6


if __name__ == "__main__":
    p = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    print(f"Netzentrale-Durchsatz-Gegenstelle lauscht auf TCP {p} (Strg+C beendet)")
    try:
        serve(p)
    except KeyboardInterrupt:
        pass
