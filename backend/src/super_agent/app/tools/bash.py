"""bash 工具——平台 shell 子进程，cwd 锁定用户工作区，带超时与输出截断。

spec §4.4：bash 与文件工具同受 Workspace 约束。sequential 执行（命令间
常有顺序副作用）。默认不在审批白名单内（ADR-0001）。
Windows 上优先 Git Bash（模型写的 Unix 语法原样可用），找不到才走 cmd。
"""

import asyncio
import os
import shutil
import subprocess
from pathlib import Path

from super_agent.agent.types import AgentTool, AgentToolResult
from super_agent.app.workspace import Workspace, WorkspaceError

DEFAULT_TIMEOUT = 60.0
MAX_TIMEOUT = 300.0
MAX_OUTPUT_CHARS = 10_000


def _git_bash_from_git(git_path: str) -> str | None:
    """git.exe → 同安装根的 bin/bash.exe（Git\\cmd\\git.exe → Git\\bin\\bash.exe）。"""
    for ancestor in Path(git_path).parents:
        candidate = ancestor / "bin" / "bash.exe"
        if candidate.is_file():
            return str(candidate)
    return None


def _find_windows_bash() -> str | None:
    """Windows 下可用的 Unix shell：显式环境变量 > git.exe 推导 > 标准路径 > PATH。

    PATH 兜底排除 System32（WSL bash——cwd 是 Windows 路径会出问题）与
    WindowsApps 存根。都找不到返回 None（调用方回退 cmd.exe）。
    """
    explicit = os.environ.get("SUPER_AGENT_BASH")
    if explicit and Path(explicit).is_file():
        return explicit
    git = shutil.which("git")
    if git:
        derived = _git_bash_from_git(git)
        if derived:
            return derived
    for env_key in ("ProgramFiles", "ProgramFiles(x86)"):
        root = os.environ.get(env_key)
        if root:
            candidate = Path(root) / "Git" / "bin" / "bash.exe"
            if candidate.is_file():
                return str(candidate)
    found = shutil.which("bash")
    if found and "system32" not in found.lower() and "windowsapps" not in found.lower():
        return found
    return None


class BashTool(AgentTool):
    name = "bash"
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的 shell 命令"},
            "timeout": {"type": "integer", "description": "超时秒数，默认取部署配置，上限 300"},
        },
        "required": ["command"],
    }
    execution_mode = "sequential"

    def __init__(self, workspace: Workspace, default_timeout: float = DEFAULT_TIMEOUT) -> None:
        self._workspace = workspace
        self._default_timeout = default_timeout
        # 描述告知真实 shell——模型据此选择语法，不再误判平台
        if os.name == "nt" and _find_windows_bash():
            shell_note = "Windows 上经 Git Bash 执行——Unix 语法（date/ls/grep/管道等）可用"
        elif os.name == "nt":
            shell_note = "Windows 上走 cmd.exe——用 DOS 语法"
        else:
            shell_note = "Unix 上走 sh"
        self.description = (
            f"在工作区内执行 shell 命令（{shell_note}），"
            "返回 stdout/stderr 与退出码。默认超时可由部署配置（Settings 的 bash_timeout），"
            "单次调用也可传 timeout（上限 300 秒）。命令只能访问当前用户的工作区。"
        )

    async def execute(self, tool_call_id, params, signal, on_update) -> AgentToolResult:
        command = params.get("command")
        if not isinstance(command, str) or not command.strip():
            return AgentToolResult(content="参数错误: command 不能为空", is_error=True)
        try:
            timeout = min(max(1.0, float(params.get("timeout") or self._default_timeout)), MAX_TIMEOUT)
        except (TypeError, ValueError):
            timeout = self._default_timeout
        try:
            cwd = self._workspace.resolve("")
        except WorkspaceError as exc:
            return AgentToolResult(content=str(exc), is_error=True)

        try:
            # 同步子进程跑在线程池：不依赖事件循环类型（Windows asyncio 子进程
            # 在部分 loop 下抛 NotImplementedError() 空 str 异常——线上事故根因）
            completed = await asyncio.to_thread(self._run_sync, command, cwd, timeout)
        except subprocess.TimeoutExpired:
            return AgentToolResult(
                content=f"命令超时（{timeout:.0f} 秒）已终止: {command[:100]}", is_error=True
            )
        except OSError as exc:
            return AgentToolResult(
                content=f"进程启动失败: {type(exc).__name__}: {exc}", is_error=True
            )

        out = (completed.stdout or b"").decode("utf-8", errors="replace").strip()
        err = (completed.stderr or b"").decode("utf-8", errors="replace").strip()
        parts: list[str] = []
        if out:
            parts.append(out[:MAX_OUTPUT_CHARS])
            if len(out) > MAX_OUTPUT_CHARS:
                parts.append("…（stdout 已截断）")
        if err:
            parts.append(f"[stderr]\n{err[:MAX_OUTPUT_CHARS]}")
            if len(err) > MAX_OUTPUT_CHARS:
                parts.append("…（stderr 已截断）")
        parts.append(f"[exit {completed.returncode}]")
        body = "\n".join(parts)
        is_error = completed.returncode != 0
        return AgentToolResult(content=body or f"[exit {completed.returncode}]", is_error=is_error)

    def _run_sync(self, command: str, cwd: str, timeout: float) -> subprocess.CompletedProcess:
        """同步子进程：Windows + Git Bash 走 argv 数组（Unix 语法可用），否则系统默认 shell。"""
        if os.name == "nt":
            bash = _find_windows_bash()
        else:
            bash = None
        if bash is not None:
            return subprocess.run(
                [bash, "-c", command], cwd=cwd, capture_output=True, timeout=timeout
            )
        return subprocess.run(
            command, cwd=cwd, capture_output=True, shell=True, timeout=timeout
        )
