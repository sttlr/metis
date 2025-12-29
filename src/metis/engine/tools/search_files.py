# SPDX-FileCopyrightText: Copyright 2025 Arm Limited and/or its affiliates <open-source-office@arm.com>
# SPDX-License-Identifier: Apache-2.0

import os
import subprocess
import json
import logging
from typing import Optional

from pydantic import BaseModel, Field

logger = logging.getLogger("metis")

MAX_LINE_LENGTH = 500
MAX_RESULTS = 300
NUM_CONTEXT_LINES = 1
TIMEOUT_SECONDS = 30


def truncate_line(line: str, max_length: int = MAX_LINE_LENGTH) -> str:
    """Truncate a line if it exceeds max_length."""
    if len(line) <= max_length:
        return line
    return line[:max_length] + " [truncated...]"


class SearchFilesArgs(BaseModel):
    path: str = Field(
        description="Directory path to search recursively, relative to workspace"
    )
    regex: str = Field(
        description="Rust-compatible regular expression pattern to match"
    )
    file_pattern: Optional[str] = Field(
        default=None, description="Optional glob pattern to filter files (e.g., '*.py')"
    )


class SearchFilesTool:
    """Tool for searching files using ripgrep regex search."""

    def __init__(self, codebase_path: str):
        self.name = "search_files"
        self.description = (
            "Request to perform a regex search across files in a specified directory, providing context-rich results. "
            "This tool searches for patterns or specific content across multiple files, displaying each match with encapsulating context. "
            "Craft your regex patterns carefully to balance specificity and flexibility. Use this tool to find code patterns, TODO comments, "
            "function definitions, or any text-based information across the project. The results include surrounding context, so analyze "
            "the surrounding code to better understand the matches."
        )
        self.codebase_path = os.path.abspath(codebase_path)
        self.args_schema = SearchFilesArgs

    def _is_safe_path(self, path: str) -> bool:
        """Check if the path is within the codebase."""
        abs_path = os.path.abspath(path)
        return os.path.commonpath([self.codebase_path, abs_path]) == self.codebase_path

    def run(self, path: str, regex: str, file_pattern: Optional[str] = None) -> str:
        """Perform regex search using ripgrep."""
        if not path:
            return "Error: No 'path' provided"
        if not regex:
            return "Error: No 'regex' provided"
        full_path = os.path.join(self.codebase_path, path)
        if not self._is_safe_path(full_path):
            return (
                f"Error: Access denied for '{path}'. File path is outside the codebase."
            )
        if not os.path.exists(full_path):
            return f"Error: Path '{path}' does not exist."
        if not os.path.isdir(full_path):
            return f"Error: Path '{path}' is not a directory."

        # Build ripgrep command
        cmd = [
            "rg",
            "--json",
            "--line-number",
            "--context",
            str(NUM_CONTEXT_LINES),
            "--no-messages",
            # "--hidden",
            "--regexp",
            regex,
            path,
        ]
        if file_pattern:
            cmd.extend(["--glob", file_pattern])

        try:
            result = subprocess.run(
                cmd,
                cwd=self.codebase_path,
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SECONDS,  # Timeout to prevent hanging
            )
        except FileNotFoundError:
            return "Error: ripgrep (rg) is not installed."
        except subprocess.TimeoutExpired:
            return "Error: Search timed out after 30 seconds."
        except Exception as e:
            logger.error(f"Error running ripgrep: {e}")
            return f"Error running search: {e}"

        if result.returncode not in (0, 1):  # 0 = matches found, 1 = no matches
            return (
                f"Error: ripgrep failed with code {result.returncode}: {result.stderr}"
            )

        # Parse JSON output
        file_lines = {}  # file_path -> list of (line_number, text)
        match_positions = []  # list of (file_path, line_number)

        for line in result.stdout.strip().splitlines():
            try:
                data = json.loads(line)
                data_type = data.get("type")
                if data_type in ("match", "context"):
                    match_data = data["data"]
                    file_path = match_data["path"]["text"]
                    line_number = match_data["line_number"]
                    text = match_data["lines"]["text"].rstrip("\n")
                    if file_path not in file_lines:
                        file_lines[file_path] = []
                    file_lines[file_path].append((line_number, text))
                    if data_type == "match":
                        match_positions.append((file_path, line_number))
            except json.JSONDecodeError:
                continue  # Skip invalid JSON lines

        # Limit to MAX_RESULTS results
        total_matches = len(match_positions)
        if total_matches > MAX_RESULTS:
            match_positions = match_positions[:MAX_RESULTS]

        # Rebuild file_lines with only files that have limited matches
        limited_file_lines = {}
        limited_files = set(file_path for file_path, _ in match_positions)
        for file_path in limited_files:
            limited_file_lines[file_path] = file_lines[file_path]

        # Format results by file with merged contexts
        results = []
        for file_path in sorted(limited_file_lines.keys()):
            lines = sorted(limited_file_lines[file_path], key=lambda x: x[0])
            if not lines:
                continue
            block = [f"# {file_path}"]
            for ln, txt in lines:
                block.append(f"{ln:3} | {truncate_line(txt)}")
            block.append("----")
            results.append("\n".join(block))

        if not results:
            return "No matches found."

        output = "\n\n".join(results)
        if total_matches > MAX_RESULTS:
            output += f"\n\n# Showing first {MAX_RESULTS} of {total_matches}+ results. Use a more specific search if necessary."

        return output
