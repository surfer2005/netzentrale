"""Internet-Speedtest ohne Zusatzprogramme (Windows/Linux; auf dem Mac nutzt die App networkQuality).

Misst gegen speed.cloudflare.com: Latenz (Median kleiner Abrufe), Download und Upload über
mehrere parallele Verbindungen. Gibt Zeilen im selben Format wie networkQuality aus, damit die
Auswertung der Seite für beide Wege gleich ist.
"""
from __future__ import annotations

import statistics
import threading
import time
import urllib.request

BASE = "https://speed.cloudflare.com"
PARALLEL = 4
DURATION = 8.0     # Sekunden je Richtung
CHUNK_DOWN = 25_000_000
CHUNK_UP = 1_000_000   # klein genug, damit auch langsame Uplinks zählbar sind


def _latency(base: str, emit) -> float | None:
    times = []
    for _ in range(8):
        t = time.perf_counter()
        try:
            with urllib.request.urlopen(f"{base}/__down?bytes=0", timeout=5) as r:
                r.read()
            times.append((time.perf_counter() - t) * 1000)
        except OSError:
            pass
    return statistics.median(times) if times else None


def _run_parallel(worker, emit, label) -> float:
    total = [0]
    lock = threading.Lock()
    stop = time.perf_counter() + DURATION

    def add(n):
        with lock:
            total[0] += n

    threads = [threading.Thread(target=worker, args=(stop, add), daemon=True) for _ in range(PARALLEL)]
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    last = 0.0
    while any(t.is_alive() for t in threads):
        time.sleep(0.25)
        el = time.perf_counter() - t0
        if el - last >= 1.0:
            last = el
            emit(f"  {label}: {total[0] * 8 / el / 1e6:.1f} Mbit/s …\n")
    return total[0] * 8 / max(time.perf_counter() - t0, 1e-6) / 1e6


def run(emit=print, base: str = BASE) -> None:
    emit(f"Speedtest gegen {base.split('://')[-1]} ({PARALLEL} Verbindungen, je {DURATION:.0f} s)\n\n")
    lat = _latency(base, emit)
    if lat is None:
        emit("Messserver nicht erreichbar – Internetverbindung oder Firewall/Proxy prüfen.\n")
        return
    emit(f"Latenz: {lat:.0f} ms\n")

    def down_worker(stop, add):
        while time.perf_counter() < stop:
            try:
                with urllib.request.urlopen(f"{base}/__down?bytes={CHUNK_DOWN}", timeout=15) as r:
                    while time.perf_counter() < stop:
                        b = r.read(65536)
                        if not b:
                            break
                        add(len(b))
            except OSError:
                time.sleep(0.2)

    payload = b"0" * CHUNK_UP

    def up_worker(stop, add):
        while time.perf_counter() < stop:
            req = urllib.request.Request(f"{base}/__up", data=payload, method="POST",
                                         headers={"Content-Type": "application/octet-stream"})
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    r.read()
                add(len(payload))
            except OSError:
                time.sleep(0.2)

    down = _run_parallel(down_worker, emit, "Download")
    up = _run_parallel(up_worker, emit, "Upload")
    emit("\n==== SUMMARY ====\n")
    emit(f"Uplink capacity: {up:.3f} Mbps\n")
    emit(f"Downlink capacity: {down:.3f} Mbps\n")
    emit(f"Idle Latency: {lat:.3f} milliseconds\n")
