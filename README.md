# Netzentrale

Mac- und Windows-App für Fernzugriff und Netzwerkdiagnose – Verbindungsmanager plus Ersatz für das
frühere macOS-„Netzwerkdienstprogramm“.

## Fernzugriff – „Verbindungen“
- Geräteliste mit Gruppen, Suche und Online-Status (grün/rot, alle 30 s)
- Ein Klick verbindet per
  - **VNC** – Client pro Gerät wählbar: **TigerVNC** (Standard), RealVNC Viewer oder Apple Bildschirmfreigabe
  - **SSH** – öffnet Terminal
  - **Web** – öffnet Browser (Home Assistant, Router, DALI-Gateway …)
  - **RDP** – öffnet die Microsoft „Windows App“
- Mehrere Weblinks pro Gerät (z. B. Bordwächter: Oberfläche, Grafana, Logs), Dateizugriff per SMB/SFTP
- Rechtsklick: Links, Passwort kopieren, Ping, Portscan, Überwachen, Wake-on-LAN, Duplizieren
- Geräteliste exportieren/importieren (Ablage-Menü), z. B. zum Teilen im Team

## Überwachung
| Seite | Funktion |
|---|---|
| Überwachung | Ping-, Port- und Webseiten-Ziele laufend prüfen, Mitteilung bei Ausfall/Wiederkehr, Verfügbarkeit in %, Zertifikatsablauf (auch bei selbst signierten Zertifikaten) |
| Inventar | Netzwerkscan als Stand speichern, Stände vergleichen (neu / fehlt / geändert), Geräte als bekannt markieren, CSV-Export für Excel |

## Netzwerk-Werkzeuge
| Seite | Funktion |
|---|---|
| Netzwerkscan | Auswahl aller Netze (WLAN/LAN, VPN, über VPN erreichbare Netze); findet Geräte im Netz (Ping, typische Ports, ARP/MAC, Namen, Raspberry-Pi-Erkennung) – Treffer per Doppelklick als Gerät übernehmen |
| Portscan | IP, Name **oder URL** – zeigt die IP-Adresse(n) dahinter; häufige Ports, Web-Ports, Bereiche oder Listen |
| Sicherheits-Check | offene Dienste und typische Schwachstellen eigener Systeme (Telnet, VNC ohne Passwort, MQTT ohne Anmeldung, alte SSH-Version, HTTP ohne HTTPS, Zertifikate, alte TLS-Versionen, Sicherheits-Header, Modbus/Datenbanken/Docker offen); bewertet strenger bei öffentlichen IPs; Bericht als Text – nur beobachtend, keine Angriffe |
| Ping | Anzahl/Intervall wählbar, auch endlos |
| Traceroute | mit/ohne Namensauflösung |
| Lookup (DNS) | A, AAAA, MX, TXT, NS, CNAME, SOA, PTR, eigener DNS-Server |
| Whois | Domain-/IP-Inhaber |
| Netstat | Routing, Verbindungen, lauschende Ports, Statistiken |
| Info | eigene IP, Router, DNS, WLAN, öffentliche IP, Schnittstellen |
| Bonjour-Suche | Geräte/Dienste per mDNS (Drucker, Macs, Pis, Home Assistant, AirPlay …) |
| WLAN | aktuelles WLAN, Nachbarnetze, Kanalbelegung, Kanal-Empfehlung |
| Speedtest | Apple networkQuality, Verlauf je Netz |
| LAN-Durchsatz | Messung zwischen zwei Geräten (eigene Gegenstelle `throughput.py` oder iperf3) |

## Werkzeuge
| Seite | Funktion |
|---|---|
| DALI-Licht | Leuchten, Gruppen, Broadcast über Lunatone DALI-2 IoT – mehrere Gateways (in „Verbindungen“ mit Dienst „DALI“), Erkennung im Netzwerkscan, Überwachung per /info |
| MQTT | verbinden (auch TLS/Anmeldung), Themen abonnieren, live mitlesen, senden, Funktionstest (Broker nimmt an + liefert aus), Broker im Netz suchen |
| Modbus TCP | Holding/Input-Register, Coils, Eingänge lesen (dez/hex/signed/float), schreiben mit Rückfrage |
| Wake-on-LAN | Rechner per Magic Packet einschalten (MAC am Gerät hinterlegbar) |
| Subnetz-Rechner | Netz, Maske, Broadcast, Hostbereich, Aufteilung in Teilnetze |

## DALI
Der DALI-Code (`netzentrale/dali/`) ist derselbe wie in der eigenständigen App „DALI Control“ für die Haustechnik.

## Passwörter
Werden im **macOS-Schlüsselbund** gespeichert (Eintrag „Netzentrale“), nie in der Geräteliste oder im Export.
Beim Verbinden liegt das Passwort 30 s in der Zwischenablage (⌘V), RustDesk bekommt es direkt übergeben.

## Benötigte Clients (kostenlos)
- TigerVNC Viewer: tigervnc.org (Standard für VNC)
- RealVNC Viewer (optional): realvnc.com
- RustDesk (für Fernhilfe): rustdesk.com
- Windows App (für RDP): Mac App Store

## Windows
Läuft mit demselben Code wie die Mac-Version. Unterschiede:
- RDP öffnet **mstsc** (bei Windows dabei), SSH das **Windows-Terminal** (OpenSSH ist in Windows 10/11 enthalten),
  Dateifreigaben den **Explorer** (`\\server\freigabe`)
- Passwörter liegen in der **Windows-Anmeldeinformationsverwaltung**
- Überwachungs-Meldungen kommen über das Symbol im **Infobereich der Taskleiste**
- Speedtest misst gegen speed.cloudflare.com (macOS nutzt Apples networkQuality)
- WLAN-Übersicht über `netsh wlan`; Windows 11 verlangt dafür die **Standortfreigabe für Desktop-Apps**
- Whois ist eingebaut (Windows hat kein whois-Programm)
- Ping zeigt kein Intervall (Windows-ping kennt keins)

Benötigte Programme (kostenlos): TigerVNC Viewer, optional RealVNC Viewer, RustDesk – jeweils die Windows-Version.

## Bauen – automatisch über GitHub
1. Ordner als (privates) Repository auf GitHub hochladen.
2. Jeder Push auf `main` baut unter **Actions** automatisch:
   - `Netzentrale-Setup-<Version>.exe` – Windows-Installer (ohne Adminrechte, Startmenü, optional Desktop-Symbol)
   - `Netzentrale-Windows-portable-<Version>.zip` – ohne Installation, entpacken und starten
   - `Netzentrale-macOS-<Version>.zip` – eigenständige Mac-App (Apple Silicon), braucht kein Python
3. Für eine Version: `netzentrale/__init__.py` → `__version__` erhöhen, dann Tag setzen
   (`git tag v0.7.0 && git push --tags`) → GitHub legt ein **Release** mit allen Dateien an.

Von Hand: Windows `packaging\build_windows.bat`, macOS `pyinstaller packaging/netzentrale.spec`.
Ohne Code-Signatur zeigen Windows (SmartScreen „Unbekannter Herausgeber“ → „Weitere Informationen“ →
„Trotzdem ausführen“) und macOS (Rechtsklick → Öffnen) beim ersten Start eine Warnung.

## Entwicklung
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m netzentrale
python3 tools/build_app.py dist/      # baut Netzentrale.app
```
Daten: `~/Library/Application Support/Netzentrale/geraete.json`
