#!/usr/bin/env python3
"""Fetch and verify the pinned ripgrep binary used by desktop bundles."""

from __future__ import annotations

from dataclasses import dataclass
import argparse
import hashlib
import io
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
from urllib.request import Request, urlopen
import zipfile


RIPGREP_VERSION = "15.2.0"
RELEASE_BASE = (
    "https://github.com/BurntSushi/ripgrep/releases/download/"
    f"{RIPGREP_VERSION}"
)
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "vendor" / "ripgrep"


@dataclass(frozen=True)
class Artifact:
    filename: str
    sha256: str
    kind: str


# Digests come from the official GitHub release asset metadata. Linux x86_64
# deliberately uses the GNU-linked Debian artifact: the 15.2.0 x86_64 musl
# release has an upstream report of occasional faults during very large scans.
ARTIFACTS = {
    ("darwin", "aarch64"): Artifact(
        "ripgrep-15.2.0-aarch64-apple-darwin.tar.gz",
        "3750b2e93f37e0c692657da574d7019a101c0084da05a790c83fd335bad973e4",
        "tar",
    ),
    ("darwin", "x86_64"): Artifact(
        "ripgrep-15.2.0-x86_64-apple-darwin.tar.gz",
        "af7825fcc69a2afc7a7aea55fc9af90e26421d8f20fe59df32e233c0b8a231c1",
        "tar",
    ),
    ("linux", "aarch64"): Artifact(
        "ripgrep-15.2.0-aarch64-unknown-linux-gnu.tar.gz",
        "a740b91c82eaf9914cfedd353572f2791cbe0162c84101ee0951058f4dcbc90d",
        "tar",
    ),
    ("linux", "x86_64"): Artifact(
        "ripgrep_15.2.0-1_amd64.deb",
        "5af93eebe4c352474632cf1d28523b0e98bcd4e2f115a249a713b8d8c7d1d01c",
        "deb",
    ),
    ("win32", "aarch64"): Artifact(
        "ripgrep-15.2.0-aarch64-pc-windows-msvc.zip",
        "e4abca10c3a64ebea742667dd7009449d49403db5460dd6873e389fa2945360f",
        "zip",
    ),
    ("win32", "x86_64"): Artifact(
        "ripgrep-15.2.0-x86_64-pc-windows-msvc.zip",
        "71b2fef860abe467217a538ff31de02f5258807c0129f771846f87bd029aafc5",
        "zip",
    ),
}


def normalize_platform(value: str) -> str:
    folded = value.strip().casefold()
    if folded.startswith("linux"):
        return "linux"
    if folded in {"darwin", "mac", "macos"}:
        return "darwin"
    if folded in {"win32", "windows", "cygwin", "msys"}:
        return "win32"
    raise ValueError(f"Unsupported ripgrep platform: {value}")


def normalize_machine(value: str) -> str:
    folded = value.strip().casefold()
    if folded in {"amd64", "x64", "x86_64"}:
        return "x86_64"
    if folded in {"arm64", "aarch64"}:
        return "aarch64"
    raise ValueError(f"Unsupported ripgrep architecture: {value}")


def artifact_for(platform_name: str, machine: str) -> Artifact:
    key = (normalize_platform(platform_name), normalize_machine(machine))
    try:
        return ARTIFACTS[key]
    except KeyError as exc:
        raise ValueError(f"No pinned ripgrep artifact for {key[0]}/{key[1]}") from exc


def _archive_member_bytes(payload: bytes, kind: str, executable: str) -> bytes:
    if kind == "zip":
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = archive.namelist()
            matches = [name for name in names if Path(name).name == executable]
            if len(matches) != 1:
                raise RuntimeError(f"Expected one {executable} in archive, found {len(matches)}")
            return archive.read(matches[0])
    if kind == "tar":
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            matches = [member for member in archive.getmembers()
                       if member.isfile() and Path(member.name).name == executable]
            if len(matches) != 1:
                raise RuntimeError(f"Expected one {executable} in archive, found {len(matches)}")
            stream = archive.extractfile(matches[0])
            if stream is None:
                raise RuntimeError(f"Could not read {executable} from archive")
            return stream.read()
    raise ValueError(f"Unsupported archive kind: {kind}")


def _deb_executable(payload: bytes) -> bytes:
    tool = shutil.which("dpkg-deb")
    if not tool:
        raise RuntimeError("dpkg-deb is required to unpack the pinned Linux x86_64 artifact")
    with tempfile.TemporaryDirectory(prefix="stillpoint-ripgrep-") as temporary:
        root = Path(temporary)
        package = root / "ripgrep.deb"
        package.write_bytes(payload)
        extracted = root / "package"
        subprocess.run(
            [tool, "--extract", str(package), str(extracted)],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        binary = extracted / "usr" / "bin" / "rg"
        if not binary.is_file():
            raise RuntimeError("Pinned ripgrep Debian package did not contain usr/bin/rg")
        return binary.read_bytes()


def fetch(platform_name: str, machine: str, output_dir: Path) -> Path:
    selected_platform = normalize_platform(platform_name)
    artifact = artifact_for(selected_platform, machine)
    url = f"{RELEASE_BASE}/{artifact.filename}"
    request = Request(url, headers={"User-Agent": "StillPoint-build"})
    with urlopen(request, timeout=60) as response:
        payload = response.read(16 * 1024 * 1024 + 1)
    if len(payload) > 16 * 1024 * 1024:
        raise RuntimeError("ripgrep release artifact exceeded the download safety limit")
    actual = hashlib.sha256(payload).hexdigest()
    if actual != artifact.sha256:
        raise RuntimeError(
            f"ripgrep checksum mismatch for {artifact.filename}: {actual}"
        )

    executable = "rg.exe" if selected_platform == "win32" else "rg"
    binary = (
        _deb_executable(payload)
        if artifact.kind == "deb"
        else _archive_member_bytes(payload, artifact.kind, executable)
    )
    if not binary:
        raise RuntimeError("Pinned ripgrep executable was empty")
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / executable
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_bytes(binary)
    temporary.chmod(0o755)
    os.replace(temporary, destination)
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", default=sys.platform)
    parser.add_argument("--machine", default=platform.machine())
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--print-selection", action="store_true")
    args = parser.parse_args(argv)
    artifact = artifact_for(args.platform, args.machine)
    if args.print_selection:
        print(f"{RIPGREP_VERSION} {artifact.filename} {artifact.sha256}")
        return 0
    destination = fetch(args.platform, args.machine, args.output_dir)
    print(f"Pinned ripgrep {RIPGREP_VERSION}: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
