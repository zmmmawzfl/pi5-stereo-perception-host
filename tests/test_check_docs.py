"""tools.check_docs 的启发式规则：防止它自己慢慢变成"什么都报"而废掉。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.check_docs import (  # noqa: E402
    EDGE_ROOT,
    HOST_ROOT,
    WORKSPACE,
    build_index,
    check,
    is_runtime,
    looks_like_path,
    resolves,
)


class LookLikePathTest(unittest.TestCase):
    def test_real_paths(self):
        for tok in ("pi/capture/base.py", "docs/HANDOFF.md", "hardware/camera_mount.scad"):
            self.assertTrue(looks_like_path(tok), tok)

    def test_parameter_pairs_are_not_paths(self):
        for tok in ("lens_dx/lens_dz", "rib_x0/rib_x1", "width/height", "r/g/b", "hd_x/2"):
            self.assertFalse(looks_like_path(tok), tok)

    def test_ros_types_and_git_refs_are_not_paths(self):
        for tok in ("sensor_msgs/Image", "std_msgs/String", "origin/main", "P25/P75"):
            self.assertFalse(looks_like_path(tok), tok)

    def test_urls_and_flags(self):
        self.assertFalse(looks_like_path("https://x/a.py"))
        self.assertFalse(looks_like_path("--replay-root"))


class RuntimeTest(unittest.TestCase):
    def test_runtime_outputs(self):
        for tok in ("runs/demo/report.md", "data/recordings/x/pairs.json", "data/calib/stereo_calib.npz"):
            self.assertTrue(is_runtime(tok), tok)

    def test_workspace_artifacts_are_optional(self):
        # artifacts/ 不在 git 里：新机器 clone 后不存在，文档引用它不该报错
        self.assertTrue(is_runtime("artifacts/calibration/README.md"))
        self.assertTrue(is_runtime("artifacts/pc-env-2026-11-14/x.tar.gz"))

    def test_checked_in_docs_are_not_runtime(self):
        self.assertFalse(is_runtime("docs/HANDOFF.md"))
        self.assertFalse(is_runtime("hardware/README.md"))


class ResolveTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = build_index()

    def test_checked_in_file_resolves(self):
        # 用一个两个仓库里都一定有的文件，别依赖工作区根目录下的非 git 文档
        self.assertTrue(resolves("tools/check_docs.py", [HOST_ROOT], self.index))

    def test_edge_repo_file_resolves_from_workspace_root(self):
        self.assertTrue(resolves("hardware/camera_mount.scad", [EDGE_ROOT], self.index))

    def test_missing_file_does_not_resolve(self):
        self.assertFalse(resolves("pi/capture/replay_backend.py", [EDGE_ROOT, WORKSPACE], self.index))


class RepoDocsAreCleanTest(unittest.TestCase):
    """两个仓库 + 根目录的文档不许出现指错文件的引用（本测试就是这次体检的护栏）。"""

    def test_no_broken_references(self):
        paths = [
            os.path.join(HOST_ROOT, "README.md"),
            os.path.join(HOST_ROOT, "docs"),
            os.path.join(WORKSPACE, "README.md"),
            os.path.join(WORKSPACE, "docs"),
            os.path.join(EDGE_ROOT, "README.md"),
            os.path.join(EDGE_ROOT, "docs"),
            os.path.join(EDGE_ROOT, "hardware"),
        ]
        self.assertEqual(0, check([p for p in paths if os.path.exists(p)], False))


if __name__ == "__main__":
    unittest.main()
