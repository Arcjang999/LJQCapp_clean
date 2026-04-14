from __future__ import annotations

from io import BytesIO

import pypdf


MM_PER_INCH = 25.4
A4_WIDTH_POINTS = 210 / MM_PER_INCH * 72
A4_HEIGHT_POINTS = 297 / MM_PER_INCH * 72
FORBIDDEN_WATERMARK_TERMS = (b"LJQCApp", b"Quality Control")
WATERMARK_ROTATION_SIGNATURE = b"0.8571673007 0.5150380749 -0.5150380749 0.8571673007"
WATERMARK_SCALE_SIGNATURE = b"0.017 0 0 0.017"
MIN_TILED_WATERMARK_COUNT = 10


def assert_uniform_a4_pages_and_watermark(
    pdf_bytes: bytes,
) -> pypdf.PdfReader:
    for forbidden_term in FORBIDDEN_WATERMARK_TERMS:
        assert forbidden_term not in pdf_bytes
    reader = pypdf.PdfReader(BytesIO(pdf_bytes))
    _assert_uniform_page_boxes(reader)
    _assert_watermark_signature(reader)
    return reader


def _assert_uniform_page_boxes(reader: pypdf.PdfReader) -> None:
    media_sizes = {
        (round(float(page.mediabox.width), 2), round(float(page.mediabox.height), 2))
        for page in reader.pages
    }
    crop_sizes = {
        (round(float(page.cropbox.width), 2), round(float(page.cropbox.height), 2))
        for page in reader.pages
    }
    assert len(media_sizes) == 1
    assert len(crop_sizes) == 1

    media_width, media_height = next(iter(media_sizes))
    crop_width, crop_height = next(iter(crop_sizes))
    assert abs(media_width - A4_WIDTH_POINTS) < 0.6
    assert abs(media_height - A4_HEIGHT_POINTS) < 0.6
    assert abs(crop_width - A4_WIDTH_POINTS) < 0.6
    assert abs(crop_height - A4_HEIGHT_POINTS) < 0.6


def _assert_watermark_signature(reader: pypdf.PdfReader) -> None:
    for page in reader.pages:
        content_stream = page.get_contents()
        assert content_stream is not None
        content_bytes = content_stream.get_data()
        assert content_bytes.count(WATERMARK_ROTATION_SIGNATURE) >= MIN_TILED_WATERMARK_COUNT
        assert content_bytes.count(WATERMARK_SCALE_SIGNATURE) >= MIN_TILED_WATERMARK_COUNT
