#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${HOME}/.local/share/omadictate-pdf-reader"
BIN_FILE="${HOME}/.local/bin/omadictate-pdf-reader"
DESKTOP_FILE="${HOME}/.local/share/applications/io.github.neotech3000.omadictatepdfreader.desktop"
OLD_DESKTOP_FILE="${HOME}/.local/share/applications/omadictate-pdf-reader.desktop"

rm -f -- "$BIN_FILE" "$DESKTOP_FILE" "$OLD_DESKTOP_FILE"
if [[ -d "$APP_DIR" && "$APP_DIR" == "$HOME/.local/share/omadictate-pdf-reader" ]]; then
  rm -rf -- "$APP_DIR"
fi

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true
fi

echo "Removed the app files. Remove the Omarchy bar plugin separately with: omarchy plugin remove neotech3000.omadictate-pdf-reader"
