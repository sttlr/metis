# SPDX-FileCopyrightText: Copyright 2025 Arm Limited and/or its affiliates <open-source-office@arm.com>
# SPDX-License-Identifier: Apache-2.0

import pytest
from unittest.mock import patch, MagicMock
import os
import tempfile

from metis.engine.tools.search_files import (
    SearchFilesTool,
    truncate_line,
    MAX_RESULTS,
    MAX_LINE_LENGTH,
    TIMEOUT_SECONDS,
)


@pytest.fixture
def temp_codebase():
    """Create a temporary directory with some test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a test file
        test_file = os.path.join(tmpdir, "test.py")
        with open(test_file, "w") as f:
            f.write(
                "class TestClass:\n    pass\n\ndef test_function():\n    return 'test'\n"
            )
        yield tmpdir


@pytest.fixture
def tool(temp_codebase):
    return SearchFilesTool(temp_codebase)


def test_search_files_valid(tool):
    result = tool.run(".", "class TestClass")
    assert "# ./test.py" in result
    assert "class TestClass:" in result
    assert "----" in result


def test_search_files_no_matches(tool):
    result = tool.run(".", "nonexistent")
    assert result == "No matches found."


def test_search_files_invalid_path(tool):
    result = tool.run("../outside", "test")
    assert "Access denied" in result


def test_search_files_no_path(tool):
    result = tool.run("", "test")
    assert "No 'path' provided" in result


def test_search_files_no_regex(tool):
    result = tool.run(".", "")
    assert "No 'regex' provided" in result


def test_search_files_with_file_pattern(tool):
    result = tool.run(".", "class", "*.py")
    assert "# ./test.py" in result
    assert "class TestClass" in result


def test_search_files_nonexistent_directory(tool):
    result = tool.run("nonexistent", "test")
    assert "does not exist" in result


def test_search_files_file_instead_of_dir(tool):
    # Create a file in temp
    file_path = os.path.join(tool.codebase_path, "file.txt")
    with open(file_path, "w") as f:
        f.write("content")
    result = tool.run("file.txt", "content")
    assert "not a directory" in result


@patch("subprocess.run")
def test_search_files_ripgrep_not_found(mock_run, tool):
    mock_run.side_effect = FileNotFoundError()
    result = tool.run(".", "test")
    assert "ripgrep (rg) is not installed" in result


@patch("subprocess.run")
def test_search_files_ripgrep_error(mock_run, tool):
    mock_result = MagicMock()
    mock_result.returncode = 2
    mock_result.stderr = "Invalid regex"
    mock_run.return_value = mock_result
    result = tool.run(".", "[invalid")
    assert "ripgrep failed" in result


@patch("subprocess.run")
def test_search_files_timeout(mock_run, tool):
    from subprocess import TimeoutExpired

    mock_run.side_effect = TimeoutExpired("rg", TIMEOUT_SECONDS)
    result = tool.run(".", "test")
    assert "timed out" in result


def test_truncate_line():
    # Should truncate lines longer than MAX_LINE_LENGTH
    long_line = "a" * 600
    truncated = truncate_line(long_line)
    assert "[truncated...]" in truncated
    assert len(truncated) < len(long_line)
    assert len(truncated) == MAX_LINE_LENGTH + len(" [truncated...]")

    # Should not truncate lines shorter than MAX_LINE_LENGTH
    short_line = "Short line of text"
    assert truncate_line(short_line) == short_line
    assert "[truncated...]" not in truncate_line(short_line)

    # Should correctly truncate a line at exactly MAX_LINE_LENGTH characters
    exact_line = "a" * MAX_LINE_LENGTH
    exact_plus_one = exact_line + "x"

    assert truncate_line(exact_line) == exact_line
    assert "[truncated...]" in truncate_line(exact_plus_one)

    # Should handle empty lines without errors
    assert truncate_line("") == ""

    # Should allow custom maximum length
    custom_length = 100
    line = "a" * (custom_length + 50)
    truncated = truncate_line(line, custom_length)
    assert len(truncated) == custom_length + len(" [truncated...]")
    assert "[truncated...]" in truncated


@patch("subprocess.run")
def test_search_files_truncation_warning(mock_run, tool):
    # Mock more matches than MAX_RESULTS
    mock_stdout = ""
    for i in range(MAX_RESULTS + 1):
        mock_stdout += f'{{"type":"match","data":{{"path":{{"text":"./test.py"}},"line_number":{i + 1},"lines":{{"text":"match {i}\\n"}}}}}}\n'
    mock_run.return_value = MagicMock(returncode=0, stdout=mock_stdout, stderr="")

    result = tool.run(".", "test")
    assert f"Showing first {MAX_RESULTS} of {MAX_RESULTS + 1}+ results" in result
