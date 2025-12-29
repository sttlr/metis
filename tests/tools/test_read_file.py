# SPDX-FileCopyrightText: Copyright 2025 Arm Limited and/or its affiliates <open-source-office@arm.com>
# SPDX-License-Identifier: Apache-2.0

import pytest
import tempfile
import os
from metis.engine.tools.read_file import ReadFileTool


@pytest.fixture
def temp_codebase():
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create test files
        os.makedirs(os.path.join(tmpdir, "src"))
        with open(os.path.join(tmpdir, "src/index.ts"), "w") as f:
            f.write("console.log('hello');\nconsole.log('world');\n")
        with open(os.path.join(tmpdir, "README.md"), "w") as f:
            f.write("# Title\n\nContent\n")
        # Create a large file
        large_content = "\n".join([f"line {i}" for i in range(600)])
        with open(os.path.join(tmpdir, "large.txt"), "w") as f:
            f.write(large_content)
        # Create code file for definitions
        with open(os.path.join(tmpdir, "test.py"), "w") as f:
            f.write(
                "class MyClass:\n    def method(self):\n        pass\n\ndef function():\n    pass\n"
            )
        yield tmpdir


def test_read_file_legacy_single(temp_codebase):
    tool = ReadFileTool(temp_codebase)
    result = tool.run(path="src/index.ts")
    assert "console.log('hello');" in result
    assert "File: src/index.ts" in result
    assert "1 | console.log('hello');" in result


def test_read_file_with_range(temp_codebase):
    tool = ReadFileTool(temp_codebase)
    result = tool.run(path="src/index.ts", line_range="1-1")
    assert "1 | console.log('hello');" in result
    assert "2 | console.log('world');" not in result


def test_read_file_truncation(temp_codebase):
    tool = ReadFileTool(temp_codebase, max_read_file_line=10)
    result = tool.run(path="large.txt")
    # Now defaults to 1-10, so reads exactly 10 lines, no truncation notice
    assert "1 | line 0" in result
    assert "10 | line 9" in result
    assert "Showing only" not in result  # no truncation
    assert "<list_code_definition_names>" not in result  # not code file


def test_read_file_code_definitions(temp_codebase):
    tool = ReadFileTool(temp_codebase, max_read_file_line=1)  # reads 1-1, no truncation
    result = tool.run(path="test.py")
    assert (
        "<list_code_definition_names>" not in result
    )  # no truncation, so no definitions shown
    assert "class MyClass:" in result


def test_read_file_errors(temp_codebase):
    tool = ReadFileTool(temp_codebase)
    result = tool.run(path="nonexistent.txt")
    assert "File not found" in result


def test_read_file_binary(temp_codebase):
    # Create a binary file
    binary_path = os.path.join(temp_codebase, "binary.bin")
    with open(binary_path, "wb") as f:
        f.write(b"\x00\x01\x02\x03")
    tool = ReadFileTool(temp_codebase)
    result = tool.run(path="binary.bin")
    assert "<binary_file" in result
    assert "Binary file - content not displayed" in result


def test_read_file_image(temp_codebase):
    # For image, since we can't create real image easily, mock or skip
    # But in code, it checks extension
    image_path = os.path.join(temp_codebase, "test.png")
    with open(image_path, "w") as f:
        f.write("fake png")
    tool = ReadFileTool(temp_codebase)
    result = tool.run(path="test.png")
    assert "<notice>Image file" in result
    assert "data:" in result  # base64


def test_read_file_large_file_preview(temp_codebase):
    # Create a file larger than 100KB
    large_content = "x" * (101 * 1024)
    large_path = os.path.join(temp_codebase, "huge.txt")
    with open(large_path, "w") as f:
        f.write(large_content)
    tool = ReadFileTool(temp_codebase)
    result = tool.run(path="huge.txt")
    assert "Preview: Showing first 100KB" in result
