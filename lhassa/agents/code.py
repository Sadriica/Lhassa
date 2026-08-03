# SPDX-License-Identifier: AGPL-3.0-or-later
"""CodeAgent — specialised in programming, debugging, and code analysis."""

from lhassa.agents.base import BaseAgent


class CodeAgent(BaseAgent):
    name = "code"
    system_prompt = """You are an expert software engineer and coding assistant.
You write clean, idiomatic code. You debug efficiently by reading error messages carefully.
When asked to write code, provide complete, working implementations.
When analyzing code, be specific about issues and how to fix them.
Always prefer concise, direct answers over verbose explanations."""
