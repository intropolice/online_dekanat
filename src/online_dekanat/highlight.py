from __future__ import annotations

from pathlib import Path
from dataclasses import replace
from difflib import SequenceMatcher
import re
from uuid import uuid4
from datetime import datetime

from PIL import Image, ImageDraw
from PIL import ImageOps

from online_dekanat.ocr.base import OCRRegion
from online_dekanat.io import load_pages
from online_dekanat.models import NOT_FOUND, DocumentCard


def create_highlights(
    source: str | Path,
    regions: list[OCRRegion],
    card: DocumentCard,
    output_root: str | Path = "data/highlights",
) -> list[str]:
    if Path(source).suffix.lower() == ".txt":
        return []
    pages = load_pages(source)
    relevant = _regions_for_card(regions, card)
    visual = _visual_regions(pages, regions, card, include_empty_signature_slot=True)
    relevant.extend(visual)
    if not relevant:
        return []
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    output: list[str] = []
    for page_index, page in enumerate(pages):
        canvas = page.convert("RGB")
        draw = ImageDraw.Draw(canvas, "RGBA")
        regions_on_page = [region for region in relevant if region.page == page_index]
        for region in regions_on_page:
            x1, y1, x2, y2 = region.bbox
            draw.rectangle((x1, y1, x2, y2), outline=(242, 154, 42, 235), width=max(2, canvas.width // 500), fill=(255, 211, 95, 45))
        if not regions_on_page:
            continue
        path = root / f"{uuid4().hex}-page-{page_index + 1}.png"
        canvas.save(path, format="PNG", optimize=True)
        output.append(str(path))
    return output


def apply_visual_presence(source: str | Path, regions: list[OCRRegion], card: DocumentCard) -> None:
    """Set signature and seal presence from visible marks for image documents.

    OCR sees printed labels such as "Подпись сотрудника" even when the line is
    empty, so text presence alone is not evidence that a signature was applied.
    """
    if Path(source).suffix.lower() == ".txt":
        return
    pages = load_pages(source)
    detected = {region.text for region in _visual_regions(pages, regions, card)}
    for key in ("student_signature", "signature", "signature_and_seal", "seal"):
        field = card.fields.get(key)
        if field is None:
            continue
        role = "seal" if key in {"signature_and_seal", "seal"} else "signature"
        field.value = "есть" if role in detected else NOT_FOUND


def _regions_for_card(regions: list[OCRRegion], card: DocumentCard) -> list[OCRRegion]:
    selected: dict[int, OCRRegion] = {}
    for field in card.fields.values():
        if not field.is_found:
            continue
        # Presence fields are visual objects. The words "Подпись" and
        # "Печать" are labels, not the evidence used by the card.
        if field.key in {"signature_and_seal", "student_signature", "signature", "seal"}:
            continue
        value = field.value
        queries: list[list[str]] = []
        if value.casefold() == "есть":
            queries = [[token] for token in _tokens(field.title) if token not in {"и", "с", "о"}]
        else:
            tokens = _tokens(value)
            if tokens:
                queries = [tokens]
                date_tokens = _date_word_tokens(value)
                if date_tokens:
                    queries.append(date_tokens)

        label_tokens = [token for token in _tokens(field.title) if token not in {"и", "с", "о"}]
        for page in sorted({region.page for region in regions}):
            page_regions = [region for region in regions if region.page == page]
            stream: list[tuple[str, int, int, int]] = []
            for region_index, region in enumerate(page_regions):
                for match in re.finditer(r"[a-zа-яё0-9]+", region.text.casefold()):
                    stream.append((match.group(), region_index, match.start(), match.end()))
            hits: list[tuple[tuple[float, int, float], dict[int, list[tuple[int, int]]]]] = []
            for query in queries:
                width = len(query)
                for start in range(0, len(stream) - width + 1):
                    matched = stream[start : start + width]
                    if [item[0] for item in matched] != query:
                        continue
                    if query and query[-1].isdigit() and start + width < len(stream) and stream[start + width][0] in {"год", "года", "году"}:
                        matched = [*matched, stream[start + width]]
                    grouped: dict[int, list[tuple[int, int]]] = {}
                    for _, region_index, char_start, char_end in matched:
                        grouped.setdefault(region_index, []).append((char_start, char_end))
                    matched_bbox = _regions_bbox(page_regions, grouped)
                    anchors = [
                        (index, token)
                        for index, region in enumerate(page_regions)
                        for token in label_tokens
                        if token in _tokens(region.text)
                    ]
                    if anchors:
                        anchor_scores = []
                        for anchor_index, _ in anchors:
                            anchor = page_regions[anchor_index].bbox
                            vertical_gap = max(0.0, max(anchor[1] - matched_bbox[3], matched_bbox[1] - anchor[3]))
                            label_is_right = anchor[0] >= matched_bbox[2]
                            horizontal_gap = abs(anchor[0] - matched_bbox[2]) if label_is_right else 0
                            anchor_scores.append((vertical_gap, int(label_is_right), horizontal_gap))
                        score = min(anchor_scores)
                    else:
                        score = (float("inf"), 0, 0)
                    hits.append((score, grouped))
            if hits:
                _, grouped = min(hits, key=lambda hit: hit[0])
                for region_index, spans in grouped.items():
                    region = page_regions[region_index]
                    char_start = min(span[0] for span in spans)
                    char_end = max(span[1] for span in spans)
                    refined = _refine_bbox(region, char_start, char_end)
                    selected[id(region)] = replace(region, bbox=refined)
    return list(selected.values())


def _regions_bbox(regions: list[OCRRegion], grouped: dict[int, list[tuple[int, int]]]) -> tuple[float, float, float, float]:
    boxes = [regions[index].bbox for index in grouped]
    return min(box[0] for box in boxes), min(box[1] for box in boxes), max(box[2] for box in boxes), max(box[3] for box in boxes)


def _visual_regions(
    pages: list[Image.Image],
    regions: list[OCRRegion],
    card: DocumentCard,
    include_empty_signature_slot: bool = False,
) -> list[OCRRegion]:
    """Find ink/stamp pixels near OCR labels for signature and seal fields."""
    wanted: list[str] = []
    for field in card.fields.values():
        if field.key in {"student_signature", "signature"}:
            wanted.append("signature")
        elif field.key in {"signature_and_seal", "seal"}:
            wanted.append("seal")
    output: list[OCRRegion] = []
    for page_index, page in enumerate(pages):
        page_regions = [region for region in regions if region.page == page_index]
        nearby_stamp = _find_colored_stamp_bbox(page) if "signature" in wanted else None
        for role in wanted:
            # Prefer the stamp's colored ink over OCR-local pixel components.
            # A round stamp often contains readable OCR text ("ПЕЧАТЬ"), which
            # makes the text itself look like a valid dark component and used
            # to prevent the colored-stamp fallback from running at all.
            if role == "seal":
                bbox = _find_colored_stamp_bbox(page)
                if bbox is not None:
                    output.append(OCRRegion(role, bbox, page_index))
                    continue
            label_words = ("подпись",) if role == "signature" else ("печать", "печат")
            labels = [
                region
                for region in page_regions
                if any(re.search(rf"\b{word}\w*\b", region.text.casefold()) for word in label_words)
                and not _is_explanatory_label(_nearby_label_text(region, page_regions))
            ]
            if role == "signature":
                labels = _merge_signature_label_boxes(labels, page_regions) if labels else _signature_context_labels(page_regions)
            candidates: list[tuple[float, tuple[float, float, float, float]]] = []
            for label in labels:
                excluded_bbox = nearby_stamp if role == "signature" else None
                bbox = _find_visual_bbox(page, label.bbox, role, excluded_bbox=excluded_bbox)
                if bbox is None and role == "signature" and include_empty_signature_slot:
                    bbox = _find_signature_slot_bbox(page, label.bbox)
                if bbox is not None:
                    label_height = max(8.0, label.bbox[3] - label.bbox[1])
                    label_width = max(8.0, label.bbox[2] - label.bbox[0])
                    box_width = bbox[2] - bbox[0]
                    box_height = bbox[3] - bbox[1]
                    # Reject a page-wide underline or a paragraph found below
                    # the actual signature line.
                    if role == "signature" and (box_width > label_width * 6 or box_height > label_height * 5):
                        continue
                    score = box_width * box_height
                    candidates.append((score, bbox))
            if candidates:
                _, best = max(candidates, key=lambda item: item[0])
                output.append(OCRRegion(role, best, page_index))
    return output


def _merge_signature_label_boxes(labels: list[OCRRegion], regions: list[OCRRegion]) -> list[OCRRegion]:
    merged: dict[tuple[float, float, float, float], OCRRegion] = {}
    for label in labels:
        x1, y1, x2, y2 = label.bbox
        text = label.text
        remaining = [region for region in regions if region is not label and region.page == label.page]
        while True:
            label_height = max(8.0, y2 - y1)
            center_y = (y1 + y2) / 2
            adjacent = [
                region
                for region in remaining
                if region.bbox[0] >= x2
                and region.bbox[0] - x2 <= max(8.0, label_height * 1.5)
                and abs((region.bbox[1] + region.bbox[3]) / 2 - center_y) <= label_height * 0.75
            ]
            if not adjacent:
                break
            next_region = min(adjacent, key=lambda region: region.bbox[0])
            x2 = next_region.bbox[2]
            y1 = min(y1, next_region.bbox[1])
            y2 = max(y2, next_region.bbox[3])
            text = f"{text} {next_region.text}"
            remaining.remove(next_region)
        box = (x1, y1, x2, y2)
        merged[box] = OCRRegion(text, box, label.page)
    return list(merged.values())


def _signature_context_labels(regions: list[OCRRegion]) -> list[OCRRegion]:
    labels: list[OCRRegion] = []
    role_terms = ("сотрудник", "руководител", "студент")
    for region in regions:
        words = _tokens(region.text)
        if not any(
            len(word) >= 7 and SequenceMatcher(None, word, term).ratio() >= 0.78
            for word in words
            for term in role_terms
        ):
            continue
        x1, y1, x2, y2 = region.bbox
        height = max(8.0, y2 - y1)
        center_y = (y1 + y2) / 2
        preceding = [
            candidate
            for candidate in regions
            if candidate is not region
            and candidate.bbox[2] <= x1
            and x1 - candidate.bbox[2] <= max(8.0, height * 1.5)
            and abs((candidate.bbox[1] + candidate.bbox[3]) / 2 - center_y) <= height * 0.75
        ]
        if preceding:
            previous = max(preceding, key=lambda candidate: candidate.bbox[2])
            x1 = previous.bbox[0]
            y1 = min(y1, previous.bbox[1])
            y2 = max(y2, previous.bbox[3])
            text = f"{previous.text} {region.text}"
        else:
            text = region.text
        labels.append(OCRRegion(text, (x1, y1, x2, y2), region.page))
    return labels


def _is_explanatory_label(text: str) -> bool:
    normalized = text.casefold()
    return any(fragment in normalized for fragment in ("все сведения", "учебными макет", "макет", "идентификатор образца"))


def _nearby_label_text(label: OCRRegion, regions: list[OCRRegion]) -> str:
    center_y = (label.bbox[1] + label.bbox[3]) / 2
    label_height = max(8.0, label.bbox[3] - label.bbox[1])
    nearby = [
        region.text
        for region in regions
        if abs((region.bbox[1] + region.bbox[3]) / 2 - center_y) <= label_height * 1.5
    ]
    return " ".join(nearby)


def _find_visual_bbox(
    page: Image.Image,
    label_bbox: tuple[float, float, float, float],
    role: str,
    excluded_bbox: tuple[int, int, int, int] | None = None,
) -> tuple[float, float, float, float] | None:
    width, height = page.size
    x1, y1, x2, y2 = label_bbox
    label_width = max(8.0, x2 - x1)
    label_height = max(8.0, y2 - y1)
    if role == "signature":
        search = (
            max(0, int(x1 - label_width * 0.75)),
            max(0, int(y1 - label_height * 1.5)),
            min(width, int(x2 + label_width * 2.5)),
            min(height, int(y2 + label_height * 1.5)),
        )
    else:
        # A stamp surrounds the printed "Печать" word and is often circular.
        search = (int(max(0, x1 - label_width * 10)), int(max(0, y1 - label_height * 10)), int(min(width, x2 + label_width * 12)), int(min(height, y2 + label_height * 10)))
    crop = page.crop(search).convert("L")
    # Handwritten strokes can occupy only a small fraction of the line. Keep
    # the threshold low enough for faint or short signatures while the
    # component filter below still rejects a plain printed underline.
    minimum = max(12, int(label_width * label_height * (0.03 if role == "signature" else 0.12)))
    if role == "seal":
        bbox = _seal_component_bbox(crop, search, label_bbox, threshold=190, min_pixels=minimum)
    else:
        relative_label = (x1 - search[0], y1 - search[1], x2 - search[0], y2 - search[1])
        excluded_boxes = [relative_label]
        if excluded_bbox is not None:
            excluded_boxes.append(
                (
                    excluded_bbox[0] - search[0],
                    excluded_bbox[1] - search[1],
                    excluded_bbox[2] - search[0],
                    excluded_bbox[3] - search[1],
                )
            )
        bbox = _signature_bbox(page.crop(search), threshold=195, min_pixels=minimum, excluded_boxes=excluded_boxes)
    if bbox is None:
        return None
    cx1, cy1, cx2, cy2 = bbox
    # A screenshot may include the dark application background immediately
    # beside the white page. It can look like a large ink component at the
    # edge of the signature search window; that is not a handwritten mark.
    if role == "signature" and (cx1 <= 1 or cy1 <= 1 or cx2 >= crop.width - 1 or cy2 >= crop.height - 1):
        return None
    padding = 1 if role == "signature" else max(3, int(label_height * 0.35))
    return (max(0, search[0] + cx1 - padding), max(0, search[1] + cy1 - padding), min(width, search[0] + cx2 + padding), min(height, search[1] + cy2 + padding))


def _find_signature_slot_bbox(page: Image.Image, label_bbox: tuple[float, float, float, float]) -> tuple[int, int, int, int] | None:
    """Locate the printed signature line when there are no handwritten strokes."""
    x1, y1, x2, y2 = label_bbox
    label_width = max(8.0, x2 - x1)
    label_height = max(8.0, y2 - y1)
    search = (
        int(x2 + label_width * 0.4),
        int(max(0, y1 - label_height)),
        int(min(page.width, x2 + label_width * 2.5)),
        int(min(page.height, y2 + label_height * 2)),
    )
    crop = ImageOps.grayscale(page.crop(search))
    pixels = crop.load()
    min_line_width = max(12, int(crop.width * 0.25))
    candidate_rows: list[int] = []
    for y in range(crop.height):
        dark_x = [x for x in range(crop.width) if pixels[x, y] < 235]
        if len(dark_x) >= min_line_width and max(dark_x) - min(dark_x) + 1 >= min_line_width:
            candidate_rows.append(y)
    if not candidate_rows:
        return None
    row = max(candidate_rows, key=lambda candidate: sum(pixels[x, candidate] < 235 for x in range(crop.width)))
    dark_x = [x for x in range(crop.width) if pixels[x, row] < 235]
    return (
        search[0] + min(dark_x),
        max(0, search[1] + row - 3),
        search[0] + max(dark_x) + 1,
        min(page.height, search[1] + row + 4),
    )


def _find_colored_stamp_bbox(page: Image.Image) -> tuple[int, int, int, int] | None:
    """Find the largest blue/colored stamp when OCR missed its inner text."""
    rgb = page.convert("RGB")
    width, height = rgb.size
    pixels = rgb.load()
    visited = bytearray(width * height)
    components: list[tuple[int, int, int, int, int]] = []

    def is_stamp_pixel(x: int, y: int) -> bool:
        red, green, blue = pixels[x, y]
        # Dark application chrome is commonly blue tinted too. Require a
        # minimum brightness so it cannot be mistaken for the document stamp
        # when a screenshot (rather than a cropped page) is uploaded.
        return max(red, green, blue) >= 70 and max(red, green, blue) - min(red, green, blue) >= 28 and blue >= red * 1.05 and blue >= green * 0.92

    for y in range(height):
        for x in range(width):
            index = y * width + x
            if visited[index] or not is_stamp_pixel(x, y):
                visited[index] = 1
                continue
            visited[index] = 1
            stack = [(x, y)]
            points: list[tuple[int, int]] = []
            while stack:
                px, py = stack.pop()
                points.append((px, py))
                for ny in range(max(0, py - 1), min(height, py + 2)):
                    for nx in range(max(0, px - 1), min(width, px + 2)):
                        ni = ny * width + nx
                        if not visited[ni]:
                            visited[ni] = 1
                            if is_stamp_pixel(nx, ny):
                                stack.append((nx, ny))
            if len(points) < 25:
                continue
            xs = [point[0] for point in points]
            ys = [point[1] for point in points]
            box = (min(xs), min(ys), max(xs) + 1, max(ys) + 1, len(points))
            box_width, box_height = box[2] - box[0], box[3] - box[1]
            ratio = box_width / max(1, box_height)
            if box_width >= 20 and box_height >= 20 and 0.55 <= ratio <= 1.8:
                components.append(box)
    if not components:
        return None
    largest = max(components, key=lambda item: (item[2] - item[0]) * (item[3] - item[1]))
    padding = max(3, min(largest[2] - largest[0], largest[3] - largest[1]) // 12)
    return max(0, largest[0] - padding), max(0, largest[1] - padding), min(width, largest[2] + padding), min(height, largest[3] + padding)


def _ink_bbox(image: Image.Image, threshold: int, min_pixels: int, exclude_rule: bool = False) -> tuple[int, int, int, int] | None:
    mask = ImageOps.grayscale(image)
    pixels = mask.load()
    xs: list[int] = []
    ys: list[int] = []
    for y in range(mask.height):
        for x in range(mask.width):
            if pixels[x, y] < threshold:
                xs.append(x)
                ys.append(y)
    if exclude_rule and ys:
        row_counts: dict[int, int] = {}
        for y in ys:
            row_counts[y] = row_counts.get(y, 0) + 1
        rule_rows = {y for y, count in row_counts.items() if count >= image.width * 0.15}
        if rule_rows:
            kept = [(x, y) for x, y in zip(xs, ys) if all(abs(y - rule_y) > 2 for rule_y in rule_rows)]
            if len(kept) >= min_pixels:
                xs = [point[0] for point in kept]
                ys = [point[1] for point in kept]
    if len(xs) < min_pixels:
        return None
    return min(xs), min(ys), max(xs) + 1, max(ys) + 1


def _signature_bbox(
    image: Image.Image,
    threshold: int,
    min_pixels: int,
    excluded_boxes: list[tuple[float, float, float, float]] | None = None,
) -> tuple[int, int, int, int] | None:
    """Return handwriting strokes while ignoring the thin printed underline."""
    grayscale = ImageOps.grayscale(image)
    pixels = grayscale.load()
    width, height = image.size
    visited = bytearray(width * height)
    components: list[tuple[int, int, int, int, int]] = []

    def is_ink(x: int, y: int) -> bool:
        if pixels[x, y] >= threshold:
            return False
        if not excluded_boxes:
            return True
        if not any(bx1 <= x < bx2 and by1 <= y < by2 for bx1, by1, bx2, by2 in excluded_boxes):
            return True
        return False

    for y in range(height):
        for x in range(width):
            index = y * width + x
            if visited[index] or not is_ink(x, y):
                visited[index] = 1
                continue
            visited[index] = 1
            stack = [(x, y)]
            points: list[tuple[int, int]] = []
            while stack:
                px, py = stack.pop()
                points.append((px, py))
                for ny in range(max(0, py - 1), min(height, py + 2)):
                    for nx in range(max(0, px - 1), min(width, px + 2)):
                        ni = ny * width + nx
                        if not visited[ni]:
                            visited[ni] = 1
                            if is_ink(nx, ny):
                                stack.append((nx, ny))
            if len(points) < min_pixels:
                continue
            xs = [point[0] for point in points]
            ys = [point[1] for point in points]
            component = (min(xs), min(ys), max(xs) + 1, max(ys) + 1, len(points))
            component_width = component[2] - component[0]
            component_height = component[3] - component[1]
            if component_height <= 3 and component_width >= 12:
                continue
            components.append(component)
    if not components:
        return None
    # Handwriting can consist of several disconnected strokes. Join strokes
    # on this short line, but keep the resulting box bounded.
    x1 = min(component[0] for component in components)
    y1 = min(component[1] for component in components)
    x2 = max(component[2] for component in components)
    y2 = max(component[3] for component in components)
    return x1, y1, x2, y2


def _seal_component_bbox(
    image: Image.Image,
    search: tuple[int, int, int, int],
    label: tuple[float, float, float, float],
    threshold: int,
    min_pixels: int,
) -> tuple[int, int, int, int] | None:
    """Pick the nearby roughly circular ink component instead of the label text."""
    pixels = image.load()
    width, height = image.size
    visited = bytearray(width * height)
    ignore = (max(0, int(label[0] - search[0])), max(0, int(label[1] - search[1])), min(width, int(label[2] - search[0])), min(height, int(label[3] - search[1])))
    candidates: list[tuple[int, int, int, int, int]] = []
    for y in range(height):
        for x in range(width):
            index = y * width + x
            if visited[index] or pixels[x, y] >= threshold or ignore[0] <= x < ignore[2] and ignore[1] <= y < ignore[3]:
                visited[index] = 1
                continue
            visited[index] = 1
            stack = [(x, y)]
            min_x = max_x = x
            min_y = max_y = y
            count = 0
            while stack:
                px, py = stack.pop()
                count += 1
                min_x, max_x = min(min_x, px), max(max_x, px)
                min_y, max_y = min(min_y, py), max(max_y, py)
                for ny in range(max(0, py - 1), min(height, py + 2)):
                    for nx in range(max(0, px - 1), min(width, px + 2)):
                        ni = ny * width + nx
                        if visited[ni]:
                            continue
                        visited[ni] = 1
                        if pixels[nx, ny] < threshold and not (ignore[0] <= nx < ignore[2] and ignore[1] <= ny < ignore[3]):
                            stack.append((nx, ny))
            box_width, box_height = max_x - min_x + 1, max_y - min_y + 1
            ratio = box_width / max(1, box_height)
            if count >= min_pixels and box_width >= 10 and box_height >= 10 and 0.55 <= ratio <= 1.8:
                candidates.append((min_x, min_y, max_x + 1, max_y + 1, count))
    if not candidates:
        # Stamps may be anti-aliased into several thin arcs; use their total
        # ink envelope as a safe fallback, still excluding the printed label.
        fallback = _ink_bbox(image, threshold=190, min_pixels=min_pixels)
        if fallback is None:
            return None
        fx1, fy1, fx2, fy2 = fallback
        if fx2 - fx1 < 15 or fy2 - fy1 < 15:
            return None
        return fallback
    # Seal outlines are among the largest near-square components. Ignore the
    # small letter components inside the stamp and printed labels.
    candidate = max(candidates, key=lambda item: (item[2] - item[0]) * (item[3] - item[1]))
    return candidate[:4]


def _tokens(value: str) -> list[str]:
    return re.findall(r"[a-zа-яё0-9]+", value.casefold())


def _date_word_tokens(value: str) -> list[str]:
    try:
        parsed = datetime.strptime(value, "%d.%m.%Y")
    except ValueError:
        return []
    months = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря")
    return [str(parsed.day), months[parsed.month - 1], str(parsed.year)]


def _refine_bbox(region: OCRRegion, start: int, end: int) -> tuple[float, float, float, float]:
    """Estimate the horizontal extent of a matched substring inside a line box."""
    x1, y1, x2, y2 = region.bbox
    text_length = max(1, len(region.text))
    left = x1 + (x2 - x1) * start / text_length
    right = x1 + (x2 - x1) * end / text_length
    return left, y1, right, y2
