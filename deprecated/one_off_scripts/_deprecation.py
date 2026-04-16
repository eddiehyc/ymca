from __future__ import annotations

import sys


def print_deprecation_warning(script_name: str) -> None:
    print(
        (
            f"Warning: deprecated/one_off_scripts/{script_name} is a deprecated one-off "
            "helper and is not part of the supported YMCA CLI workflow."
        ),
        file=sys.stderr,
    )
