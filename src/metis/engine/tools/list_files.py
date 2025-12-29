# SPDX-FileCopyrightText: Copyright 2025 Arm Limited and/or its affiliates <open-source-office@arm.com>
# SPDX-License-Identifier: Apache-2.0

import os
import subprocess
import pathspec
from typing import List, Tuple, Optional, Set
import logging

from pydantic import BaseModel, Field

logger = logging.getLogger("metis")

# Constants
DIRS_TO_IGNORE = [
    "node_modules",
    "__pycache__",
    "env",
    "venv",
    "target/dependency",
    "build/dependencies",
    "dist",
    "out",
    "bundle",
    "vendor",
    "tmp",
    "temp",
    "deps",
    "pkg",
    "Pods",
    ".git",
    ".*",
]
CRITICAL_IGNORE_PATTERNS: Set[str] = {
    "node_modules",
    ".git",
    "__pycache__",
    "venv",
    "env",
}


class ScanContext:
    """Context object for directory scanning operations"""

    def __init__(
        self,
        is_target_dir: bool,
        inside_explicit_hidden_target: bool,
        base_path: str,
        ignore_instance: pathspec.PathSpec,
    ):
        self.is_target_dir = is_target_dir
        self.inside_explicit_hidden_target = inside_explicit_hidden_target
        self.base_path = base_path
        self.ignore_instance = ignore_instance


def list_files(dir_path: str, recursive: bool, limit: int) -> Tuple[List[str], bool]:
    """
    List files in a directory, with optional recursive traversal

    Args:
        dir_path: Directory path to list files from
        recursive: Whether to recursively list files in subdirectories
        limit: Maximum number of files to return

    Returns:
        Tuple of [file paths array, whether the limit was reached]
    """
    # Early return for limit of 0
    if limit == 0:
        return [], False

    # Handle special directories
    special_result = _handle_special_directories(dir_path)
    if special_result:
        return special_result

    # Get ripgrep path - assuming rg is available
    rg_path = "rg"  # Assume ripgrep is installed as 'rg'

    if not recursive:
        # For non-recursive, use existing approach
        files = _list_files_with_ripgrep(rg_path, dir_path, False, limit)
        ignore_instance = _create_ignore_instance(dir_path)
        # Filter files that are ignored
        filtered_files = []
        for f in files:
            rel_path = os.path.relpath(f, dir_path)
            if not ignore_instance.match_file(rel_path):
                filtered_files.append(f)
        remaining_limit = max(0, limit - len(filtered_files))
        directories = _list_filtered_directories(
            dir_path, False, ignore_instance, remaining_limit
        )
        return _format_and_combine_results(filtered_files, directories, limit)

    # For recursive mode
    files = _list_files_with_ripgrep(rg_path, dir_path, True, limit)
    ignore_instance = _create_ignore_instance(dir_path)
    # Filter files that are ignored
    filtered_files = []
    for f in files:
        rel_path = os.path.relpath(f, dir_path)
        if not ignore_instance.match_file(rel_path):
            filtered_files.append(f)
    remaining_limit = max(0, limit - len(filtered_files))
    directories = _list_filtered_directories(
        dir_path, True, ignore_instance, remaining_limit
    )

    # Combine and check if we hit the limits
    results, limit_reached = _format_and_combine_results(files, directories, limit)

    # If we hit the limit, ensure all first-level directories are included
    if limit_reached:
        first_level_dirs = _get_first_level_directories(dir_path, ignore_instance)
        return _ensure_first_level_directories_included(
            results, first_level_dirs, limit
        )

    return results, limit_reached


def _get_first_level_directories(
    dir_path: str, ignore_instance: pathspec.PathSpec
) -> List[str]:
    """Get only the first-level directories in a path"""
    absolute_path = os.path.abspath(dir_path)
    directories: List[str] = []

    try:
        entries = os.scandir(absolute_path)
        for entry in entries:
            if entry.is_dir() and not entry.is_symlink():
                full_dir_path = os.path.join(absolute_path, entry.name)
                context = ScanContext(
                    is_target_dir=False,
                    inside_explicit_hidden_target=False,
                    base_path=dir_path,
                    ignore_instance=ignore_instance,
                )
                if _should_include_directory(entry.name, full_dir_path, context):
                    formatted_path = (
                        full_dir_path
                        if full_dir_path.endswith(os.sep)
                        else full_dir_path + os.sep
                    )
                    directories.append(formatted_path)
    except Exception as e:
        logger.warning(f"Could not read directory {absolute_path}: {e}")

    return directories


def _ensure_first_level_directories_included(
    results: List[str], first_level_dirs: List[str], limit: int
) -> Tuple[List[str], bool]:
    """Ensure all first-level directories are included in the results"""
    # Create a set of existing paths for quick lookup
    existing_paths = set(results)

    # Find missing first-level directories
    missing_dirs = [d for d in first_level_dirs if d not in existing_paths]

    if not missing_dirs:
        # All first-level directories are already included
        return results, True

    # We need to make room for the missing directories
    items_to_remove = min(len(missing_dirs), len(results))
    adjusted_results = results[:-items_to_remove] if items_to_remove > 0 else results

    # Add the missing directories at the beginning
    final_results = first_level_dirs + adjusted_results
    return final_results[:limit], True


def _handle_special_directories(dir_path: str) -> Optional[Tuple[List[str], bool]]:
    """Handle special directories (root, home) that should not be fully listed"""
    absolute_path = os.path.abspath(dir_path)

    # Do not allow listing files in root directory
    root = (
        os.path.abspath(os.sep)
        if os.name != "nt"
        else os.path.splitdrive(absolute_path)[0]
    )
    try:
        if os.path.exists(absolute_path) and os.path.samefile(absolute_path, root):
            return [root], False
    except (OSError, FileNotFoundError):
        pass  # Path doesn't exist, continue

    # Do not allow listing files in home directory
    home_dir = os.path.expanduser("~")
    try:
        if os.path.exists(absolute_path) and os.path.samefile(absolute_path, home_dir):
            return [home_dir], False
    except (OSError, FileNotFoundError):
        pass  # Path doesn't exist, continue

    return None


def _list_files_with_ripgrep(
    rg_path: str, dir_path: str, recursive: bool, limit: int
) -> List[str]:
    """List files using ripgrep with appropriate arguments"""
    rg_args = _build_ripgrep_args(dir_path, recursive)

    relative_paths = _exec_ripgrep(rg_path, rg_args, limit)

    # Convert relative paths from ripgrep to absolute paths
    absolute_path = os.path.abspath(dir_path)
    return [
        os.path.abspath(os.path.join(absolute_path, rel_path))
        for rel_path in relative_paths
    ]


def _build_ripgrep_args(dir_path: str, recursive: bool) -> List[str]:
    """Build appropriate ripgrep arguments based on whether we're doing a recursive search"""
    # Base arguments to list files
    args = ["--files", "--hidden", "--follow"]

    if recursive:
        return args + _build_recursive_args(dir_path) + [dir_path]
    else:
        return args + _build_non_recursive_args() + [dir_path]


def _build_recursive_args(dir_path: str) -> List[str]:
    """Build ripgrep arguments for recursive directory traversal"""
    args: List[str] = []

    # Check if we're explicitly targeting a hidden directory
    normalized_path = os.path.normpath(dir_path)
    path_parts = [p for p in normalized_path.split(os.sep) if p]
    is_targeting_hidden_dir = any(p.startswith(".") for p in path_parts)

    # Get the target directory name to check if it's in the ignore list
    target_dir_name = os.path.basename(dir_path)
    is_target_in_ignore_list = target_dir_name in DIRS_TO_IGNORE

    # If targeting a hidden directory or a directory in the ignore list,
    # use special handling to ensure all files are shown
    if is_targeting_hidden_dir or is_target_in_ignore_list:
        args.extend(["--no-ignore-vcs", "--no-ignore"])
        args.extend(["-g", "*", "-g", "**/*"])

    # Apply directory exclusions for recursive searches
    for d in DIRS_TO_IGNORE:
        if d == ".*":
            if not is_targeting_hidden_dir:
                args.extend(["-g", "!**/.*/**"])
            continue

        if d == target_dir_name and is_target_in_ignore_list:
            continue

        args.extend(["-g", f"!**/{d}/**"])

    return args


def _build_non_recursive_args() -> List[str]:
    """Build ripgrep arguments for non-recursive directory listing"""
    args: List[str] = []

    # For non-recursive, limit to the current directory level
    args.extend(["-g", "*", "--maxdepth", "1"])

    # Apply directory exclusions for non-recursive searches
    for d in DIRS_TO_IGNORE:
        if d == ".*":
            continue
        else:
            args.extend(["-g", f"!{d}", "-g", f"!{d}/**"])

    return args


def _create_ignore_instance(dir_path: str) -> pathspec.PathSpec:
    """Create an ignore instance that handles .gitignore files properly"""
    all_patterns = []

    absolute_path = os.path.abspath(dir_path)

    # Find all .gitignore files from the target directory up to the root
    gitignore_files = _find_gitignore_files(absolute_path)

    # Add patterns from all .gitignore files
    for gitignore_file in gitignore_files:
        try:
            with open(gitignore_file, "r", encoding="utf-8") as f:
                content = f.read()
            all_patterns.extend(content.splitlines())
        except Exception as e:
            logger.warning(f"Could not read .gitignore at {gitignore_file}: {e}")

    # Always ignore .gitignore files themselves
    all_patterns.append(".gitignore")

    return pathspec.PathSpec.from_lines("gitwildmatch", all_patterns)


def _find_gitignore_files(start_path: str) -> List[str]:
    """Find all .gitignore files from the given directory up to the workspace root"""
    gitignore_files: List[str] = []
    current_path = start_path

    while current_path and current_path != os.path.dirname(current_path):
        gitignore_path = os.path.join(current_path, ".gitignore")

        if os.path.exists(gitignore_path):
            gitignore_files.append(gitignore_path)

        parent_path = os.path.dirname(current_path)
        if parent_path == current_path:
            break
        current_path = parent_path

    # Return in reverse order (root .gitignore first)
    return gitignore_files[::-1]


def _list_filtered_directories(
    dir_path: str,
    recursive: bool,
    ignore_instance: pathspec.PathSpec,
    limit: Optional[int] = None,
) -> List[str]:
    """List directories with appropriate filtering"""
    absolute_path = os.path.abspath(dir_path)
    directories: List[str] = []
    dir_count = 0
    effective_limit = limit if limit is not None else float("inf")

    is_explicit_hidden_target = os.path.basename(absolute_path).startswith(".")

    initial_context = ScanContext(
        is_target_dir=is_explicit_hidden_target,
        inside_explicit_hidden_target=is_explicit_hidden_target,
        base_path=dir_path,
        ignore_instance=ignore_instance,
    )

    def scan_directory(current_path: str, context: ScanContext) -> bool:
        nonlocal dir_count
        if dir_count >= effective_limit:
            return True

        try:
            entries = os.scandir(current_path)
            for entry in entries:
                if dir_count >= effective_limit:
                    return True

                if entry.is_dir() and not entry.is_symlink():
                    dir_name = entry.name
                    full_dir_path = os.path.join(current_path, dir_name)

                    subdir_context = ScanContext(
                        is_target_dir=False,
                        inside_explicit_hidden_target=context.inside_explicit_hidden_target,
                        base_path=context.base_path,
                        ignore_instance=context.ignore_instance,
                    )

                    if _should_include_directory(
                        dir_name, full_dir_path, subdir_context
                    ):
                        formatted_path = (
                            full_dir_path
                            if full_dir_path.endswith(os.sep)
                            else full_dir_path + os.sep
                        )
                        directories.append(formatted_path)
                        dir_count += 1

                        if dir_count >= effective_limit:
                            return True

                    is_hidden_dir = dir_name.startswith(".")
                    should_recurse_into_dir = True
                    if context.inside_explicit_hidden_target:
                        should_recurse_into_dir = (
                            dir_name not in CRITICAL_IGNORE_PATTERNS
                        )
                    else:
                        should_recurse_into_dir = not _is_directory_explicitly_ignored(
                            dir_name
                        )

                    should_recurse = (
                        recursive
                        and should_recurse_into_dir
                        and not (
                            is_hidden_dir
                            and ".*" in DIRS_TO_IGNORE
                            and not context.is_target_dir
                            and not context.inside_explicit_hidden_target
                        )
                    )
                    if should_recurse:
                        new_inside_explicit_hidden_target = (
                            context.inside_explicit_hidden_target
                            or (is_hidden_dir and context.is_target_dir)
                        )
                        new_context = ScanContext(
                            is_target_dir=False,
                            inside_explicit_hidden_target=new_inside_explicit_hidden_target,
                            base_path=context.base_path,
                            ignore_instance=context.ignore_instance,
                        )
                        if scan_directory(full_dir_path, new_context):
                            return True
        except Exception as e:
            logger.warning(f"Could not read directory {current_path}: {e}")

        return False

    scan_directory(absolute_path, initial_context)
    return directories


def _should_include_directory(
    dir_name: str, full_dir_path: str, context: ScanContext
) -> bool:
    """Determine if a directory should be included in results based on filters"""
    if context.is_target_dir:
        return _should_include_target_directory(dir_name)

    if context.inside_explicit_hidden_target:
        return _should_include_inside_hidden_target(dir_name, full_dir_path, context)

    return _should_include_regular_directory(dir_name, full_dir_path, context)


def _should_include_target_directory(dir_name: str) -> bool:
    """Check if a target directory should be included"""
    non_hidden_ignore_patterns = [p for p in DIRS_TO_IGNORE if p != ".*"]
    return not _matches_ignore_pattern(dir_name, non_hidden_ignore_patterns)


def _should_include_inside_hidden_target(
    dir_name: str, full_dir_path: str, context: ScanContext
) -> bool:
    """Check if a directory inside an explicitly targeted hidden directory should be included"""
    if dir_name in CRITICAL_IGNORE_PATTERNS:
        return False

    return not _is_ignored_by_gitignore(
        full_dir_path, context.base_path, context.ignore_instance
    )


def _should_include_regular_directory(
    dir_name: str, full_dir_path: str, context: ScanContext
) -> bool:
    """Check if a regular directory should be included"""
    non_hidden_ignore_patterns = [p for p in DIRS_TO_IGNORE if p != ".*"]
    if _matches_ignore_pattern(dir_name, non_hidden_ignore_patterns):
        return False

    return not _is_ignored_by_gitignore(
        full_dir_path, context.base_path, context.ignore_instance
    )


def _matches_ignore_pattern(dir_name: str, patterns: List[str]) -> bool:
    """Check if a directory matches any of the given patterns"""
    for pattern in patterns:
        if pattern == dir_name or (
            "/" in pattern and pattern.split("/")[0] == dir_name
        ):
            return True
    return False


def _is_ignored_by_gitignore(
    full_dir_path: str, base_path: str, ignore_instance: pathspec.PathSpec
) -> bool:
    """Check if a directory is ignored by gitignore"""
    relative_path = os.path.relpath(full_dir_path, base_path)
    normalized_path = relative_path.replace(os.sep, "/")
    return ignore_instance.match_file(normalized_path) or ignore_instance.match_file(
        normalized_path + "/"
    )


def _is_path_in_ignored_directory(file_path: str) -> bool:
    """Check if a file path should be ignored based on the DIRS_TO_IGNORE patterns."""
    # Normalize path separators
    normalized_path = file_path.replace(os.sep, "/")
    path_parts = normalized_path.split("/")

    # Check each directory in the path against DIRS_TO_IGNORE
    for part in path_parts:
        # Skip empty parts (from leading or trailing slashes)
        if not part:
            continue

        # Handle the ".*" pattern for hidden directories
        if ".*" in DIRS_TO_IGNORE and part.startswith(".") and part != ".":
            return True

        # Check for exact matches
        if part in DIRS_TO_IGNORE:
            return True

    # Check if path contains any ignored directory pattern
    for d in DIRS_TO_IGNORE:
        if d == ".*":
            # Already handled above
            continue

        # Check if the directory appears in the path
        if f"/{d}/" in normalized_path:
            return True

    return False


def _is_directory_explicitly_ignored(dir_name: str) -> bool:
    """Check if a directory is in our explicit ignore list"""
    return dir_name in DIRS_TO_IGNORE or (
        dir_name.startswith(".") and ".*" in DIRS_TO_IGNORE
    )


def _format_and_combine_results(
    files: List[str], directories: List[str], limit: int
) -> Tuple[List[str], bool]:
    """Combine file and directory results and format them properly"""
    # Combine
    all_paths = directories + files

    # Deduplicate
    unique_paths = list(set(all_paths))

    # Sort: directories first, then files
    unique_paths.sort(key=lambda p: (not p.endswith(os.sep), p))

    trimmed_paths = unique_paths[:limit]
    return trimmed_paths, len(trimmed_paths) >= limit


def _exec_ripgrep(rg_path: str, args: List[str], limit: int) -> List[str]:
    """Execute ripgrep command and return list of files"""
    try:
        result = subprocess.run(
            [rg_path] + args, capture_output=True, text=True, timeout=10.0
        )
        lines = result.stdout.strip().split("\n")
        results = [line for line in lines if line.strip()]
        return results[:limit]
    except subprocess.TimeoutExpired:
        logger.warning("ripgrep timed out")
        return []
    except Exception as e:
        logger.error(f"ripgrep error: {e}")
        return []


class ListFilesArgs(BaseModel):
    """Arguments for list files tool"""

    path: str = Field(
        description="The path of the directory to list contents for (relative to the current workspace directory)"
    )
    recursive: bool = Field(
        default=False,
        description="Whether to list files recursively. Use true for recursive listing, false or omit for top-level only.",
    )


class ListFilesTool:
    """Tool for listing files and directories with ripgrep-based implementation."""

    def __init__(self, codebase_path: str, metisignore_spec=None):
        self.name = "list_files"
        self.description = (
            "Request to list files and directories within the specified directory. "
            "If recursive is true, it will list all files and directories recursively. "
            "If recursive is false or not provided, it will only list the top-level contents. "
            "This implementation uses ripgrep for efficient file listing."
        )
        self.codebase_path = os.path.abspath(codebase_path)
        self.args_schema = ListFilesArgs
        self.metisignore_spec = metisignore_spec

    def _is_safe_path(self, path: str) -> bool:
        """Check if the path is within the codebase."""
        abs_path = os.path.abspath(path)
        return os.path.commonpath([self.codebase_path, abs_path]) == self.codebase_path

    def run(self, path: str, recursive: bool = False) -> str:
        """List files and directories."""
        if not path:
            return "Error: No path provided"
        full_path = os.path.join(self.codebase_path, path)
        if not self._is_safe_path(full_path):
            return "Error: Access denied. Path is outside the codebase."
        if not os.path.exists(full_path):
            return f"Error: Path '{path}' does not exist."
        if not os.path.isdir(full_path):
            return f"Error: Path '{path}' is not a directory."

        try:
            # Use limit of 200 like the original
            files, limit_reached = list_files(full_path, recursive, 200)

            # Filter out files/directories that are ignored by metisignore
            filtered_files = []
            for f in files:
                rel_path = os.path.relpath(f, self.codebase_path)
                # Ensure directories have trailing slash
                if f.endswith(os.sep) and not rel_path.endswith(os.sep):
                    rel_path += os.sep
                if self.metisignore_spec and self.metisignore_spec.match_file(rel_path):
                    continue
                filtered_files.append(rel_path)

            result = f'Listing directories and files for path "{path}":\n' + "\n".join(
                filtered_files
            )
            if limit_reached:
                result += "\n\nFile listing truncated. Showing first 200 files."
            return result
        except Exception as e:
            logger.error(f"Error listing files: {e}")
            return f"Error listing files: {e}"
