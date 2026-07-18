# -*- coding: utf-8 -*-
import os
import sys
import traceback


app_root = os.path.join(
    os.environ.get("LOCALAPPDATA", ""),
    "MinoruStudio",
    "resolve_adapter",
)
if not os.path.isdir(app_root):
    raise RuntimeError(
        "MinoruStudio Resolve adapter is not installed: " + app_root
    )
if app_root not in sys.path:
    sys.path.insert(0, app_root)

from minoru_studio_resolve.entry import run


try:
    exit_code = run(globals())
    if exit_code:
        raise RuntimeError(
            "MinoruStudio adapter exited with {0}".format(exit_code)
        )
except Exception:
    traceback.print_exc()
    raise
