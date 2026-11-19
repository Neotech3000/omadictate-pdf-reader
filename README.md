# Omadictate PDF reader

An Omarchy bar launcher and GTK 4 PDF reader. It offers ten Microsoft Edge neural voices, live word/sentence highlighting, chart/figure and line-number skip filters, Flea file picking, and drag-and-drop.

## Install on Omarchy

The plugin repository contains the launcher; the separate installer sets up the desktop app and its Python environment. The plugin manager does **not** run install scripts.

```bash
omarchy plugin add https://github.com/Neotech3000/omadictate-pdf-reader.git
cd ~/.config/omarchy/plugins/neotech3000.omadictate-pdf-reader
./install.sh
omarchy plugin enable neotech3000.omadictate-pdf-reader
```

Click **PDF** in the Omarchy bar, or search for **Omadictate PDF reader** in the app launcher. The app installer is user-local and does not use `sudo`.

### Dependencies

Omarchy supplies GTK 4, PyGObject, and GStreamer. MP3 playback needs the GStreamer good plugins. Install `tesseract` for OCR on scanned PDFs; without it, text-based PDFs still work. Python package dependencies are installed into a private virtual environment.

## Features

- Ten curated US and UK neural voices in a dropdown.
- Audio streams to playback as it is generated, with pause/resume.
- Embedded PDF viewer with word or sentence highlights and five highlight colors.
- Background layout analysis of the next page while the current page is being read. It waits until narration starts and runs at reduced CPU priority to help avoid audio underruns.
- Optional **Charts / figures** and **Line numbers** skip filters. Ordinary numbered lists are intended to remain intact.
- Uses the active Omarchy theme and the system GTK font settings.

Layout analysis is local. It looks for image/vector regions and figure captions, plus numeric columns aligned at the page margin. It is heuristic: unusual layouts may not be classified correctly. Skipping charts removes detected chart-area text and captions; it cannot interpret or summarize a graph.

## Privacy

Layout detection and PDF rendering happen locally. For online neural speech, the text of the page being read is sent to Microsoft's Edge text-to-speech service through the community `edge-tts` client. Internet access is required; no API key is needed. The service and client are not an official Microsoft API integration.

## Remove

```bash
cd ~/.config/omarchy/plugins/neotech3000.omadictate-pdf-reader
./uninstall.sh
omarchy plugin remove neotech3000.omadictate-pdf-reader
```

## Development

Validate the Omarchy manifest with:

```bash
omarchy plugin validate .
```

## License

AGPL-3.0-or-later. PyMuPDF is distributed under its AGPL-3.0 or commercial license; `edge-tts` is a separate GPL-licensed dependency. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
