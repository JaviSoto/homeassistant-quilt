from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CI_SCRIPT = REPO_ROOT / "ci" / "run_ci.sh"


def test_hassfest_uses_bounded_remote_staging_by_default() -> None:
    script = CI_SCRIPT.read_text(encoding="utf-8")

    assert 'hassfest_remote_host="${HASSFEST_REMOTE_HOST:-miniserver}"' in script
    assert 'hassfest_remote_staging=""' in script
    assert 'hassfest_remote_staging_candidate=""' in script
    assert 'if [[ "$hassfest_remote_host" == "local" ]]' in script
    assert '"$sshx" --timeout 20 --connect-timeout 5 --batch' in script
    assert 'mktemp -d /tmp/quilt-hassfest.XXXXXX' in script
    assert 'rm -rf -- "$hassfest_remote_staging"' in script
    assert 'rm -rf -- "$hassfest_staging"' in script
    assert '"$hassfest_remote_staging:/github/workspace:ro"' in script
    assert "COPYFILE_DISABLE=1 tar --no-xattrs" in script


def test_invalid_remote_staging_path_is_never_removed(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for name in ("docker", "rsync", "uv"):
        path = fake_bin / name
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)

    sshx = fake_bin / "sshx"
    sshx.write_text(
        """#!/bin/sh
set -eu
case "$*" in
  *"docker info"*) exit 0 ;;
  *"mktemp -d"*) printf '%s\\n' '/tmp/not-a-quilt-stage' ;;
  *"rm -rf"*) printf '%s\\n' rm >>"$FAKE_SSHX_LOG" ;;
esac
""",
        encoding="utf-8",
    )
    sshx.chmod(0o755)

    log = tmp_path / "sshx.log"
    staging_root = tmp_path / "cache"
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "SSHX": str(sshx),
            "FAKE_SSHX_LOG": str(log),
            "HASSFEST_STAGING_ROOT": str(staging_root),
        }
    )
    result = subprocess.run(
        ["bash", str(CI_SCRIPT)],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "invalid hassfest staging path" in result.stderr.lower()
    assert not log.exists()
