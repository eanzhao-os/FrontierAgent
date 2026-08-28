import subprocess
from pathlib import Path


def test_node_client_logic():
    root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        ["node", "--test", str(root / "tests/js/web_client.test.mjs")],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
