#!/usr/bin/env bash
# Halo installer for Raspberry Pi OS Bookworm (64-bit) on a Raspberry Pi 5.
set -euo pipefail
cd "$(dirname "$0")/.."
HALO_DIR="$(pwd)"

echo "==> System packages"
sudo apt-get update
sudo apt-get install -y python3-venv python3-dev python3-lgpio python3-pygame avahi-daemon

echo "==> Enable SPI (LED ring) and allow full USB current from the 5 A supply"
sudo raspi-config nonint do_spi 0
CFG=/boot/firmware/config.txt
grep -q '^usb_max_current_enable=1' "$CFG" || echo 'usb_max_current_enable=1' | sudo tee -a "$CFG"

echo "==> Hostname halo (so http://halo.local:8080 works)"
sudo raspi-config nonint do_hostname halo

echo "==> Python environment"
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt

[ -f .env ] || cp .env.example .env
[ -f config.toml ] || cp config.example.toml config.toml
[ -f bottles.json ] || cp scripts/sample_bottles.json bottles.json
[ -f recipes.json ] || cp scripts/sample_recipes.json recipes.json

echo "==> Autostart"
sed "s#__HALO_DIR__#$HALO_DIR#g; s#__USER__#$USER#g" scripts/halo.service | sudo tee /etc/systemd/system/halo.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable halo

echo
echo "Done. Next: put your Anthropic key in $HALO_DIR/.env, then reboot."
echo "After the reboot: .venv/bin/python -m halo.calibrate scale"
