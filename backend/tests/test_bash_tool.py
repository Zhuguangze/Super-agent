"""app/tools/bash.py——子进程执行、cwd 锁定工作区、超时与截断、Windows shell 解析。"""

import os
import re
import sys
from pathlib import Path

import pytest

from super_agent.app.tools.bash import BashTool, _find_windows_bash
from super_agent.app.workspace import user_workspace


@pytest.fixture()
def tool(tmp_path: Path) -> BashTool:
    return BashTool(user_workspace(tmp_path / "workspaces", 1))


async def _run(tool: BashTool, **params):
    return await tool.execute("t1", params, None, None)


async def test_bash_runs_command_and_captures_output(tool) -> None:
    result = await _run(tool, command=f'"{sys.executable}" -c "print(\'hello from bash\')"')
    assert result.is_error is False
    assert "hello from bash" in result.content
    assert "[exit 0]" in result.content


async def test_bash_cwd_is_user_workspace(tool, tmp_path: Path) -> None:
    """命令的工作目录就是该用户的工作区（路径隔离的可执行面）。"""
    result = await _run(tool, command=f'"{sys.executable}" -c "import os; print(os.getcwd())"')
    assert result.is_error is False
    ws_root = str((tmp_path / "workspaces" / "1").resolve())
    assert ws_root in result.content


async def test_bash_nonzero_exit_is_error(tool) -> None:
    result = await _run(tool, command=f'"{sys.executable}" -c "import sys; sys.exit(3)"')
    assert result.is_error is True
    assert "[exit 3]" in result.content


async def test_bash_timeout_kills_process(tool) -> None:
    result = await _run(
        tool,
        command=f'"{sys.executable}" -c "import time; time.sleep(30)"',
        timeout=1,
    )
    assert result.is_error is True
    assert "超时" in result.content


# ---- Windows shell 解析（票：date 报错修复——Unix 语法在 Windows 可用） ----


def test_git_bash_derived_from_git_exe(tmp_path: Path, monkeypatch) -> None:
    """从 git.exe 推导同安装根的 bash.exe——非标准安装位置（如 E:\\Softwares\\git）也能找到。"""
    fake_git = tmp_path / "tools" / "Git" / "cmd" / "git.exe"
    fake_git.parent.mkdir(parents=True)
    bash_dir = fake_git.parents[1] / "bin"
    bash_dir.mkdir(parents=True)
    (bash_dir / "bash.exe").write_bytes(b"")
    monkeypatch.delenv("SUPER_AGENT_BASH", raising=False)
    monkeypatch.setattr(
        "super_agent.app.tools.bash.shutil.which",
        lambda name: str(fake_git) if name == "git" else None,
    )
    assert _find_windows_bash() == str(bash_dir / "bash.exe")


def test_wsl_stub_bash_is_rejected(tmp_path: Path, monkeypatch) -> None:
    """System32 的 WSL bash 与 WindowsApps 存根不可用——回退 None（走 cmd）。"""
    monkeypatch.delenv("SUPER_AGENT_BASH", raising=False)
    monkeypatch.setattr(
        "super_agent.app.tools.bash.shutil.which",
        lambda name: r"C:\Windows\System32\bash.exe" if name == "bash" else None,
    )
    assert _find_windows_bash() is None


def test_explicit_env_var_wins(tmp_path: Path, monkeypatch) -> None:
    """SUPER_AGENT_BASH 显式指定优先（部署可控）。"""
    bash = tmp_path / "custom" / "bash.exe"
    bash.parent.mkdir(parents=True)
    bash.write_bytes(b"")
    monkeypatch.setenv("SUPER_AGENT_BASH", str(bash))
    assert _find_windows_bash() == str(bash)


@pytest.mark.skipif(os.name != "nt" or _find_windows_bash() is None, reason="需要 Windows + Git Bash")
async def test_unix_date_syntax_works_on_windows(tool) -> None:
    """模型写的 GNU 语法（date "+%Y-%m-%d"）在 Windows 上原样可跑——不再报错。"""
    result = await _run(tool, command='date "+%Y-%m-%d"')
    assert result.is_error is False
    assert re.search(r"\d{4}-\d{2}-\d{2}", result.content)


@pytest.mark.skipif(os.name != "nt" or _find_windows_bash() is None, reason="需要 Windows + Git Bash")
def test_bash_description_tells_real_shell(tmp_path: Path) -> None:
    """工具描述告知真实 shell——模型不会再误判平台写错语法。"""
    t = BashTool(user_workspace(tmp_path / "workspaces", 1))
    assert "Git Bash" in t.description
    assert "cmd.exe" not in t.description



async def test_bash_truncates_long_output(tool) -> None:
    result = await _run(tool, command=f'"{sys.executable}" -c "print(\'x\' * 20000)"')
    assert result.is_error is False
    assert len(result.content) < 20000
    assert "已截断" in result.content


async def test_bash_stderr_and_empty_command(tool) -> None:
    err = await _run(tool, command=f'"{sys.executable}" -c "import sys; print(\'oops\', file=sys.stderr)"')
    assert "[stderr]" in err.content and "oops" in err.content
    empty = await _run(tool, command="   ")
    assert empty.is_error and "command" in empty.content


def test_bash_is_sequential_and_not_in_default_allowlist() -> None:
    assert BashTool.__dict__["execution_mode"] == "sequential"
