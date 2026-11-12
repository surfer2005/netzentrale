# Eigener RustDesk-Server

## Voraussetzungen
- Linux-VM (z. B. Debian/Ubuntu) mit Docker – 1 vCPU, 1 GB RAM, ~20 GB Platte reichen
- Feste öffentliche IP oder DNS-Name (z. B. fernhilfe.stadtwerke-konstanz.de)
- Firewall/Portfreigabe aus dem Internet auf die VM:
  - TCP 21115 (NAT-Test), TCP+UDP 21116 (ID-Server), TCP 21117 (Relay)
  - optional TCP 21118/21119 (Web-Client über WebSocket)

## Einrichtung
1. `docker-compose.yml` auf die VM kopieren, DNS-Namen eintragen
2. `docker compose up -d`
3. `cat data/id_ed25519.pub` → Schlüssel in Netzentrale (Fernhilfe → Eigener Server) eintragen
4. „Server prüfen“ in Netzentrale muss drei grüne Haken zeigen
5. Im eigenen RustDesk (Einstellungen → Netzwerk) dieselben Werte eintragen

Den Ordner `data/` sichern – ohne den privaten Schlüssel müssen alle Clients neu konfiguriert werden.
