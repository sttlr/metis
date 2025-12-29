# SPDX-FileCopyrightText: Copyright 2025 Arm Limited and/or its affiliates <open-source-office@arm.com>
# SPDX-License-Identifier: Apache-2.0

import os
import tempfile
from unittest.mock import patch, MagicMock
from metis.engine.tools.list_files import (
    list_files,
    ListFilesTool,
    _is_path_in_ignored_directory,
    _is_directory_explicitly_ignored,
)


class TestListFiles:
    """Test cases for list_files functionality."""

    def test_zero_limit_returns_empty(self):
        """Test that limit of 0 returns empty results."""
        files, limit_reached = list_files("/tmp", True, 0)
        assert files == []
        assert limit_reached is False

    @patch("subprocess.run")
    @patch("os.path.exists")
    @patch("os.path.isdir")
    def test_ripgrep_called_with_correct_args(
        self, mock_isdir, mock_exists, mock_subprocess
    ):
        """Test that ripgrep is called with correct arguments."""
        mock_exists.return_value = True
        mock_isdir.return_value = True
        mock_subprocess.return_value = MagicMock(
            returncode=0, stdout="file1.txt\nfile2.txt\n", stderr=""
        )

        with patch("pathspec.PathSpec.from_lines") as mock_pathspec:
            mock_pathspec.return_value = MagicMock()
            mock_pathspec.return_value.match_file.return_value = False

            list_files("/test/dir", False, 100)

            # Check that subprocess.run was called
            assert mock_subprocess.called
            args, kwargs = mock_subprocess.call_args
            cmd = args[0]
            assert "rg" in cmd
            assert "--files" in cmd
            assert "--hidden" in cmd
            assert "--follow" in cmd

    def test_special_directories_root(self):
        """Test handling of root directory."""
        files, limit_reached = list_files("/", True, 100)
        assert files == ["/"]
        assert limit_reached is False

    def test_special_directories_home(self):
        """Test handling of home directory."""
        home = os.path.expanduser("~")
        files, limit_reached = list_files(home, True, 100)
        assert files == [home]
        assert limit_reached is False

    def test_is_path_in_ignored_directory(self):
        """Test the ignore path checking function."""
        # Test hidden directory
        assert _is_path_in_ignored_directory("node_modules/file.txt") is True
        assert _is_path_in_ignored_directory(".git/config") is True
        assert _is_path_in_ignored_directory("src/main.py") is False

        # Test exact matches
        assert _is_path_in_ignored_directory("node_modules") is True
        assert _is_path_in_ignored_directory("__pycache__") is True

    def test_is_directory_explicitly_ignored(self):
        """Test directory ignore checking."""
        assert _is_directory_explicitly_ignored("node_modules") is True
        assert _is_directory_explicitly_ignored(".hidden") is True  # hidden dir
        assert _is_directory_explicitly_ignored("src") is False

    @patch("subprocess.run")
    @patch("os.scandir")
    @patch("os.path.exists")
    @patch("os.path.isdir")
    def test_hidden_directory_handling(
        self, mock_isdir, mock_exists, mock_scandir, mock_subprocess
    ):
        """Test special handling for hidden directories."""
        mock_exists.return_value = True
        mock_isdir.return_value = True
        mock_subprocess.return_value = MagicMock(returncode=0, stdout="", stderr="")

        # Mock directory entries
        mock_entries = [
            MagicMock(is_dir=lambda: True, is_symlink=lambda: False, name="subdir")
        ]
        mock_scandir.return_value = mock_entries

        with patch("pathspec.PathSpec.from_lines") as mock_pathspec:
            mock_pathspec.return_value = MagicMock()
            mock_pathspec.return_value.match_file.return_value = False

            # Test targeting hidden directory
            files, limit_reached = list_files("/test/.hidden", True, 100)

            # Should have called subprocess with special args for hidden dirs
            assert mock_subprocess.called
            args, kwargs = mock_subprocess.call_args
            cmd = args[0]
            assert "--no-ignore-vcs" in cmd
            assert "--no-ignore" in cmd

    @patch("subprocess.run")
    @patch("os.path.exists")
    @patch("os.path.isdir")
    def test_limit_handling(self, mock_isdir, mock_exists, mock_subprocess):
        """Test that limits are respected."""
        mock_exists.return_value = True
        mock_isdir.return_value = True
        mock_subprocess.return_value = MagicMock(
            returncode=0,
            stdout="file1.txt\nfile2.txt\nfile3.txt\nfile4.txt\nfile5.txt\n",
            stderr="",
        )

        with patch("pathspec.PathSpec.from_lines") as mock_pathspec:
            mock_pathspec.return_value = MagicMock()
            mock_pathspec.return_value.match_file.return_value = False

            files, limit_reached = list_files("/test/dir", False, 3)

            assert len(files) <= 3
            assert limit_reached is True


class TestListFilesTool:
    """Test cases for ListFilesTool class."""

    def test_tool_initialization(self):
        """Test tool initialization."""
        tool = ListFilesTool("/test/path", None)
        assert tool.name == "list_files"
        assert "list files" in tool.description.lower()
        assert tool.codebase_path == os.path.abspath("/test/path")

    def test_safe_path_check(self):
        """Test path safety checking."""
        tool = ListFilesTool("/safe/path", None)

        # Safe path
        assert tool._is_safe_path("/safe/path/subdir") is True
        # Unsafe path
        assert tool._is_safe_path("/unsafe/path") is False

    @patch("os.path.exists")
    def test_nonexistent_path(self, mock_exists):
        """Test handling of nonexistent paths."""
        mock_exists.return_value = False
        tool = ListFilesTool("/test", None)

        result = tool.run("nonexistent")
        assert "does not exist" in result

    @patch("os.path.exists")
    @patch("os.path.isdir")
    def test_non_directory_path(self, mock_isdir, mock_exists):
        """Test handling of non-directory paths."""
        mock_exists.return_value = True
        mock_isdir.return_value = False
        tool = ListFilesTool("/test", None)

        result = tool.run("file.txt")
        assert "not a directory" in result

    def test_empty_path(self):
        """Test handling of empty path."""
        tool = ListFilesTool("/test", None)
        result = tool.run("")
        assert "No path provided" in result

    def test_gitignore_integration(self):
        """Test that .gitignore patterns are respected."""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create .gitignore
            gitignore_path = os.path.join(temp_dir, ".gitignore")
            with open(gitignore_path, "w") as f:
                f.write("*.tmp\nignore_me.txt\n")

            # Create test files
            test_file = os.path.join(temp_dir, "test.txt")
            with open(test_file, "w") as f:
                f.write("content")

            ignored_tmp = os.path.join(temp_dir, "file.tmp")
            with open(ignored_tmp, "w") as f:
                f.write("ignored")

            ignored_exact = os.path.join(temp_dir, "ignore_me.txt")
            with open(ignored_exact, "w") as f:
                f.write("ignored")

            # Test the tool
            tool = ListFilesTool(temp_dir, None)
            result = tool.run(".", recursive=False)

            # Should include test.txt but not the ignored files
            assert "test.txt" in result
            assert "file.tmp" not in result
            assert "ignore_me.txt" not in result

    def test_gitignore_respects_directory_patterns_recursive(self):
        """Test that .gitignore patterns for directories are respected in recursive mode."""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create directory structure
            os.makedirs(os.path.join(temp_dir, "src"))
            os.makedirs(os.path.join(temp_dir, "node_modules"))
            os.makedirs(os.path.join(temp_dir, "build"))
            os.makedirs(os.path.join(temp_dir, "ignored-dir"))

            # Create .gitignore
            gitignore_path = os.path.join(temp_dir, ".gitignore")
            with open(gitignore_path, "w") as f:
                f.write("node_modules/\nbuild/\nignored-dir/\n")

            # Create some files
            with open(os.path.join(temp_dir, "src", "index.ts"), "w") as f:
                f.write("")
            with open(os.path.join(temp_dir, "node_modules", "package.json"), "w") as f:
                f.write("")
            with open(os.path.join(temp_dir, "build", "output.js"), "w") as f:
                f.write("")
            with open(os.path.join(temp_dir, "ignored-dir", "file.txt"), "w") as f:
                f.write("")

            # Test the tool in recursive mode
            tool = ListFilesTool(temp_dir, None)
            result = tool.run(".", recursive=True)

            # Should include src/ but not the gitignored directories
            assert "src/" in result
            assert "node_modules" not in result
            assert "build" not in result
            assert "ignored-dir" not in result

    def test_nested_gitignore_files(self):
        """Test that nested .gitignore files are handled correctly."""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create nested directory structure
            os.makedirs(os.path.join(temp_dir, "src", "components"), exist_ok=True)
            os.makedirs(os.path.join(temp_dir, "src", "temp"), exist_ok=True)
            os.makedirs(os.path.join(temp_dir, "src", "utils"), exist_ok=True)

            # Create root .gitignore
            with open(os.path.join(temp_dir, ".gitignore"), "w") as f:
                f.write("node_modules/\n")

            # Create nested .gitignore in src/
            with open(os.path.join(temp_dir, "src", ".gitignore"), "w") as f:
                f.write("temp/\n")

            # Test the tool in recursive mode
            tool = ListFilesTool(temp_dir, None)
            result = tool.run(".", recursive=True)

            # Should include src/, components/, utils/ but not temp
            assert "src/" in result
            assert "components/" in result
            assert "utils/" in result
            assert "temp" not in result

    @patch("subprocess.run")
    def test_gitignore_integration_comprehensive(self, mock_subprocess):
        """Test comprehensive .gitignore integration with various patterns."""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Mock ripgrep to return expected files (excluding gitignored ones)
            mock_subprocess.return_value = MagicMock(
                returncode=0, stdout="src/index.ts\nallowed-dir/file.txt\n", stderr=""
            )

            # Create directory structure
            os.makedirs(os.path.join(temp_dir, "src"))
            os.makedirs(os.path.join(temp_dir, "node_modules"))
            os.makedirs(os.path.join(temp_dir, "build"))
            os.makedirs(os.path.join(temp_dir, "dist"))
            os.makedirs(os.path.join(temp_dir, "allowed-dir"))

            # Create .gitignore
            with open(os.path.join(temp_dir, ".gitignore"), "w") as f:
                f.write("node_modules/\nbuild/\ndist/\n*.log\n")

            # Create files
            with open(os.path.join(temp_dir, "src", "index.ts"), "w") as f:
                f.write("console.log('hello')")
            with open(os.path.join(temp_dir, "allowed-dir", "file.txt"), "w") as f:
                f.write("content")
            with open(os.path.join(temp_dir, "debug.log"), "w") as f:
                f.write("log content")

            # Test the tool in recursive mode
            tool = ListFilesTool(temp_dir, None)
            result = tool.run(".", recursive=True)

            # Should include allowed directories and files, exclude gitignored ones
            assert "src/" in result
            assert "allowed-dir/" in result
            assert "node_modules" not in result
            assert "build" not in result
            assert "dist" not in result
            # Files: should include allowed files
            assert "index.ts" in result
            assert "file.txt" in result
            assert "debug.log" not in result

    def test_non_recursive_gitignore_respected(self):
        """Test that .gitignore is respected in non-recursive mode."""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create directory structure
            os.makedirs(os.path.join(temp_dir, "src"))
            os.makedirs(os.path.join(temp_dir, "node_modules"))
            os.makedirs(os.path.join(temp_dir, "allowed-dir"))

            # Create .gitignore
            with open(os.path.join(temp_dir, ".gitignore"), "w") as f:
                f.write("node_modules/\n")

            # Test the tool in non-recursive mode
            tool = ListFilesTool(temp_dir, None)
            result = tool.run(".", recursive=False)

            # Should include src/ and allowed-dir/, but not node_modules
            assert "src/" in result
            assert "allowed-dir/" in result
            assert "node_modules" not in result
