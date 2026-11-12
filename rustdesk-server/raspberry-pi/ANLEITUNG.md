# RustDesk-Server auf dem Raspberry Pi (Testbetrieb)

Geeignet: Raspberry Pi 3B+, 4 oder 5 · SD-Karte ab 8 GB · Netzwerkkabel empfohlen

## 1. SD-Karte vorbereiten (am Mac)
1. **Raspberry Pi Imager** öffnen (raspberrypi.com/software).
2. Gerät: dein Pi-Modell · Betriebssystem: **Raspberry Pi OS (other) → Raspberry Pi OS Lite (64-bit)**.
3. Bei „OS-Anpassungen“ einstellen:
   - Hostname: `fernhilfe`
   - Benutzer + Passwort festlegen
   - WLAN nur, falls kein Kabel
   - Reiter „Dienste“: **SSH aktivieren**
4. Schreiben lassen.
5. Karte danach **nicht auswerfen**: im Finder das Laufwerk **bootfs** öffnen und
   `install-rustdesk-server.sh` hineinkopieren. Dann auswerfen.

## 2. Pi starten und Server installieren
1. Karte in den Pi, Netzwerk und Strom anschließen, ca. 2 Minuten warten.
2. Am Mac im Terminal (oder in der Netzentrale per SSH):
   ```
   ssh <benutzer>@fernhilfe.local
   sudo bash /boot/firmware/install-rustdesk-server.sh
   ```
3. Am Ende zeigt das Skript **Adresse** und **Schlüssel** an. Später jederzeit wieder mit `rustdesk-info`.

## 3. Testen im eigenen Netz
1. Netzentrale → **Fernhilfe** → Eigener RustDesk-Server: Adresse (`fernhilfe.local` oder IP) und Schlüssel
   eintragen → **Server prüfen** → drei grüne Haken.
2. RustDesk auf **beiden** Test-Rechnern: Einstellungen → Netzwerk → ID/Relay-Server
   - ID-Server: Adresse des Pi
   - Relay-Server: Adresse des Pi
   - Key: Schlüssel
3. Auf dem einen Rechner ID + Passwort ablesen, in der Netzentrale verbinden.

## 4. Test über das Internet (optional)
- Der Pi braucht eine feste IP im Netz (im Router „immer dieselbe IP zuweisen“).
- Im Router an den Pi weiterleiten: **TCP 21115, TCP+UDP 21116, TCP 21117**.
- Von außen ist der Server dann über die öffentliche IP bzw. einen DynDNS-Namen erreichbar
  (z. B. MyFRITZ!-Adresse). Diese Adresse statt `fernhilfe.local` eintragen.
- Nur für den Test – für den Dauerbetrieb gehört der Server ins Rechenzentrum der Stadtwerke.

## Wichtig
- Ordner `/var/lib/rustdesk-server/` enthält den Schlüssel. Beim Umzug auf den Stadtwerke-Server
  diesen Ordner mitnehmen, dann müssen die Clients nur die Adresse ändern.
- Updates: Skript einfach erneut ausführen.
