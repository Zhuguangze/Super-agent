"""工作区分配与路径约束（spec §3 目录树）。

每个 User 一个 Workspace 目录（``data/workspaces/<user_id>/``），文件类工具与
bash 的执行边界都在其中：路径归一化后越界即拒。resolve 会跟随符号链接，
因此通过 symlink 指向外部的路径同样会被拦下。
"""

from pathlib import Path


class WorkspaceError(Exception):
    """路径越界或非法——工具层捕获后以 is_error 结果回填。"""


class Workspace:
    """单个用户的文件沙箱：一切路径先经 resolve 再使用。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def resolve(self, path: str) -> Path:
        """相对路径锚定 root，绝对路径必须落在 root 内；返回规范化后的绝对路径。

        空 path 视为 root 本身。越界（含 symlink 逃逸）抛 WorkspaceError。
        """
        if not path or path.strip() == "":
            resolved = self.root.resolve()
        else:
            candidate = Path(path)
            if not candidate.is_absolute():
                candidate = self.root / candidate
            resolved = candidate.resolve()
        if resolved != self.root.resolve() and not resolved.is_relative_to(self.root.resolve()):
            raise WorkspaceError(f"路径越出工作区: {path}")
        return resolved


def user_workspace(root: Path, user_id: int) -> Workspace:
    """按 user_id 分配工作区（目录边界即隔离边界）。"""
    return Workspace(root / str(user_id))
