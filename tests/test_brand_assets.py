from __future__ import annotations

import subprocess
from pathlib import Path
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
COMPONENT_DIR = ROOT / "custom_components" / "quilt"


def test_brand_assets_use_ha_2026_3_layout() -> None:
    """Home Assistant 2026.3+ expects custom integration assets in brand/."""
    assert (COMPONENT_DIR / "brand" / "icon.png").is_file()
    assert (COMPONENT_DIR / "brand" / "logo.png").is_file()
    assert not (COMPONENT_DIR / "icon.png").exists()
    assert not (COMPONENT_DIR / "logo.png").exists()


def test_release_zip_includes_brand_assets(tmp_path: Path) -> None:
    zip_path = tmp_path / "quilt.zip"
    subprocess.run(
        ["bash", str(ROOT / "scripts" / "build_release_zip.sh"), str(zip_path)],
        check=True,
        cwd=ROOT,
    )

    with ZipFile(zip_path) as zip_file:
        names = set(zip_file.namelist())

    assert "custom_components/quilt/brand/icon.png" in names
    assert "custom_components/quilt/brand/logo.png" in names
    assert "custom_components/quilt/icon.png" not in names
    assert "custom_components/quilt/logo.png" not in names
