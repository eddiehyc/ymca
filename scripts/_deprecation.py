from __future__ import annotations

import sys


def print_deprecation_warning(script_name: str) -> None:
    print(
        (
            f"Warning: scripts/{script_name} is deprecated. "
            "It is kept only for compatibility as a one-off helper and is not part of "
            "the supported YMCA CLI workflow."
        ),
        file=sys.stderr,
    )
