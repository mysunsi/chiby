"""
集成示例：如何在现有工程中调用 remediation（不修改 core/executor.py）。

用法：
  在仓库根目录（ai-ops-assistant）执行：
    set PYTHONPATH=%CD%
    python -m remediator.remediation.example_integration

或通过代码复制以下「包装器」模式，将你方的 executor 注入 execute_fn。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

# 保证可从仓库根导入 remediator
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from remediator.remediation import (
    CommandExecutionOutcome,
    EnvironmentSnapshot,
    RemediationController,
    RemediationKnowledgeBase,
)


def subprocess_executor(command: str) -> CommandExecutionOutcome:
    """
    演示用本地执行后端：生产环境请替换为对 core.executor 的包装。

    约束：不得修改 core/executor.py 时，在此处把你的调用封装成
    ``Callable[[str], CommandExecutionOutcome]`` 即可。
    """
    proc = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return CommandExecutionOutcome(
        command=command,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
        return_code=proc.returncode,
    )


def demo() -> None:
    kb_dir = tempfile.mkdtemp(prefix="remediation_kb_")
    kb_path = Path(kb_dir) / "cases.sqlite"
    kb = RemediationKnowledgeBase(kb_path)

    env = EnvironmentSnapshot(
        os_name=os.name,
        os_version=sys.platform,
        shell=os.environ.get("SHELL", ""),
        current_user=os.environ.get("USERNAME") or os.environ.get("USER", ""),
        is_root_or_sudo=False,
        cwd=os.getcwd(),
    )

    controller = RemediationController(
        execute_fn=subprocess_executor,
        knowledge_base=kb,
        env=env,
        max_retries=3,
        similarity_stop=0.90,
        llm_model=os.environ.get("REMEDIATION_MODEL", "gpt-4o-mini"),
        litellm_api_key=os.environ.get("OPENAI_API_KEY") or os.environ.get("LITELLM_API_KEY"),
        litellm_api_base=os.environ.get("OPENAI_API_BASE"),
        interactive=False,
    )

    # Linux/macOS：典型 Permission denied；Windows：演示非零退出（解析多为 UNKNOWN）
    if os.name == "nt":
        failing_cmd = 'cmd /c "echo simulated failure 1>&2 && exit 1"'
    else:
        failing_cmd = (
            "cp /etc/hosts /tmp/__remediation_denied_test__.log 2>&1 || exit 1"
        )
    result = controller.run(failing_cmd)

    print("termination:", result.termination.value)
    print("message:", result.message)
    print("history:", result.history.format_arrow_chain())
    print("knowledge_saved:", result.knowledge_saved)


if __name__ == "__main__":
    demo()
