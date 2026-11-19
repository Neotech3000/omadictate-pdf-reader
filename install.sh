#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${HOME}/.local/share/omadictate-pdf-reader"
BIN_DIR="${HOME}/.local/bin"
DESKTOP_DIR="${HOME}/.local/share/applications"
DESKTOP_ID="io.github.neotech3000.omadictatepdfreader"
DESKTOP_FILE="$DESKTOP_DIR/$DESKTOP_ID.desktop"
OLD_DESKTOP_FILE="$DESKTOP_DIR/omadictate-pdf-reader.desktop"
PYTHON="${PYTHON:-/usr/bin/python3}"
VENV_DIR="${OMADICTATE_VENV:-$APP_DIR/venv}"

if [[ ! -x "$PYTHON" ]]; then
  echo "Python not found at $PYTHON. Set PYTHON to the system Python that provides PyGObject." >&2
  exit 1
fi

"$PYTHON" - <<'PY'
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gst", "1.0")
from gi.repository import Gst
Gst.init(None)
if Gst.ElementFactory.find("playbin") is None or Gst.ElementFactory.find("mpg123audiodec") is None:
    raise SystemExit("GStreamer MP3 playback plugins are missing; install gst-plugins-good.")
PY

mkdir -p "$APP_DIR" "$BIN_DIR" "$DESKTOP_DIR"
install -m 644 "$ROOT_DIR/app/pdf-reader-app.py" "$APP_DIR/pdf-reader-app.py"
install -m 644 "$ROOT_DIR/app/pdf_read_aloud_engine.py" "$APP_DIR/pdf_read_aloud_engine.py"
install -m 644 "$ROOT_DIR/app/requirements.txt" "$APP_DIR/requirements.txt"
install -m 644 "$ROOT_DIR/assets/omadictate-pdf-reader.png" "$APP_DIR/app-icon.png"

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  "$PYTHON" -m venv --system-site-packages "$VENV_DIR"
fi
"$VENV_DIR/bin/python" -m pip install --disable-pip-version-check -r "$APP_DIR/requirements.txt"

cat >"$BIN_DIR/omadictate-pdf-reader" <<EOF
#!/usr/bin/env bash
exec "$VENV_DIR/bin/python" "$APP_DIR/pdf-reader-app.py" "\$@"
EOF
chmod 755 "$BIN_DIR/omadictate-pdf-reader"

cat >"$DESKTOP_DIR/omadictate-pdf-reader.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Omadictate PDF reader
Comment=Read PDFs aloud with synchronized highlights
Exec=$BIN_DIR/omadictate-pdf-reader
TryExec=$BIN_DIR/omadictate-pdf-reader
Icon=$APP_DIR/app-icon.png
Terminal=false
Categories=Office;
Keywords=PDF;read aloud;speech;accessibility;
StartupNotify=true
StartupWMClass=$DESKTOP_ID
EOF

mv -f -- "$DESKTOP_DIR/omadictate-pdf-reader.desktop" "$DESKTOP_FILE"
rm -f -- "$OLD_DESKTOP_FILE"

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
fi

echo "Installed Omadictate PDF reader. Search for it in the app launcher, or click the PDF bar widget."
