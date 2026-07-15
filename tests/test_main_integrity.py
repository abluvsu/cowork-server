from cowork.__main__ import _format_corruption_message

def test_format_corruption_message():
    assert _format_corruption_message("pydantic_core") == "venv corrupted (pydantic_core): run  uv sync --reinstall-package pydantic-core"
    assert _format_corruption_message("httpx") == "venv corrupted (httpx): run  uv sync --reinstall-package httpx"
