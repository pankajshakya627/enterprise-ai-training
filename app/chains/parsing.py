from __future__ import annotations

from collections import Counter
from html import escape

import pdfplumber
from unstructured.documents.elements import Element, ElementMetadata, NarrativeText, Table, Title
from unstructured.partition.auto import partition

# Running headers and footers sit in the page margin; policy content does not.
PAGE_MARGIN_PT = 50


def parse_document(path: str) -> list[Element]:
    """Parse a source document into ordered headings, paragraphs and whole tables."""
    if path.lower().endswith(".pdf"):
        return _parse_pdf(path)
    return partition(filename=path)


def _parse_pdf(path: str) -> list[Element]:
    """Born-digital PDFs only: tables come from ruling lines, headings from font size."""
    blocks: list[tuple[int, float, Element]] = []
    with pdfplumber.open(path) as pdf:
        sizes = Counter(round(char["size"], 1) for page in pdf.pages for char in page.chars)
        body_size = sizes.most_common(1)[0][0]

        for page in pdf.pages:
            content = page.crop((0, PAGE_MARGIN_PT, page.width, page.height - PAGE_MARGIN_PT))
            text_area = content
            for table in content.find_tables():
                text_area = text_area.outside_bbox(table.bbox)
                blocks.append((page.page_number, table.bbox[1], _table_element(table.extract())))

            paragraph: list[str] = []
            paragraph_top = previous_bottom = 0.0
            for line in text_area.extract_text_lines():
                is_heading = round(line["chars"][0]["size"], 1) > body_size
                gap = line["top"] - previous_bottom
                if paragraph and (is_heading or gap > body_size / 2):
                    blocks.append(
                        (page.page_number, paragraph_top, NarrativeText(" ".join(paragraph)))
                    )
                    paragraph = []
                if is_heading:
                    blocks.append((page.page_number, line["top"], Title(line["text"])))
                else:
                    if not paragraph:
                        paragraph_top = line["top"]
                    paragraph.append(line["text"])
                previous_bottom = line["bottom"]
            if paragraph:
                blocks.append((page.page_number, paragraph_top, NarrativeText(" ".join(paragraph))))

    return [element for _, _, element in sorted(blocks, key=lambda block: block[:2])]


def _table_element(rows: list[list[str | None]]) -> Table:
    cells = [[" ".join((cell or "").split()) for cell in row] for row in rows]
    html = "".join(
        "<tr>" + "".join(f"<td>{escape(cell)}</td>" for cell in row) + "</tr>" for row in cells
    )
    return Table(
        text=" ".join(cell for row in cells for cell in row),
        metadata=ElementMetadata(text_as_html=f"<table>{html}</table>"),
    )
