"""通过 pytest 执行预览脚本的确定性生命周期回归。"""

from pathlib import Path
import shutil
import subprocess

import pytest


def test_preview_lifecycle_and_polling():
    """使用 Node 内置测试运行器，无需安装前端依赖。"""
    node = shutil.which(cmd="node")
    if node is None:
        pytest.skip(reason="预览脚本回归需要 Node.js 22 或更高版本")
    script = Path(__file__).with_name("preview.test.cjs")
    result = subprocess.run(args=[node, "--test", str(script)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
