#!/bin/bash
# =============================================================================
#  RustDesk-Server (Open Source) – Installation für Raspberry Pi OS / Debian
#  Installiert hbbs (ID-Server) und hbbr (Relay) als Systemdienste.
#
#  Aufruf auf dem Pi:   sudo bash install-rustdesk-server.sh
#  (liegt das Skript auf der SD-Karte:  sudo bash /boot/firmware/install-rustdesk-server.sh)
# =============================================================================
set -euo pipefail

FALLBACK_VERSION="1.1.16"
REPO="rustdesk/rustdesk-server"
DATA_DIR="/var/lib/rustdesk-server"

green() { printf '\033[1;32m%s\033[0m\n' "$*"; }
red()   { printf '\033[1;31m%s\033[0m\n' "$*"; }
bold()  { printf '\033[1m%s\033[0m\n' "$*"; }

if [ "$(id -u)" -ne 0 ]; then
  red "Bitte mit sudo starten:  sudo bash $0"
  exit 1
fi

ARCH="${ARCH:-$(dpkg --print-architecture)}"
case "$ARCH" in
  arm64|armhf|amd64|i386) ;;
  *) red "Architektur '$ARCH' wird nicht unterstützt."; exit 1 ;;
esac

bold "1/4  Prüfe Internetverbindung …"
if ! curl -sS --max-time 10 -o /dev/null https://github.com; then
  red "github.com ist nicht erreichbar – bitte Netzwerk/Internet am Pi prüfen."
  exit 1
fi

bold "2/4  Ermittle aktuelle Version …"
VERSION="$(curl -fsS --max-time 10 "https://api.github.com/repos/$REPO/releases/latest" 2>/dev/null \
           | grep -m1 '"tag_name"' | sed -E 's/.*"tag_name": *"v?([^"]+)".*/\1/' || true)"
VERSION="${VERSION:-$FALLBACK_VERSION}"
echo "     Version $VERSION, Architektur $ARCH"

bold "3/4  Lade und installiere Pakete …"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
for part in hbbs hbbr; do
  url="https://github.com/$REPO/releases/download/$VERSION/rustdesk-server-${part}_${VERSION}_${ARCH}.deb"
  echo "     $url"
  if ! curl -fsSL --max-time 120 -o "$TMP/$part.deb" "$url"; then
    red "Download fehlgeschlagen: $url"
    exit 1
  fi
done
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$TMP/hbbs.deb" "$TMP/hbbr.deb" >/dev/null

systemctl enable --now rustdesk-hbbr.service rustdesk-hbbs.service >/dev/null 2>&1 || true

# Hilfsbefehl für später: zeigt Schlüssel, Adresse und Dienststatus
cat > /usr/local/bin/rustdesk-info <<'EOF'
#!/bin/bash
IP="$(hostname -I | awk '{print $1}')"
echo
echo "RustDesk-Server auf $(hostname) ($IP)"
echo "-------------------------------------------"
for s in rustdesk-hbbs rustdesk-hbbr; do
  printf '%-15s %s\n' "$s" "$(systemctl is-active $s 2>/dev/null)"
done
echo
echo "Server-Adresse (im LAN):  $IP   oder   $(hostname).local"
echo "Schlüssel (Key):          $(cat /var/lib/rustdesk-server/id_ed25519.pub 2>/dev/null || echo 'noch nicht erzeugt')"
echo
echo "Ports: TCP 21115, TCP+UDP 21116, TCP 21117  (für Internet-Zugriff im Router freigeben)"
echo "Logs:  /var/log/rustdesk-server/"
echo
EOF
chmod 755 /usr/local/bin/rustdesk-info

bold "4/4  Warte auf Schlüsselerzeugung …"
for _ in $(seq 1 20); do
  [ -s "$DATA_DIR/id_ed25519.pub" ] && break
  sleep 1
done

echo
if [ -s "$DATA_DIR/id_ed25519.pub" ]; then
  green "Fertig – der RustDesk-Server läuft."
else
  red "Dienste installiert, aber noch kein Schlüssel vorhanden. Status prüfen mit:  systemctl status rustdesk-hbbs"
fi
/usr/local/bin/rustdesk-info
bold "Nächste Schritte:"
echo " 1. In der Netzentrale → Fernhilfe → „Eigener RustDesk-Server“ Adresse und Schlüssel eintragen,"
echo "    dann „Server prüfen“ (drei grüne Haken)."
echo " 2. In RustDesk auf beiden Rechnern: Einstellungen → Netzwerk → ID/Relay-Server:"
echo "    ID-Server und Relay-Server = Adresse oben, Key = Schlüssel oben."
echo " 3. Für Fernhilfe über das Internet im Router die Ports oben an diesen Pi weiterleiten."
echo
echo "Diese Übersicht später wieder anzeigen:  rustdesk-info"
