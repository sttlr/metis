# SPDX-FileCopyrightText: Copyright 2025 Arm Limited and/or its affiliates <open-source-office@arm.com>
# SPDX-License-Identifier: Apache-2.0

from typing import Any

from pydantic import BaseModel, Field


class AskQuestionArgs(BaseModel):
    question: str = Field(description="The question to ask about the codebase")


class AskQuestionTool:
    """Tool for asking questions about the codebase using the Ask graph."""

    def __init__(self):
        self.name = "ask_question"
        self.description = (
            "Request to ask a question about the codebase and get an answer using AI analysis. "
            "Use this when you need clarification or additional information about code, architecture, or functionality."
        )
        self.args_schema = AskQuestionArgs

    def run(self, engine: Any, question: str) -> str:
        """Ask the question using the Ask graph and return the answer."""
        try:
            result = engine.ask_question(question)
            return result.get("answer", "No answer provided")
        except Exception as e:
            return f"Error asking question: {str(e)}"