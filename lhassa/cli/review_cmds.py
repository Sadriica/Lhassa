# SPDX-License-Identifier: AGPL-3.0-or-later
"""``lhassa review``: deterministic pre-flight review (commit range, message, text)."""

from __future__ import annotations

import typer

review_app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Deterministic pre-flight review of commits, messages and text (no LLM).",
)


@review_app.command("commit-range")
def commit_range_cmd(
    rng: str = typer.Argument(..., metavar="RANGE", help="e.g. origin/main..HEAD"),
    repo: str = typer.Option(".", help="Repository path."),
    as_json: bool = typer.Option(False, "--json"),
    strict: bool = typer.Option(False, "--strict", help="Exit 1 on findings."),
    fail_on: str = typer.Option("warn", help="warn or critical (with --strict)."),
    no_log: bool = typer.Option(False, "--no-log"),
) -> None:
    """Review every commit in RANGE: authorship, message, and added lines."""
    from lhassa.review.cli import run_commit_range

    raise typer.Exit(run_commit_range(rng, repo, as_json=as_json, strict=strict,
                                      fail_on=fail_on, log=not no_log))


@review_app.command("message")
def message_cmd(
    file: str = typer.Argument(..., help="A commit message file."),
    as_json: bool = typer.Option(False, "--json"),
    strict: bool = typer.Option(False, "--strict"),
    fail_on: str = typer.Option("warn"),
    no_log: bool = typer.Option(False, "--no-log"),
) -> None:
    """Review a commit message file."""
    from lhassa.review.cli import run_message

    raise typer.Exit(run_message(file, as_json=as_json, strict=strict,
                                 fail_on=fail_on, log=not no_log))


@review_app.command("text")
def text_cmd(
    file: str = typer.Argument(..., help="A text file to review."),
    as_json: bool = typer.Option(False, "--json"),
    strict: bool = typer.Option(False, "--strict"),
    fail_on: str = typer.Option("warn"),
    no_log: bool = typer.Option(False, "--no-log"),
) -> None:
    """Review public text: em dashes, secrets, configured names."""
    from lhassa.review.cli import run_text

    raise typer.Exit(run_text(file, as_json=as_json, strict=strict,
                              fail_on=fail_on, log=not no_log))
