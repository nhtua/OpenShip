import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_opensandbox_config.py"
PROFILE = (
    '[docker]\nnetwork_mode = "openship-sandbox-data"\n'
    'publish_host = "127.0.0.1"\nno_new_privileges = true\n'
    'drop_capabilities = ["ALL"]\npids_limit = 128\n'
    '[server]\nmax_sandbox_timeout_seconds = 120\n'
)
EXECD_IMAGE = 'execd_image = "opensandbox/execd@sha256:' + "a" * 64 + '"\n'


def check_config(tmp_path, config):
    path = tmp_path / "sandbox.toml"
    path.write_text(config)
    env = os.environ.copy()
    env.pop("OPENSANDBOX_SERVER_API_KEY", None)
    return subprocess.run([sys.executable, str(SCRIPT), str(path)], capture_output=True, text=True, env=env, check=False)


def test_requires_gvisor_and_authentication(tmp_path):
    result = check_config(tmp_path, '[runtime]\ntype = "docker"\n')
    assert result.returncode != 0
    assert "gvisor" in result.stderr.lower()


def test_accepts_explicit_gvisor_with_api_key(tmp_path):
    result = check_config(tmp_path, '[runtime]\ntype = "docker"\nexecd_run_as_init = true\n' + EXECD_IMAGE + '[secure_runtime]\ntype = "gvisor"\ndocker_runtime = "runsc"\n' + PROFILE + 'api_key = "test-only-key"\n')
    assert result.returncode == 0, result.stderr
    assert "test-only-key" not in result.stdout + result.stderr


def test_rejects_missing_api_key(tmp_path):
    result = check_config(tmp_path, '[runtime]\ntype = "docker"\nexecd_run_as_init = true\n' + EXECD_IMAGE + '[secure_runtime]\ntype = "gvisor"\ndocker_runtime = "runsc"\n' + PROFILE)
    assert result.returncode != 0
    assert "api key" in result.stderr.lower()


def test_rejects_unbounded_or_external_sandbox_profile(tmp_path):
    config = (
        '[runtime]\ntype = "docker"\n'
        'execd_run_as_init = true\n' + EXECD_IMAGE +
        '[secure_runtime]\ntype = "gvisor"\ndocker_runtime = "runsc"\n'
        '[server]\napi_key = "test-only-key"\nmax_sandbox_timeout_seconds = 300\n'
        '[docker]\nnetwork_mode = "bridge"\nno_new_privileges = false\n'
        'drop_capabilities = []\npids_limit = 1000\n'
    )
    result = check_config(tmp_path, config)
    assert result.returncode != 0
    assert "sandbox profile" in result.stderr.lower()
