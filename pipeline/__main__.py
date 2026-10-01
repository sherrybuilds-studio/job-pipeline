"""python -m pipeline <command> [options]: one entry point for every step.

Each command is a module with its own main(argv); `<command> --help` shows its options.
"""
import importlib
import sys

from pipeline.profile import ProfileError

COMMANDS = {
    "daily": ("pipeline.agent", "daily run: scrape, score, letters for strong matches, Telegram digest"),
    "source": ("pipeline.source", "sourcing pass over career boards and public search; writes a report"),
    "kit": ("pipeline.kit", "build reviewer-cleared application kits from a sourcing report"),
    "review": ("pipeline.reviewer", "re-run the independent review on kit folders"),
    "lint": ("pipeline.lint", "lint generated letters and CVs (fail and report)"),
    "send-gate": ("pipeline.send_gate", "dry-run the send gate for one kit"),
    "tracker": ("pipeline.tracker", "record a send (gated) or a stage: sent | stage | show"),
    "archive": ("pipeline.archive", "archive postings, record outcomes, print the funnel"),
    "followups": ("pipeline.followup", "draft the follow-ups that are due"),
    "cv": ("pipeline.cv_builder", "render the master CV from the profile"),
    "summary": ("pipeline.summary", "print the application summary"),
}


def usage() -> str:
    width = max(len(c) for c in COMMANDS)
    lines = ["usage: python -m pipeline <command> [options]", "", "commands:"]
    lines += [f"  {name:<{width}}  {text}" for name, (_, text) in COMMANDS.items()]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(usage())
        return 0 if argv else 1
    command, rest = argv[0], argv[1:]
    if command not in COMMANDS:
        print(f"unknown command {command!r}\n\n{usage()}")
        return 2
    module = importlib.import_module(COMMANDS[command][0])
    try:
        return module.main(rest) or 0
    except ProfileError as e:
        print(f"profile error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
