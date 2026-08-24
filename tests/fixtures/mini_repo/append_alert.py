"""Fixture alert sink: appends stdin to alert.log, the way a real Slack-post script would consume it."""
import pathlib
import sys

pathlib.Path("alert.log").open("a", encoding="utf-8").write(sys.stdin.read())
