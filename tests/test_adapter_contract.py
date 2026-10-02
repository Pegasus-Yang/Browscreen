"""原始截图的通用数据契约校验。"""

import pytest
from pydantic import ValidationError

from browscreen.adapters.base import BrowserScreenshot


@pytest.mark.parametrize("dimensions", [(0, 96), (-1, 96), (128, 0), (float("nan"), 96), (128, float("inf"))])
def test_dimensions_are_positive_and_finite(png_bytes, dimensions):
    with pytest.raises(ValidationError):
        BrowserScreenshot(png_bytes=png_bytes, css_viewport_width=dimensions[0], css_viewport_height=dimensions[1])


def test_screenshot_bytes_are_required():
    with pytest.raises(ValidationError):
        BrowserScreenshot(png_bytes=b"", css_viewport_width=128, css_viewport_height=96)
