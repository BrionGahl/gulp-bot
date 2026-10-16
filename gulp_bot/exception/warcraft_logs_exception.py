"""Warcraft Logs requests that got no usable answer."""


class WarcraftLogsError(Exception):
    """A request to Warcraft Logs that got no usable answer. The message is shown to the officer."""


class ReportRequestProblem(ValueError):
    """What an officer asked for can't be worked out (no report code in it, no uploader registered). The
    message says why."""
