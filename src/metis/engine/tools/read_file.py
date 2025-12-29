# SPDX-FileCopyrightText: Copyright 2025 Arm Limited and/or its affiliates <open-source-office@arm.com>
# SPDX-License-Identifier: Apache-2.0

import os
import logging
import base64
import mimetypes
import re

from typing import Optional, List

from pydantic import BaseModel, Field

logger = logging.getLogger("metis")

MAX_READ_FILE_LINE = 500


class ReadFileArgs(BaseModel):
    path: str = Field(description="File path (relative to workspace directory)")
    line_range: Optional[str] = Field(
        default=f"1-{MAX_READ_FILE_LINE}",
        description="Line range specification (e.g., '1-50' or '100-150'). For multiple ranges, use comma-separated values like '1-50,100-150'.",
    )


class ReadFileTool:
    """Tool for reading file contents safely, supporting single-file reads, line ranges, images, and truncation."""

    SUPPORTED_IMAGE_EXTENSIONS = {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".svg",
        ".bmp",
        ".ico",
        ".tiff",
        ".tif",
        ".avif",
    }

    def __init__(
        self, codebase_path: str, max_read_file_line: int = MAX_READ_FILE_LINE
    ):
        self.name = "read_file"
        self.description = (
            'Request to read the contents of a file. By default, reads the first 500 lines. The tool outputs line-numbered content (e.g. "1 | const x = 1") for easy reference when creating diffs or discussing code.\n\n'
            "If you need to read more files, use multiple sequential read_file requests."
        )
        self.codebase_path = os.path.abspath(codebase_path)
        self.max_read_file_line = max_read_file_line
        self.args_schema = ReadFileArgs

    def _is_safe_path(self, path: str) -> bool:
        """Check if the path is within the codebase."""
        abs_path = os.path.abspath(path)
        return os.path.commonpath([self.codebase_path, abs_path]) == self.codebase_path

    def _is_image_file(self, path: str) -> bool:
        """Check if the file is a supported image."""
        _, ext = os.path.splitext(path.lower())
        return ext in self.SUPPORTED_IMAGE_EXTENSIONS

    def _get_file_size(self, path: str) -> int:
        """Get file size in bytes."""
        return os.path.getsize(path)

    def _read_image_as_base64(self, path: str) -> str:
        """Read image file and return base64 data URL."""
        with open(path, "rb") as f:
            data = f.read()
        mime_type, _ = mimetypes.guess_type(path)
        if not mime_type:
            mime_type = "application/octet-stream"
        encoded = base64.b64encode(data).decode("utf-8")
        return f"data:{mime_type};base64,{encoded}"

    def _extract_definitions(self, content: str) -> List[str]:
        """Extract code definitions (classes, functions, etc.) from content."""
        definitions = []
        # Simple regex patterns for common languages
        patterns = [
            r"^\s*class\s+(\w+)",  # class Name
            r"^\s*def\s+(\w+)",  # def function
            r"^\s*function\s+(\w+)",  # function name
            r"^\s*const\s+(\w+)\s*=.*=>",  # arrow functions, etc.
        ]
        for line in content.splitlines():
            for pattern in patterns:
                match = re.search(pattern, line)
                if match:
                    definitions.append(match.group(1))
        return definitions

    def _format_definitions(self, definitions: List[str]) -> str:
        """Format definitions as list."""
        if not definitions:
            return ""
        return "\n".join([f"  - {d}" for d in definitions])

    def _read_file_content(
        self, full_path: str, line_ranges: Optional[List[str]] = None
    ) -> str:
        """Read and process file content."""
        if not os.path.exists(full_path):
            return f"<error>File not found at path '{os.path.relpath(full_path, self.codebase_path)}'.</error>"

        file_size = self._get_file_size(full_path)
        if self._is_image_file(full_path):
            base64_data = self._read_image_as_base64(full_path)
            size_kb = file_size / 1024
            return f"<notice>Image file ({size_kb:.1f} KB)</notice>\n{base64_data}"

        # For large files, preview first 100KB
        LARGE_FILE_THRESHOLD = 100 * 1024  # 100KB
        if file_size > LARGE_FILE_THRESHOLD:
            with open(full_path, "rb") as f:
                preview = f.read(LARGE_FILE_THRESHOLD)
            try:
                preview_str = preview.decode("utf-8", errors="ignore")
                size_mb = file_size / (1024 * 1024)
                return f"<notice>Preview: Showing first 100KB of {size_mb:.1f}MB file. Use line_range to read specific sections.</notice>\n{preview_str}"
            except UnicodeDecodeError:
                ext = os.path.splitext(full_path)[1][1:]
                return f'<binary_file format="{ext}">Binary file - content not displayed</binary_file>'

        # Try to read as text
        try:
            with open(full_path, "r", encoding="utf-8") as f:
                content = f.read()
            # Check for binary content (null bytes)
            if "\x00" in content:
                ext = os.path.splitext(full_path)[1]
                ext = ext[1:] if ext else ""
                return f'<binary_file format="{ext}">Binary file - content not displayed</binary_file>'
        except UnicodeDecodeError:
            ext = os.path.splitext(full_path)[1]
            ext = ext[1:] if ext else ""
            return f'<binary_file format="{ext}">Binary file - content not displayed</binary_file>'

        lines = content.splitlines()
        total_lines = len(lines)

        if line_ranges:
            # Explicit ranges take precedence
            formatted_parts = []
            total_selected = 0
            for lr in line_ranges:
                try:
                    start, end = map(int, lr.split("-"))
                    if start < 1 or end < start:
                        continue
                    # Clamp end to total_lines
                    end = min(end, total_lines)
                    range_lines = lines[start - 1 : end]
                    formatted_part = "\n".join(
                        [f"{start + i} | {line}" for i, line in enumerate(range_lines)]
                    )
                    formatted_parts.append(formatted_part)
                    total_selected += len(range_lines)
                    if total_selected > self.max_read_file_line:
                        break
                except ValueError:
                    continue
            formatted = "\n\n".join(formatted_parts)
            truncated = total_selected > self.max_read_file_line
            if truncated:
                formatted = formatted.split("\n")[: self.max_read_file_line]
                formatted = "\n".join(formatted)
        else:
            # Auto-truncate if too many lines
            if total_lines > self.max_read_file_line:
                lines = lines[: self.max_read_file_line]
                truncated = True
            else:
                truncated = False
            formatted = "\n".join([f"{i + 1} | {line}" for i, line in enumerate(lines)])

        if truncated:
            shown_lines = len(formatted.split("\n")) if formatted else 0
            notice = f"<notice>Showing only {shown_lines} of {total_lines} total lines. Use line_range to read specific sections.</notice>"
            if self._is_code_file(full_path):
                defs = self._extract_definitions(content)
                if defs:
                    notice += f"\n<list_code_definition_names>\n{self._format_definitions(defs)}\n</list_code_definition_names>"
            formatted += f"\n{notice}"

        return formatted

    def _is_code_file(self, path: str) -> bool:
        """Check if file is a code file."""
        _, ext = os.path.splitext(path.lower())
        code_exts = {
            ".py",
            ".js",
            ".ts",
            ".java",
            ".c",
            ".cpp",
            ".h",
            ".hpp",
            ".cs",
            ".php",
            ".rb",
            ".go",
            ".rs",
            ".swift",
        }
        return ext in code_exts

    def run(self, path: str, line_range: Optional[str] = None) -> str:
        """Read the file contents."""
        full_path = os.path.join(self.codebase_path, path)
        if not self._is_safe_path(full_path):
            return f"Error: Access denied for '{path}'."
        # Default to first N lines if no range specified
        if line_range is None:
            line_range = f"1-{self.max_read_file_line}"
        # Parse line_range string into list
        line_ranges = [lr.strip() for lr in line_range.split(",") if lr.strip()]
        content = self._read_file_content(full_path, line_ranges)
        return f"File: {path}\n{content}"
