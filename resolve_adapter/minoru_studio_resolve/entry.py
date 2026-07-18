from minoru_studio_resolve.ui import launch, show_error


def run(resolve_globals):
    resolve = resolve_globals.get("resolve")
    if resolve is None:
        show_error(
            "MinoruStudio",
            "DaVinci Resolve のスクリプトメニューから実行してください。",
        )
        return 2
    launch(resolve)
    return 0
