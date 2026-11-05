from __future__ import annotations

from pathlib import Path

from PIL import Image

from sp.app import ocr_utils


def test_find_tesseract_uses_platform_location_when_gui_path_is_missing(
    tmp_path, monkeypatch
) -> None:
    executable = tmp_path / "homebrew" / "bin" / "tesseract"
    executable.parent.mkdir(parents=True)
    executable.write_text("", encoding="utf-8")
    monkeypatch.delenv("TESSERACT_CMD", raising=False)
    monkeypatch.setattr(ocr_utils.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        ocr_utils, "_platform_tesseract_candidates", lambda: (executable,)
    )

    assert ocr_utils.find_tesseract_command() == str(executable)


def test_ocr_configures_discovered_tesseract_command(tmp_path, monkeypatch) -> None:
    image_path = tmp_path / "sample.png"
    Image.new("RGB", (10, 10), "white").save(image_path)
    command = "/opt/homebrew/bin/tesseract"
    monkeypatch.setattr(ocr_utils, "find_tesseract_command", lambda: command)
    monkeypatch.setattr(
        ocr_utils.pytesseract,
        "image_to_string",
        lambda _image: " Recognized text ",
    )

    result = ocr_utils.ocr_image_file(image_path)

    assert result.ok
    assert result.text == "Recognized text"
    assert ocr_utils.pytesseract.pytesseract.tesseract_cmd == command


def test_missing_tesseract_result_includes_install_guidance(tmp_path, monkeypatch) -> None:
    image_path = tmp_path / "sample.png"
    Image.new("RGB", (10, 10), "white").save(image_path)
    monkeypatch.setattr(ocr_utils, "find_tesseract_command", lambda: None)

    result = ocr_utils.ocr_image_file(image_path)

    assert result.error_code == "tesseract_missing"
    assert "brew install tesseract" in (result.message or "")
    assert "winget install" in (result.message or "")
    assert "sudo apt install tesseract-ocr" in (result.message or "")
