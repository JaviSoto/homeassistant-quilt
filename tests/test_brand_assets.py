from __future__ import annotations

import json
import subprocess
from pathlib import Path
from zipfile import ZipFile

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]
COMPONENT_DIR = ROOT / "custom_components" / "quilt"


def test_brand_assets_use_ha_2026_3_layout() -> None:
    """Home Assistant 2026.3+ expects custom integration assets in brand/."""
    assert (COMPONENT_DIR / "brand" / "icon.png").is_file()
    assert (COMPONENT_DIR / "brand" / "logo.png").is_file()
    assert not (COMPONENT_DIR / "icon.png").exists()
    assert not (COMPONENT_DIR / "logo.png").exists()


def test_manifest_declares_runtime_dependency_and_release_version() -> None:
    manifest = json.loads((COMPONENT_DIR / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["requirements"] == ["grpcio>=1.78.0,<2.0"]
    assert manifest["version"] == "1.0.7"


def test_manifest_admits_home_assistant_2026_10_grpcio_runtime() -> None:
    """The custom integration must accept Core's grpcio 1.83.1 dependency."""
    manifest = json.loads((COMPONENT_DIR / "manifest.json").read_text(encoding="utf-8"))
    requirements = [Requirement(value) for value in manifest["requirements"]]
    grpc_requirements = [
        requirement for requirement in requirements if requirement.name == "grpcio"
    ]

    assert len(grpc_requirements) == 1
    assert grpc_requirements[0].specifier.contains("1.78.0")
    assert grpc_requirements[0].specifier.contains("1.83.1")


def test_release_zip_uses_hacs_zip_release_layout(tmp_path: Path) -> None:
    zip_path = tmp_path / "quilt.zip"
    subprocess.run(
        ["bash", str(ROOT / "scripts" / "build_release_zip.sh"), str(zip_path)],
        check=True,
        cwd=ROOT,
    )

    with ZipFile(zip_path) as zip_file:
        names = set(zip_file.namelist())

    assert "__init__.py" in names
    assert "diagnostics.py" in names
    assert "manifest.json" in names
    assert "brand/icon.png" in names
    assert "brand/logo.png" in names
    assert "icon.png" not in names
    assert "logo.png" not in names
    assert not any(name.startswith("custom_components/") for name in names)
