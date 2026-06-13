# -*- coding: utf-8 -*-
# MinoruDouga ランチャー — Resolve のスクリプトメニューに配置するファイル。
# 本体はリポジトリ側 (src/minoru_douga.py) を読み込むので、
# 本体を編集しても Resolve の再起動なしで反映される。
# 実行ログ・エラーは minoru_douga.log (リポジトリ直下) に書き出す。
import importlib
import sys
import time
import traceback

REPO = r"C:\Users\rnmgy\dev\MinoruDouga"
REPO_SRC = REPO + r"\src"
LOG = REPO + r"\minoru_douga.log"


def _log(text):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + text + "\n")
    except Exception:
        pass


_log("=== launcher start === python=%s exe=%s" % (sys.version.split()[0], sys.executable))
_log("globals: resolve=%s fusion=%s bmd=%s" % (
    "resolve" in dir() or "resolve" in globals(),
    "fusion" in globals(),
    "bmd" in globals(),
))

try:
    if REPO_SRC not in sys.path:
        sys.path.insert(0, REPO_SRC)
    import minoru_douga
    importlib.reload(minoru_douga)
    _log("module loaded, calling run()")
    minoru_douga.run(globals(), log_file=LOG)
    _log("run() finished")
except Exception:
    _log("ERROR:\n" + traceback.format_exc())
    raise
