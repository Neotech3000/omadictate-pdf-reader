"""PDF layout extraction and timed online speech for Omadictate PDF reader."""

from __future__ import annotations

import asyncio
import csv
import io
import re
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import edge_tts
import pymupdf


@dataclass(frozen=True)
class WordBox:
    text: str
    rect: tuple[float, float, float, float]
    block: int = 0
    line: int = 0
    word: int = 0


@dataclass(frozen=True)
class PageData:
    width: float
    height: float
    words: tuple[WordBox, ...]
    text: str
    graphics: tuple[tuple[float, float, float, float], ...] = ()


@dataclass(frozen=True)
class PageAnalysis:
    page: PageData
    graphic_word_indexes: frozenset[int]
    line_number_indexes: frozenset[int]
    graphic_count: int

    @property
    def line_number_count(self) -> int:
        return len(self.line_number_indexes)

    def speech_words(self, skip_graphics: bool, skip_line_numbers: bool) -> tuple[WordBox, ...]:
        skipped = set()
        if skip_graphics:
            skipped.update(self.graphic_word_indexes)
        if skip_line_numbers:
            skipped.update(self.line_number_indexes)
        return tuple(word for index, word in enumerate(self.page.words) if index not in skipped)

    def summary(self) -> str:
        details = []
        if self.graphic_count:
            details.append(f"{self.graphic_count} chart/figure area{'s' if self.graphic_count != 1 else ''}")
        if self.line_number_count:
            details.append("line numbers")
        return ", ".join(details) if details else "regular text layout"


@dataclass(frozen=True)
class TimedHighlight:
    start: float
    end: float
    rects: tuple[tuple[float, float, float, float], ...]
    text: str


def _ocr_words(page: pymupdf.Page) -> list[WordBox]:
    try:
        import shutil

        tesseract = shutil.which("tesseract")
        if not tesseract:
            return []
        scale = 2.0
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
        with tempfile.NamedTemporaryFile(suffix=".png") as image_file:
            image_file.write(pixmap.tobytes("png"))
            image_file.flush()
            result = subprocess.run(
                [tesseract, image_file.name, "stdout", "-l", "eng", "tsv"],
                check=True,
                capture_output=True,
                text=True,
            )
        x_scale = pixmap.width / page.rect.width
        y_scale = pixmap.height / page.rect.height
        words = []
        for row in csv.DictReader(io.StringIO(result.stdout), delimiter="\t"):
            if row.get("level") != "5":
                continue
            text = (row.get("text") or "").strip()
            if not text:
                continue
            x0 = float(row["left"]) / x_scale
            y0 = float(row["top"]) / y_scale
            x1 = (float(row["left"]) + float(row["width"])) / x_scale
            y1 = (float(row["top"]) + float(row["height"])) / y_scale
            words.append(
                WordBox(
                    text,
                    (x0, y0, x1, y1),
                    int(row.get("block_num", 0)),
                    int(row.get("line_num", 0)),
                    int(row.get("word_num", 0)),
                )
            )
        return words
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError):
        return []


def load_document(
    pdf_path: str | Path,
    progress: Callable[[int, int], None] | None = None,
) -> list[PageData]:
    pages = []
    with pymupdf.open(str(pdf_path)) as document:
        for index, page in enumerate(document):
            if progress:
                progress(index + 1, len(document))
            words = [
                WordBox(
                    word[4],
                    tuple(map(float, word[:4])),
                    int(word[5]),
                    int(word[6]),
                    int(word[7]),
                )
                for word in page.get_text("words", sort=True)
                if word[4].strip()
            ]
            if not words:
                words = _ocr_words(page)
            text = " ".join(word.text for word in words)
            pages.append(PageData(float(page.rect.width), float(page.rect.height), tuple(words), text))
    return pages


def _line_groups(words: tuple[WordBox, ...]) -> dict[tuple[int, int], list[int]]:
    groups: dict[tuple[int, int], list[int]] = {}
    for index, word in enumerate(words):
        groups.setdefault((word.block, word.line), []).append(index)
    for indexes in groups.values():
        indexes.sort(key=lambda index: words[index].rect[0])
    return groups


def _graphic_regions(page: pymupdf.Page) -> tuple[tuple[float, float, float, float], ...]:
    page_area = max(page.rect.width * page.rect.height, 1.0)
    regions: list[tuple[float, float, float, float]] = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 1:
            continue
        rect = tuple(map(float, block.get("bbox", (0, 0, 0, 0))))
        image_area = (rect[2] - rect[0]) * (rect[3] - rect[1])
        if page_area * 0.005 <= image_area <= page_area * 0.7:
            regions.append(rect)

    candidates = []
    for drawing in page.get_drawings()[:200]:
        rect = pymupdf.Rect(drawing.get("rect", (0, 0, 0, 0)))
        if rect.is_empty or (rect.width < 3 and rect.height < 3):
            continue
        if rect.width > page.rect.width * 0.95 and rect.height > page.rect.height * 0.95:
            continue
        candidates.append(tuple(map(float, rect)))

    clusters: list[tuple[tuple[float, float, float, float], int]] = []
    for candidate in candidates:
        merged = candidate
        count = 1
        index = 0
        while index < len(clusters):
            other, other_count = clusters[index]
            if not (
                merged[2] + 12 < other[0]
                or other[2] + 12 < merged[0]
                or merged[3] + 12 < other[1]
                or other[3] + 12 < merged[1]
            ):
                merged = (
                    min(merged[0], other[0]),
                    min(merged[1], other[1]),
                    max(merged[2], other[2]),
                    max(merged[3], other[3]),
                )
                count += other_count
                clusters.pop(index)
                index = 0
            else:
                index += 1
        clusters.append((merged, count))

    for rect, count in clusters:
        width, height = rect[2] - rect[0], rect[3] - rect[1]
        area = width * height
        if count >= 5 and width >= 80 and height >= 50 and page_area * 0.025 <= area <= page_area * 0.7:
            regions.append(rect)
    return tuple(regions)


def _line_number_indexes(page: PageData) -> frozenset[int]:
    groups = _line_groups(page.words)
    candidates: dict[tuple[str, int], list[tuple[int, float, int]]] = {}
    number_pattern = re.compile(r"^\d{1,4}$")
    for indexes in groups.values():
        for index in {indexes[0], indexes[-1]}:
            word = page.words[index]
            if not number_pattern.fullmatch(word.text.strip()):
                continue
            x0, _y0, x1, y1 = word.rect
            if x0 <= page.width * 0.08:
                side, position = "left", x0
            elif x1 >= page.width * 0.92:
                side, position = "right", x1
            else:
                continue
            candidates.setdefault((side, round(position / 6)), []).append(
                (index, (word.rect[1] + y1) / 2, int(word.text.strip()))
            )

    min_count = max(3, min(6, round(len(groups) * 0.25)))
    for hits in candidates.values():
        if len(hits) < min_count or len(hits) / max(len(groups), 1) < 0.2:
            continue
        ordered = sorted(hits, key=lambda hit: hit[1])
        values = [hit[2] for hit in ordered]
        rising = sum(right > left for left, right in zip(values, values[1:]))
        if len(values) > 3 and rising / (len(values) - 1) < 0.7:
            continue
        return frozenset(hit[0] for hit in hits)
    return frozenset()


_FIGURE_CAPTION = re.compile(r"^\s*(?:fig(?:ure)?\.?|chart|graph|plot|diagram|exhibit)\b", re.IGNORECASE)


def analyze_page(page: PageData, pdf_path: str | Path, page_index: int) -> PageAnalysis:
    """Classify visual regions and line-number columns without sending page data online."""
    with pymupdf.open(str(pdf_path)) as document:
        regions = _graphic_regions(document[page_index])

    groups = _line_groups(page.words)
    graphic_indexes = set()
    for index, word in enumerate(page.words):
        x0, y0, x1, y1 = word.rect
        word_area = max((x1 - x0) * (y1 - y0), 1.0)
        for gx0, gy0, gx1, gy1 in regions:
            overlap = max(0.0, min(x1, gx1) - max(x0, gx0)) * max(
                0.0, min(y1, gy1) - max(y0, gy0)
            )
            if overlap / word_area >= 0.35:
                graphic_indexes.add(index)
                break

    caption_indexes = set()
    caption_count = 0
    for indexes in groups.values():
        line = " ".join(page.words[index].text for index in indexes)
        if _FIGURE_CAPTION.match(line):
            caption_count += 1
            caption_indexes.update(indexes)

    graphic_indexes.update(caption_indexes)
    graphic_count = max(len(regions), caption_count)
    return PageAnalysis(
        page,
        frozenset(graphic_indexes),
        _line_number_indexes(page),
        graphic_count,
    )


def render_page(pdf_path: str | Path, page_index: int, target_width: int = 920) -> tuple[bytes, float, float]:
    with pymupdf.open(str(pdf_path)) as document:
        page = document[page_index]
        scale = min(1.65, target_width / max(page.rect.width, 1.0))
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
        return (
            pixmap.tobytes("png"),
            pixmap.width / page.rect.width,
            pixmap.height / page.rect.height,
        )


def _tokens(text: str) -> list[str]:
    return [
        token.casefold().replace("’", "'")
        for token in re.findall(r"[^\W_]+(?:['’][^\W_]+)*", text, re.UNICODE)
    ]


class BoundaryMapper:
    """Incrementally match voice timing events to the page's word rectangles."""

    def __init__(self, words: tuple[WordBox, ...]) -> None:
        self.words = words
        self.tokens = [
            (token, word_index)
            for word_index, word in enumerate(words)
            for token in _tokens(word.text)
        ]
        self.cursor = 0

    def add(self, boundary: dict[str, object]) -> TimedHighlight | None:
        spoken = str(boundary.get("text", ""))
        spoken_tokens = _tokens(spoken)
        if not spoken_tokens:
            return None

        match_start = None
        last_start = len(self.tokens) - len(spoken_tokens)
        for start in range(self.cursor, last_start + 1):
            if self.tokens[start][0] != spoken_tokens[0]:
                continue
            if all(
                self.tokens[start + offset][0] == token
                for offset, token in enumerate(spoken_tokens)
            ):
                match_start = start
                break

        if match_start is None:
            for start in range(self.cursor, len(self.tokens)):
                if self.tokens[start][0] == spoken_tokens[0]:
                    match_start = start
                    break
        if match_start is None:
            return None

        span = min(len(spoken_tokens), len(self.tokens) - match_start)
        indexes = sorted({self.tokens[match_start + offset][1] for offset in range(span)})
        self.cursor = match_start + span
        start_ticks = int(boundary.get("offset", 0))
        end_ticks = start_ticks + int(boundary.get("duration", 0))
        return TimedHighlight(
            start_ticks / 10_000_000,
            end_ticks / 10_000_000,
            tuple(self.words[index].rect for index in indexes),
            spoken,
        )


def map_boundaries_to_words(
    words: tuple[WordBox, ...], boundaries: list[dict[str, object]]
) -> tuple[TimedHighlight, ...]:
    mapper = BoundaryMapper(words)
    return tuple(item for boundary in boundaries if (item := mapper.add(boundary)))


async def _stream(
    text: str,
    voice: str,
    rate: str,
    boundary_type: str,
    words: tuple[WordBox, ...],
    cancelled: threading.Event,
    on_audio: Callable[[bytes], None],
    on_highlight: Callable[[TimedHighlight], None],
) -> None:
    communicator = edge_tts.Communicate(
        text,
        voice,
        rate=rate,
        boundary=boundary_type,  # type: ignore[arg-type]
    )
    mapper = BoundaryMapper(words)
    audio_bytes = 0
    stream = communicator.stream()
    try:
        async for chunk in stream:
            if cancelled.is_set():
                return
            if chunk["type"] == "audio":
                data = chunk.get("data", b"")
                if data:
                    on_audio(data)
                    audio_bytes += len(data)
            elif chunk["type"] == boundary_type:
                highlight = mapper.add(chunk)
                if highlight:
                    on_highlight(highlight)
    finally:
        await stream.aclose()
    if not cancelled.is_set() and not audio_bytes:
        raise RuntimeError("The voice service returned no audio.")


def stream_page(
    text: str,
    voice: str,
    rate: str,
    boundary_type: str,
    words: tuple[WordBox, ...],
    cancelled: threading.Event,
    on_audio: Callable[[bytes], None],
    on_highlight: Callable[[TimedHighlight], None],
) -> None:
    asyncio.run(
        _stream(
            text,
            voice,
            rate,
            boundary_type,
            words,
            cancelled,
            on_audio,
            on_highlight,
        )
    )
