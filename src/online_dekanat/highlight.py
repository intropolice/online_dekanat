from __future__ import annotations

from pathlib import Path
from dataclasses import replace
import re
from uuid import uuid4
from datetime import datetime

from PIL import Image, ImageDraw, ImageOps
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
    visual = _visual_regions(pages, regions, card)
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
    for region in visual:
        if region.text != "signature" or region.page < 0 or region.page >= len(pages):
            continue
        page = pages[region.page].convert("RGB")
        x1, y1, x2, y2 = region.bbox
        padding_x = max(16, int((x2 - x1) * 0.2))
        padding_y = max(12, int((y2 - y1) * 0.35))
        crop = page.crop(
            (
                max(0, int(x1 - padding_x)),
                max(0, int(y1 - padding_y)),
                min(page.width, int(x2 + padding_x)),
                min(page.height, int(y2 + padding_y)),
            )
        )
        crop = crop.resize(
            (crop.width * 2, crop.height * 2),
            Image.Resampling.LANCZOS,
        )
        path = root / f"{uuid4().hex}-signature-detail.png"
        crop.save(path, format="PNG", optimize=True)
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
    queries: list[tuple[list[str], bool]] = []
    for field in card.fields.values():
        if not field.is_found:
            continue
        # Presence fields are visual objects. The words "Подпись" and
        # "Печать" are labels, not the evidence used by the card.
        if field.key in {"signature_and_seal", "student_signature", "signature", "seal"}:
            continue
        value = field.value
        if value.casefold() == "есть":
            queries.extend(
                ([token], False)
                for token in _tokens(field.title)
                if token not in {"и", "с", "о"}
            )
        else:
            tokens = _tokens(value)
            if tokens:
                queries.append((tokens, field.key == "group"))
                date_tokens = _date_word_tokens(value)
                if date_tokens:
                    queries.append((date_tokens, False))

    for page in sorted({region.page for region in regions}):
        page_regions = [region for region in regions if region.page == page]
        stream: list[tuple[str, int, int, int]] = []
        for region_index, region in enumerate(page_regions):
            for match in re.finditer(r"[a-zа-яё0-9]+", region.text.casefold()):
                stream.append((match.group(), region_index, match.start(), match.end()))
        for query, group_code in queries:
            width = len(query)
            for start in range(0, len(stream) - width + 1):
                candidate = [item[0] for item in stream[start : start + width]]
                if not _highlight_tokens_match(query, candidate, group_code=group_code):
                    continue
                matched = stream[start : start + width]
                if query and query[-1].isdigit() and start + width < len(stream) and stream[start + width][0] in {"год", "года", "году"}:
                    matched = [*matched, stream[start + width]]
                grouped: dict[int, list[tuple[int, int]]] = {}
                for _, region_index, char_start, char_end in matched:
                    grouped.setdefault(region_index, []).append((char_start, char_end))
                for region_index, spans in grouped.items():
                    region = page_regions[region_index]
                    char_start = min(span[0] for span in spans)
                    char_end = max(span[1] for span in spans)
                    refined = _refine_bbox(region, char_start, char_end)
                    selected[id(region)] = replace(region, bbox=refined)
                break
    return list(selected.values())


def _highlight_tokens_match(
    query: list[str],
    candidate: list[str],
    *,
    group_code: bool = False,
) -> bool:
    if candidate == query:
        return True
    if not group_code or len(query) < 2 or len(candidate) != len(query):
        return False
    if not all(token.isdigit() for token in query[1:]):
        return False
    return (
        candidate[1:] == query[1:]
        and query[0].isalpha()
        and candidate[0].isalpha()
    )


def _visual_regions(pages: list[Image.Image], regions: list[OCRRegion], card: DocumentCard) -> list[OCRRegion]:
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
        colored_components: list[tuple[int, int, int, int, int]] | None = None
        for role in wanted:
            # Prefer the stamp's colored ink over OCR-local pixel components.
            # A round stamp often contains readable OCR text ("ПЕЧАТЬ"), which
            # makes the text itself look like a valid dark component and used
            # to prevent the colored-stamp fallback from running at all.
            if role == "seal":
                if colored_components is None:
                    colored_components = _colored_ink_components(page)
                bbox = _find_colored_stamp_bbox(page, colored_components)
                if bbox is not None:
                    output.append(OCRRegion(role, bbox, page_index))
                    continue
            label_words = ("подпись", "подпис") if role == "signature" else ("печать", "печат")
            labels = [
                region
                for region in page_regions
                if any(word in region.text.casefold() for word in label_words)
                and not _is_explanatory_label_region(region, page_regions)
            ]
            candidates: list[tuple[float, tuple[float, float, float, float]]] = []
            for label in labels:
                label_bbox = _signature_label_bbox(label, page_regions) if role == "signature" else label.bbox
                bbox = _find_visual_bbox(page, label_bbox, role)
                if bbox is not None:
                    label_height = max(8.0, label_bbox[3] - label_bbox[1])
                    label_width = max(8.0, label_bbox[2] - label_bbox[0])
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
            elif role == "signature":
                if colored_components is None:
                    colored_components = _colored_ink_components(page)
                bbox = _find_colored_signature_bbox(page, colored_components)
                if bbox is not None:
                    output.append(OCRRegion(role, bbox, page_index))
    return output


def _is_explanatory_label(text: str) -> bool:
    normalized = text.casefold()
    return any(fragment in normalized for fragment in ("все сведения", "являются учебными", "учебными макет", "идентификатор образца"))


def _is_explanatory_label_region(region: OCRRegion, page_regions: list[OCRRegion]) -> bool:
    if _is_explanatory_label(region.text):
        return True

    _, y1, _, y2 = region.bbox
    center = (y1 + y2) / 2
    height = max(1.0, y2 - y1)
    same_line = []
    for candidate in page_regions:
        _, candidate_y1, _, candidate_y2 = candidate.bbox
        candidate_height = max(1.0, candidate_y2 - candidate_y1)
        candidate_center = (candidate_y1 + candidate_y2) / 2
        if abs(center - candidate_center) <= max(3.0, min(height, candidate_height) * 0.6):
            same_line.append(candidate)

    line_text = " ".join(item.text for item in sorted(same_line, key=lambda item: item.bbox[0]))
    return _is_explanatory_label(line_text)


def _signature_label_bbox(
    label: OCRRegion,
    page_regions: list[OCRRegion],
) -> tuple[float, float, float, float]:
    x1, y1, x2, y2 = label.bbox
    height = max(1.0, y2 - y1)
    center = (y1 + y2) / 2
    qualifiers = {
        "сотрудника",
        "студента",
        "руководителя",
        "декана",
        "директора",
        "заявителя",
        "работника",
        "ответственного",
        "уполномоченного",
        "начальника",
        "преподавателя",
        "куратора",
    }

    for region in sorted(page_regions, key=lambda item: item.bbox[0]):
        rx1, ry1, rx2, ry2 = region.bbox
        qualifier = re.sub(r"[^a-zа-яё]", "", region.text.casefold())
        region_height = max(1.0, ry2 - ry1)
        region_center = (ry1 + ry2) / 2
        gap = rx1 - x2
        if (
            qualifier in qualifiers
            and gap >= -height * 0.2
            and gap <= max(3.0, height * 1.5)
            and abs(center - region_center) <= max(3.0, min(height, region_height) * 0.6)
        ):
            x2 = max(x2, rx2)
            y1 = min(y1, ry1)
            y2 = max(y2, ry2)
            height = max(1.0, y2 - y1)
            center = (y1 + y2) / 2

    return x1, y1, x2, y2


def _find_visual_bbox(page: Image.Image, label_bbox: tuple[float, float, float, float], role: str) -> tuple[float, float, float, float] | None:
    width, height = page.size
    x1, y1, x2, y2 = label_bbox
    label_width = max(8.0, x2 - x1)
    label_height = max(8.0, y2 - y1)
    if role == "signature":
        # A handwritten signature is normally to the right of its printed label.
        search = (int(x2 + label_width * 0.4), int(max(0, y1 - label_height)), int(min(width, x2 + label_width * 2.5)), int(min(height, y2 + label_height * 2)))
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
        bbox = _signature_bbox(crop, threshold=170, min_pixels=minimum)
    if bbox is None:
        return None
    cx1, cy1, cx2, cy2 = bbox
    # A screenshot may include the dark application background immediately
    # beside the white page. It can look like a large ink component at the
    # edge of the signature search window; that is not a handwritten mark.
    if role == "signature" and (cx1 <= 1 or cy1 <= 1 or cx2 >= crop.width - 1 or cy2 >= crop.height - 1):
        return None
    padding = max(3, int(label_height * 0.35))
    return (max(0, search[0] + cx1 - padding), max(0, search[1] + cy1 - padding), min(width, search[0] + cx2 + padding), min(height, search[1] + cy2 + padding))


def _find_colored_stamp_bbox(
    page: Image.Image,
    components: list[tuple[int, int, int, int, int]] | None = None,
) -> tuple[int, int, int, int] | None:
    """Find the largest blue/colored stamp when OCR missed its inner text."""
    width, height = page.size
    components = components if components is not None else _colored_ink_components(page)
    stamps = [
        component
        for component in components
        if component[4] >= 25
        and component[2] - component[0] >= 20
        and component[3] - component[1] >= 20
        and 0.55 <= (component[2] - component[0]) / max(1, component[3] - component[1]) <= 1.8
    ]
    if not stamps:
        return None
    largest = max(stamps, key=lambda item: (item[2] - item[0]) * (item[3] - item[1]))
    padding = max(3, min(largest[2] - largest[0], largest[3] - largest[1]) // 12)
    return max(0, largest[0] - padding), max(0, largest[1] - padding), min(width, largest[2] + padding), min(height, largest[3] + padding)


def _find_colored_signature_bbox(
    page: Image.Image,
    components: list[tuple[int, int, int, int, int]] | None = None,
) -> tuple[int, int, int, int] | None:
    """Find a blue handwritten signature when OCR misses its printed label."""
    width, height = page.size
    components = components if components is not None else _colored_ink_components(page)
    stamp_boxes = [
        component[:4]
        for component in components
        if component[4] >= 25
        and component[2] - component[0] >= 20
        and component[3] - component[1] >= 20
        and 0.55 <= (component[2] - component[0]) / max(1, component[3] - component[1]) <= 1.8
    ]
    signatures = []
    for component in components:
        x1, y1, x2, y2, pixels_count = component
        box_width, box_height = x2 - x1, y2 - y1
        ratio = box_width / max(1, box_height)
        if (
            pixels_count < 25
            or box_width < 20
            or box_height < 4
            or box_height > height * 0.15
            or box_width > width * 0.4
            or not 1.8 <= ratio <= 10
            or x1 <= 1
            or y1 <= 1
            or x2 >= width - 1
            or y2 >= height - 1
        ):
            continue
        if any(
            x1 < stamp[2] and x2 > stamp[0] and y1 < stamp[3] and y2 > stamp[1]
            for stamp in stamp_boxes
        ):
            continue
        signatures.append(component)
    if not signatures:
        return None
    largest = max(signatures, key=lambda item: item[4])
    padding = max(3, min(largest[3] - largest[1], 12) // 3)
    return max(0, largest[0] - padding), max(0, largest[1] - padding), min(width, largest[2] + padding), min(height, largest[3] + padding)


def _colored_ink_components(image: Image.Image) -> list[tuple[int, int, int, int, int]]:
    rgb = image.convert("RGB")
    width, height = rgb.size
    pixels = rgb.load()
    visited = bytearray(width * height)
    components: list[tuple[int, int, int, int, int]] = []

    def is_colored_ink(x: int, y: int) -> bool:
        red, green, blue = pixels[x, y]
        # Exclude dark application chrome when a screenshot is uploaded.
        return max(red, green, blue) >= 70 and max(red, green, blue) - min(red, green, blue) >= 28 and blue >= red * 1.05 and blue >= green * 0.92

    for y in range(height):
        for x in range(width):
            index = y * width + x
            if visited[index]:
                continue
            visited[index] = 1
            if not is_colored_ink(x, y):
                continue
            stack = [(x, y)]
            min_x = max_x = x
            min_y = max_y = y
            count = 0
            while stack:
                current_x, current_y = stack.pop()
                count += 1
                min_x = min(min_x, current_x)
                max_x = max(max_x, current_x)
                min_y = min(min_y, current_y)
                max_y = max(max_y, current_y)
                for neighbor_y in range(max(0, current_y - 1), min(height, current_y + 2)):
                    for neighbor_x in range(max(0, current_x - 1), min(width, current_x + 2)):
                        neighbor_index = neighbor_y * width + neighbor_x
                        if not visited[neighbor_index]:
                            visited[neighbor_index] = 1
                            if is_colored_ink(neighbor_x, neighbor_y):
                                stack.append((neighbor_x, neighbor_y))
            components.append((min_x, min_y, max_x + 1, max_y + 1, count))
    return components


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


def _signature_bbox(image: Image.Image, threshold: int, min_pixels: int) -> tuple[int, int, int, int] | None:
    """Return handwriting strokes while ignoring the thin printed underline."""
    pixels = ImageOps.grayscale(image).load()
    width, height = image.size
    visited = bytearray(width * height)
    components: list[tuple[int, int, int, int, int]] = []
    for y in range(height):
        for x in range(width):
            index = y * width + x
            if visited[index] or pixels[x, y] >= threshold:
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
                            if pixels[nx, ny] < threshold:
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
