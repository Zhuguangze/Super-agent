"""app/workspace.py——路径归一化与越界拒绝。"""

from pathlib import Path

import pytest

from super_agent.app.workspace import Workspace, WorkspaceError, user_workspace


def test_relative_paths_anchor_to_root(tmp_path: Path) -> None:
    ws = Workspace(tmp_path)
    assert ws.resolve("docs/a.md") == (tmp_path / "docs" / "a.md").resolve()
    assert ws.resolve("") == tmp_path.resolve()
    assert ws.resolve(".") == tmp_path.resolve()


def test_dotdot_escape_rejected(tmp_path: Path) -> None:
    ws = Workspace(tmp_path)
    with pytest.raises(WorkspaceError):
        ws.resolve("../outside.txt")
    with pytest.raises(WorkspaceError):
        ws.resolve("a/../../b.txt")
    # 中途出去又回来是合法的
    assert ws.resolve("a/../b.txt") == (tmp_path / "b.txt").resolve()


def test_absolute_path_outside_rejected(tmp_path: Path) -> None:
    ws = Workspace(tmp_path)
    with pytest.raises(WorkspaceError):
        ws.resolve(str(Path.home() / "elsewhere.txt"))
    # 工作区内的绝对路径合法
    assert ws.resolve(str(tmp_path / "in.txt")) == (tmp_path / "in.txt").resolve()


def test_user_workspace_isolates_by_id(tmp_path: Path) -> None:
    a = user_workspace(tmp_path, 1)
    b = user_workspace(tmp_path, 2)
    assert a.root != b.root
    with pytest.raises(WorkspaceError):
        a.resolve("../2/secret.txt")


def test_symlink_escape_rejected(tmp_path: Path) -> None:
    """symlink 指向外部 → resolve 跟随后越界即拒（Windows 无特权时跳过）。"""
    outside = tmp_path.parent / f"outside_{tmp_path.name}"
    outside.mkdir(exist_ok=True)
    link = tmp_path / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("当前环境无 symlink 权限")
    ws = Workspace(tmp_path)
    with pytest.raises(WorkspaceError):
        ws.resolve("link/secret.txt")
