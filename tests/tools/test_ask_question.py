# SPDX-FileCopyrightText: Copyright 2025 Arm Limited and/or its affiliates <open-source-office@arm.com>
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import MagicMock, patch

from metis.engine.tools.ask_question import AskQuestionTool, AskQuestionArgs


class TestAskQuestionTool:
    """Test cases for AskQuestionTool class."""

    def test_tool_initialization(self):
        """Test tool initialization."""
        tool = AskQuestionTool()
        assert tool.name == "ask_question"
        assert "ask a question" in tool.description.lower()
        assert "codebase" in tool.description.lower()
        assert tool.args_schema == AskQuestionArgs

    def test_run_success(self):
        """Test successful run with mocked engine."""
        tool = AskQuestionTool()
        mock_engine = MagicMock()
        mock_engine.ask_question.return_value = {"answer": "This is the answer"}

        result = tool.run(engine=mock_engine, question="What is this code?")

        assert result == "This is the answer"
        mock_engine.ask_question.assert_called_once_with("What is this code?")

    def test_run_no_answer(self):
        """Test run when ask_question returns dict without answer."""
        tool = AskQuestionTool()
        mock_engine = MagicMock()
        mock_engine.ask_question.return_value = {"code": "some code"}

        result = tool.run(engine=mock_engine, question="Test question")

        assert result == "No answer provided"

    def test_run_exception(self):
        """Test run when ask_question raises exception."""
        tool = AskQuestionTool()
        mock_engine = MagicMock()
        mock_engine.ask_question.side_effect = Exception("Test error")

        result = tool.run(engine=mock_engine, question="Test question")

        assert "Error asking question: Test error" in result

    def test_args_schema(self):
        """Test that args_schema is properly defined."""
        args = AskQuestionArgs(question="Test question")
        assert args.question == "Test question"