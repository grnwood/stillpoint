# OCR Setup

StillPoint uses the external [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) executable to extract text from images. The Python package included with StillPoint does not include the Tesseract program itself.

After installing Tesseract, restart StillPoint.

## macOS

Install with Homebrew:

```sh
brew install tesseract
```

StillPoint checks both Apple Silicon Homebrew (`/opt/homebrew/bin/tesseract`) and Intel Homebrew (`/usr/local/bin/tesseract`), even when an app launched from Finder does not inherit your shell `PATH`.

## Windows

Install the UB Mannheim build with WinGet:

```powershell
winget install UB-Mannheim.TesseractOCR
```

The default `Program Files/Tesseract-OCR` installation is detected automatically.

## Linux

Ubuntu or Debian:

```sh
sudo apt update
sudo apt install tesseract-ocr
```

Fedora:

```sh
sudo dnf install tesseract
```

Arch Linux:

```sh
sudo pacman -S tesseract
```

## Custom installation path

If Tesseract is installed elsewhere, set `TESSERACT_CMD` to the full executable path before starting StillPoint. For example:

```sh
export TESSERACT_CMD=/custom/path/tesseract
```

You can verify the command in a terminal with:

```sh
tesseract --version
```
