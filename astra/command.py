"""Fail-closed interface placeholder for future DPN command authorization."""


class CommandDenied(PermissionError):
    pass


def execute(action: str, arguments: dict):
    raise CommandDenied("ASTRA COMMAND is disabled. No DPN system action can run in v0.1.")
