# -*- coding: utf-8 -*-
"""MinoruDouga — 写真・動画・音楽から音ハメムービーのタイムラインを自動生成する
DaVinci Resolve 用スクリプト本体。

Resolve の「ワークスペース → スクリプト → MinoruDouga」から起動する。
ビート解析は外部の Python (librosa) を subprocess で呼び出すため、
Resolve 側の Python に librosa は不要。
"""
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time

PHOTO_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp", ".heic", ".dng"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mxf", ".avi", ".mkv", ".braw", ".mts", ".m2ts"}

ANALYZER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "analyze_beats.py")
SETTINGS_PATH = os.path.join(
    os.environ.get("APPDATA", tempfile.gettempdir()), "MinoruDouga", "settings.json"
)

# AppendToTimeline の endFrame をインクルーシブ(その番号のフレームを含む)として
# 扱うかどうか。クリップが毎回 1 フレーム長い/短い場合はここを反転させる。
END_FRAME_INCLUSIVE = True


# ---------------------------------------------------------------- 設定の保存
def load_settings():
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(cfg):
    try:
        os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
        keep = {k: cfg[k] for k in ("audio", "media_dir", "every_n", "auto_n", "order") if k in cfg}
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(keep, f, ensure_ascii=False)
    except Exception:
        pass


# ---------------------------------------------------------------- ビート解析
def find_python():
    """librosa が入っているシステム Python を探す。

    WindowsApps の python.exe は Store のスタブで動かないため除外する。
    """
    candidates = []
    py = shutil.which("py")
    if py:
        candidates.append([py, "-3"])
    for name in ("python", "python3"):
        p = shutil.which(name)
        if p and "WindowsApps" not in p and "DaVinci" not in p:
            candidates.append([p])
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        for d in sorted(
            [x for x in os.listdir(os.path.join(local, "Programs", "Python"))
             if x.lower().startswith("python")]
            if os.path.isdir(os.path.join(local, "Programs", "Python")) else [],
            reverse=True,
        ):
            exe = os.path.join(local, "Programs", "Python", d, "python.exe")
            if os.path.isfile(exe):
                candidates.append([exe])
    return candidates


def analyze(audio_path, log):
    out = os.path.join(tempfile.gettempdir(), "minoru_beats.json")
    errors = []
    for base_cmd in find_python():
        cmd = base_cmd + [ANALYZER, "--audio", audio_path, "--out", out]
        log("ビート解析中: " + " ".join(cmd))
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        except Exception as e:
            errors.append(str(e))
            continue
        if r.returncode == 0:
            log(r.stdout.strip())
            with open(out, encoding="utf-8") as f:
                return json.load(f)
        errors.append((r.stderr or r.stdout or "").strip()[-1500:])
    raise RuntimeError(
        "ビート解析に失敗しました。`py -m pip install librosa soundfile numpy` 済みか確認してください。\n\n"
        + "\n---\n".join(errors[-2:])
    )


# ---------------------------------------------------------------- 素材スキャン
def scan_media(folder):
    files = []
    for name in sorted(os.listdir(folder)):
        ext = os.path.splitext(name)[1].lower()
        if ext in PHOTO_EXT or ext in VIDEO_EXT:
            files.append(os.path.join(folder, name))
    return files


# ---------------------------------------------------------------- カット計算
def beat_points(beats, duration, tl_fps, min_len=0.15):
    """拍位置をタイムラインのフレーム番号リストに変換する(0 と曲末尾を含む)。"""
    points = [0]
    for b in beats:
        f = int(round(b * tl_fps))
        if f - points[-1] >= int(min_len * tl_fps):
            points.append(f)
    end_f = int(round(duration * tl_fps))
    if end_f - points[-1] >= int(min_len * tl_fps):
        points.append(end_f)
    return points


# ---------------------------------------------------------------- タイムライン生成
def _item_key(item):
    try:
        return item.GetUniqueId()
    except Exception:
        return id(item)


def build(resolve, cfg, data, log):
    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject()
    if not project:
        raise RuntimeError("プロジェクトが開かれていません。")
    mp = project.GetMediaPool()
    tl_fps = float(project.GetSetting("timelineFrameRate") or 24)

    media_files = scan_media(cfg["media_dir"])
    if not media_files:
        raise RuntimeError("素材フォルダに写真・動画が見つかりません: " + cfg["media_dir"])

    photos = [f for f in media_files if os.path.splitext(f)[1].lower() in PHOTO_EXT]
    videos = [f for f in media_files if os.path.splitext(f)[1].lower() in VIDEO_EXT]

    # カット計画を先に立てる(写真に必要なスロット長を知るため)
    points = beat_points(data["beats"], data["duration"], tl_fps)
    n_beats = len(points) - 1
    every_n = cfg["every_n"]
    if every_n <= 0:  # 自動: 全素材がほぼ一巡する間隔
        every_n = max(1, min(16, round(n_beats / len(media_files))))
        log(f"カット間隔(自動): {n_beats}拍 ÷ 素材{len(media_files)}件 → {every_n}拍ごと")
    # 通常スロット長 = ビート区間内での every_n 拍ぶんの長さ。
    # 末尾(アウトロ等のビート無し区間)の特大スロットは対象外 ——
    # そこは長い動画か、短縮した写真で埋める。
    inner = range(max(1, n_beats - every_n))
    win_lens = [points[j + every_n] - points[j] for j in inner] if n_beats > every_n \
        else [points[-1] - points[0]]
    typical_slot = max(win_lens)
    # 写真は「標準スチルの長さ」設定そのままの長さで置かれる(endFrame 無視)ため、
    # 設定すべきジャスト値 = スロット長の最頻値(ビート揺らぎで ±1f 程度ばらつく)
    counts = {}
    for w in win_lens:
        counts[w] = counts.get(w, 0) + 1
    still_target = max(counts, key=counts.get)

    # 実行ごとに専用ビンを作ってそこへインポートする。同じファイルを
    # 同じビンに再インポートすると既存クリップ(古いスチル長が焼き込み済み)が
    # 返されるため、ビンを分けることで設定変更後の取り込み直しを確実にする。
    bin_no = [0]

    def fresh_bin():
        bin_no[0] += 1
        suffix = "" if bin_no[0] == 1 else f" ({bin_no[0]})"
        try:
            folder = mp.AddSubFolder(mp.GetRootFolder(), cfg["timeline_name"] + suffix)
            if folder:
                mp.SetCurrentFolder(folder)
                return True
        except Exception:
            pass
        return False

    fresh_bin()

    log(f"素材 {len(media_files)} 件(写真 {len(photos)} / 動画 {len(videos)})をインポート中...")
    items = []
    if videos:
        items += mp.ImportMedia(videos) or []

    def import_photos():
        # 写真は 1 枚ずつインポートする。まとめて渡すと Resolve が連番ファイル名
        # (IMG_0388.JPG, ...)を画像シーケンスとして 1 クリップに統合してしまうため。
        got = []
        for p in photos:
            got += mp.ImportMedia([p]) or []
        return got

    photo_items = import_photos()
    items += photo_items
    log(f"インポート結果: {len(items)}/{len(media_files)} 件")
    if len(items) < len(media_files):
        log("警告: インポート数が一致しません。連番写真がシーケンス化された場合は"
            "メディアプールの該当クリップを削除して再実行してください。")
    music_items = mp.ImportMedia([cfg["audio"]]) or []
    if not music_items:
        raise RuntimeError("音楽ファイルのインポートに失敗しました: " + cfg["audio"])
    music = music_items[0]

    # items は写真・動画リストからのインポート結果のみなので、そのまま全部使う
    # (Type プロパティでの絞り込みは UI 言語でローカライズされるため行わない)
    visual = list(items)
    if not visual:
        raise RuntimeError("映像素材(写真・動画)をインポートできませんでした。")
    # ImportMedia の戻り順は不定なので、まずファイル名昇順に揃える
    visual.sort(key=lambda it: os.path.basename(it.GetClipProperty("File Path") or "").lower())
    if cfg.get("order", "random") == "random":
        random.shuffle(visual)
    n = len(visual)

    timeline = mp.CreateEmptyTimeline(cfg["timeline_name"])
    if not timeline:
        raise RuntimeError(
            f"タイムライン '{cfg['timeline_name']}' を作成できません(同名が既にある可能性)。"
        )
    base = int(timeline.GetStartFrame())
    log(f"BPM {data['bpm']:.1f} / {every_n}拍ごとにカット(短い動画は収まる拍数に短縮)")

    def probe_still_len():
        """写真を 1 枚試し置きして実際に置ける長さを測り、すぐ削除する。

        「標準スチルの長さ」はタイムラインに置く瞬間に適用されるため、
        メディアプール側のプロパティからは読めない。実測が唯一確実。
        """
        if not photo_items:
            return 0
        res = mp.AppendToTimeline([{
            "mediaPoolItem": photo_items[0],
            "startFrame": 0,
            "endFrame": typical_slot * 2,
            "mediaType": 1,
            "trackIndex": 1,
            "recordFrame": base,
        }])
        ti = res[0] if isinstance(res, list) and res else None
        if ti is None:
            return 0
        try:
            measured = int(ti.GetDuration())
        except Exception:
            measured = 0
        try:
            timeline.DeleteClips([ti])
        except Exception:
            pass
        return measured

    still_len = probe_still_len()
    if photo_items:
        log(f"スチル長プローブ: {still_len}f(通常スロット {typical_slot}f)")

    # 生成前の実行確認: カット計画と「標準スチルの長さ」の状況を見せて
    # 実行/キャンセルを選んでもらう(毎回表示)
    still_ok = (not photo_items) or abs(still_len - still_target) <= 1
    plan_text = (
        "■ カット計画\n"
        f"　BPM {data['bpm']:.1f} / {every_n}拍ごと(1カット 約{still_target / tl_fps:.2f}秒)\n"
        f"　素材 {len(media_files)} 件(写真 {len(photos)} / 動画 {len(videos)})\n"
        f"　曲の長さ {data['duration']:.1f} 秒\n\n"
    )
    if photo_items:
        plan_text += (
            "■ 標準スチルの長さ(写真の表示時間 — ジャスト設定が必要)\n"
            f"　必要: {still_target} フレーム / 現在: {still_len} フレーム → "
            + ("OK\n\n" if still_ok else "要変更!\n\n"
               "　環境設定 → ユーザー → 編集 → 一般設定 →\n"
               "　「標準スチルの長さ」で単位を【フレーム】にして\n"
               f"　【 {still_target} 】を入力・保存してから [OK] を押してください。\n\n")
        )
    plan_text += "[OK] で生成を実行 / [キャンセル] で中止"

    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    go = messagebox.askokcancel("生成の確認", plan_text, parent=root)
    root.destroy()
    if not go:
        raise RuntimeError(
            "キャンセルしました(空のタイムラインとビンが残っていれば削除してください)。")
    if not still_ok:
        still_len = probe_still_len()
        log(f"スチル長プローブ(再測定): {still_len}f")

    # 音楽を A1 の先頭に配置
    ok = mp.AppendToTimeline(
        [{"mediaPoolItem": music, "mediaType": 2, "trackIndex": 1, "recordFrame": base}]
    )
    if not ok:
        log("警告: 音楽の配置に失敗しました(続行します)")

    def src_info(item):
        """(総フレーム数, ソースfps)。不明なら (0, tl_fps)。"""
        try:
            total = int(float(item.GetClipProperty("Frames") or 0))
        except Exception:
            total = 0
        try:
            fps = float(item.GetClipProperty("FPS") or tl_fps) or tl_fps
        except Exception:
            fps = tl_fps
        return total, fps

    # 種別判定はファイル拡張子で行う。Resolve の Type プロパティは
    # UI 言語によってローカライズされる(日本語だと「ビデオ」等)ため使えない。
    video_paths = {os.path.normcase(os.path.normpath(p)) for p in videos}

    def item_is_video(item):
        p = item.GetClipProperty("File Path") or ""
        return os.path.normcase(os.path.normpath(p)) in video_paths

    def fit_steps(item, i, want):
        """位置 i から want 拍ぶん使いたい。素材長に収まる最大の拍数を返す(0=不可)。

        動画はソースの長さ、写真は「標準スチルの長さ」(still_len)が上限。
        """
        k = min(want, n_beats - i)
        if not item_is_video(item):
            if still_len <= 0:
                return k
            while k >= 1 and points[i + k] - points[i] > still_len:
                k -= 1
            return k
        total, fps = src_info(item)
        if total <= 0:  # 長さ不明は制限なし扱い
            return k
        while k >= 1:
            need = int(round((points[i + k] - points[i]) * fps / tl_fps))
            if need <= total:
                return k
            k -= 1
        return 0

    def source_window(item, total):
        """ソースビューアで打った In/Out 点(ハイライト指定)を読む。未指定なら全体。"""
        win_in, win_out = 0, total
        try:
            mio = item.GetMarkInOut() or {}
            v = mio.get("video") or mio.get("audio") or {}
            if "in" in v:
                win_in = max(0, min(int(v["in"]), total - 1))
            if "out" in v:
                win_out = max(win_in + 1, min(int(v["out"]) + 1, total))
        except Exception:
            pass
        return win_in, win_out

    cursors = {}  # 各動画素材の使用位置(毎回違う箇所を使う)
    usage = {}    # 使用回数 (素材キー → 回数)
    placed = failed = 0
    still_mismatch = 0
    order_pos = 0
    i = 0
    while i < n_beats:
        want = min(every_n, n_beats - i)
        it = None
        steps = 0
        for attempt in range(n):
            cand = visual[(order_pos + attempt) % n]
            k = fit_steps(cand, i, want)
            if k >= 1:
                it, steps = cand, k
                order_pos = (order_pos + attempt + 1) % n
                break
        if it is None:  # どの素材も 1 拍すら埋められない(ほぼ起きない)
            i += 1
            continue

        length = points[i + steps] - points[i]
        total, fps = src_info(it)
        is_video = item_is_video(it)
        need = max(1, int(round(length * fps / tl_fps))) if is_video else length
        name = os.path.basename(it.GetClipProperty("File Path") or "?")
        start = 0
        if is_video and total > 0:
            key = _item_key(it)
            win_in, win_out = source_window(it, total)
            if (win_in, win_out) != (0, total) and key not in usage and key not in cursors:
                log(f"  ハイライト指定検出: {name} [{win_in}..{win_out}] / {total}f")
            if win_out - win_in < need:
                # ハイライト範囲がスロットより短い → In 点起点で必要分使う(幅優先)
                start = min(win_in, max(0, total - need))
            else:
                start = win_in + cursors.get(key, 0)
                if start + need > win_out:
                    start = win_in
                cursors[key] = (start - win_in) + need

        def append_clip(start_f, need_f):
            """配置して TimelineItem(取れなければ True/None)を返す。"""
            end_f = start_f + need_f - 1 if END_FRAME_INCLUSIVE else start_f + need_f
            res = mp.AppendToTimeline([{
                "mediaPoolItem": it,
                "startFrame": start_f,
                "endFrame": end_f,
                "mediaType": 1,
                "trackIndex": 1,
                "recordFrame": base + points[i],
            }])
            if isinstance(res, list) and res:
                return res[0]
            return True if res else None

        def duration_of(ti):
            try:
                return int(ti.GetDuration()) if ti not in (None, True) else None
            except Exception:
                return None

        ti = append_clip(start, need)
        ok_placed = ti is not None
        actual = duration_of(ti)

        if ok_placed and actual is not None and actual != length and is_video:
            # fps 換算の丸め誤差(±数フレーム)→ 1回だけ置き直して補正
            diff = length - actual
            step = int(round(diff * fps / tl_fps)) or (1 if diff > 0 else -1)
            new_need = max(1, need + step)
            new_start = min(start, max(0, total - new_need)) if total > 0 else start
            try:
                timeline.DeleteClips([ti])
                ti = append_clip(new_start, new_need)
                actual = duration_of(ti)
                ok_placed = ti is not None
            except Exception:
                pass

        if ok_placed:
            placed += 1
            usage[_item_key(it)] = usage.get(_item_key(it), 0) + 1
            # ビート揺らぎによる ±1f は正常範囲として扱う
            if actual is not None and abs(actual - length) > 1:
                still_mismatch += 1
                if still_mismatch <= 8:
                    log(f"  長さ不一致(補正後も): {name} 指定{length}f → 実際{actual}f")
        else:
            failed += 1
            log(f"  配置失敗: {name} (start={start} need={need} rec={base + points[i]})")

        try:
            timeline.AddMarker(points[i], "Blue", "beat", "", 1)
        except Exception:
            pass
        i += steps

    unused = [it for it in visual if _item_key(it) not in usage]
    if unused:
        log("未使用素材: " + ", ".join(
            os.path.basename(it.GetClipProperty("File Path") or "?") for it in unused))

    resolve.OpenPage("edit")
    return (
        f"タイムライン「{cfg['timeline_name']}」を作成しました。\n"
        f"BPM {data['bpm']:.1f} / {every_n}拍ごと / クリップ {placed} 配置"
        + (f" / {failed} 失敗" if failed else "")
        + f"\n使用素材 {len(usage)}/{n} 件"
        + (f"(未使用 {len(unused)} 件 — ログ参照)" if unused else "(全素材使用)")
        + (f"\n長さ不一致 {still_mismatch} 件 — ログ参照" if still_mismatch else "")
        + (f"\n注: 写真は {still_len / tl_fps:.1f} 秒({still_len}f)で配置されています"
           f"(標準スチルの長さ設定 / 推奨 {still_target}f)"
           if photo_items and still_len and abs(still_len - still_target) > 1 else "")
        + f"\n曲の長さ {data['duration']:.1f} 秒 / {tl_fps:g} fps"
    )


# ---------------------------------------------------------------- UI (tkinter)
# 無償版 Resolve では fusion.UIManager がスクリプトから使えない
# (アクセスすると Studio 宣伝ダイアログが出て None が返る)ため、
# ダイアログは Python 標準の tkinter で表示する。
def show_dialog_tk(defaults):
    import tkinter as tk
    from tkinter import filedialog

    default_name = "音ハメ " + time.strftime("%m%d %H%M%S")
    result = {}

    root = tk.Tk()
    root.title("MinoruDouga — 音ハメムービー生成")
    root.attributes("-topmost", True)
    root.resizable(False, False)

    audio_var = tk.StringVar(value=defaults.get("audio", ""))
    dir_var = tk.StringVar(value=defaults.get("media_dir", ""))
    saved_n = int(defaults.get("every_n", 2))
    auto_var = tk.BooleanVar(value=bool(defaults.get("auto_n", True)) or saved_n <= 0)
    n_var = tk.IntVar(value=saved_n if saved_n > 0 else 2)
    order_var = tk.StringVar(value=defaults.get(
        "order", "random" if defaults.get("shuffle", True) else "asc"))
    name_var = tk.StringVar(value=default_name)

    def browse_audio():
        p = filedialog.askopenfilename(
            parent=root, title="音楽ファイルを選択",
            filetypes=[("音楽", "*.wav *.mp3 *.flac *.ogg *.m4a *.aac"), ("すべて", "*.*")],
        )
        if p:
            audio_var.set(p)

    def browse_dir():
        p = filedialog.askdirectory(parent=root, title="素材フォルダを選択")
        if p:
            dir_var.set(p)

    def on_build():
        result.update({
            "audio": audio_var.get().strip().strip('"'),
            "media_dir": dir_var.get().strip().strip('"'),
            # every_n=0 は「自動(素材数に合わせる)」の意味
            "every_n": 0 if auto_var.get() else max(1, int(n_var.get())),
            "auto_n": bool(auto_var.get()),
            "order": order_var.get(),
            "timeline_name": name_var.get().strip() or default_name,
        })
        root.destroy()

    pad = {"padx": 6, "pady": 4}
    tk.Label(root, text="音楽ファイル").grid(row=0, column=0, sticky="e", **pad)
    tk.Entry(root, textvariable=audio_var, width=52).grid(row=0, column=1, **pad)
    tk.Button(root, text="参照...", command=browse_audio).grid(row=0, column=2, **pad)

    tk.Label(root, text="素材フォルダ").grid(row=1, column=0, sticky="e", **pad)
    tk.Entry(root, textvariable=dir_var, width=52).grid(row=1, column=1, **pad)
    tk.Button(root, text="参照...", command=browse_dir).grid(row=1, column=2, **pad)

    opts = tk.Frame(root)
    opts.grid(row=2, column=1, sticky="w", **pad)
    tk.Label(opts, text="カット間隔").pack(side="left")
    spin = tk.Spinbox(opts, from_=1, to=16, textvariable=n_var, width=4)
    spin.pack(side="left", padx=4)
    tk.Label(opts, text="拍ごと").pack(side="left")

    def sync_auto(*_):
        spin.config(state="disabled" if auto_var.get() else "normal")

    tk.Checkbutton(opts, text="自動(全素材を一巡)", variable=auto_var,
                   command=sync_auto).pack(side="left", padx=8)
    sync_auto()

    order_row = tk.Frame(root)
    order_row.grid(row=3, column=1, sticky="w", **pad)
    tk.Label(order_row, text="並び順:").pack(side="left")
    tk.Radiobutton(order_row, text="ファイル名 昇順", variable=order_var,
                   value="asc").pack(side="left", padx=6)
    tk.Radiobutton(order_row, text="ランダム", variable=order_var,
                   value="random").pack(side="left", padx=6)

    tk.Label(root, text="タイムライン名").grid(row=4, column=0, sticky="e", **pad)
    tk.Entry(root, textvariable=name_var, width=52).grid(row=4, column=1, **pad)

    btns = tk.Frame(root)
    btns.grid(row=5, column=1, columnspan=2, sticky="e", **pad)
    tk.Button(btns, text="タイムライン生成", command=on_build, width=16).pack(side="left", padx=4)
    tk.Button(btns, text="キャンセル", command=root.destroy, width=10).pack(side="left")

    root.eval("tk::PlaceWindow . center")
    root.mainloop()
    return result or None


def show_message_tk(title, text):
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        messagebox.showinfo(title, text, parent=root)
        root.destroy()
    except Exception:
        print(f"[{title}] {text}")


# ---------------------------------------------------------------- UI (UIManager / Studio用・未使用)
def show_dialog(fusion, bmd, defaults):
    ui = fusion.UIManager
    disp = bmd.UIDispatcher(ui)

    default_name = "音ハメ " + time.strftime("%m%d %H%M%S")
    win = disp.AddWindow(
        {"ID": "MDWin", "WindowTitle": "MinoruDouga — 音ハメムービー生成", "Geometry": [400, 300, 620, 240]},
        [
            ui.VGroup(
                {"Spacing": 8},
                [
                    ui.HGroup([
                        ui.Label({"Text": "音楽ファイル", "Weight": 0, "MinimumSize": [90, 0]}),
                        ui.LineEdit({"ID": "Audio", "Text": defaults.get("audio", "")}),
                        ui.Button({"ID": "BrowseAudio", "Text": "参照...", "Weight": 0}),
                    ]),
                    ui.HGroup([
                        ui.Label({"Text": "素材フォルダ", "Weight": 0, "MinimumSize": [90, 0]}),
                        ui.LineEdit({"ID": "MediaDir", "Text": defaults.get("media_dir", "")}),
                        ui.Button({"ID": "BrowseDir", "Text": "参照...", "Weight": 0}),
                    ]),
                    ui.HGroup([
                        ui.Label({"Text": "カット間隔", "Weight": 0, "MinimumSize": [90, 0]}),
                        ui.SpinBox({"ID": "EveryN", "Minimum": 1, "Maximum": 16,
                                    "Value": int(defaults.get("every_n", 2)), "Weight": 0}),
                        ui.Label({"Text": "拍ごと", "Weight": 0}),
                        ui.Label({"Text": "", "Weight": 1}),
                        ui.CheckBox({"ID": "Shuffle", "Text": "素材をランダム順に",
                                     "Checked": bool(defaults.get("shuffle", True)), "Weight": 0}),
                    ]),
                    ui.HGroup([
                        ui.Label({"Text": "タイムライン名", "Weight": 0, "MinimumSize": [90, 0]}),
                        ui.LineEdit({"ID": "TlName", "Text": default_name}),
                    ]),
                    ui.HGroup([
                        ui.Label({"Text": "", "Weight": 1}),
                        ui.Button({"ID": "Build", "Text": "タイムライン生成", "Weight": 0}),
                        ui.Button({"ID": "Cancel", "Text": "キャンセル", "Weight": 0}),
                    ]),
                ],
            )
        ],
    )
    itm = win.GetItems()
    state = {"go": False}

    def on_close(ev):
        disp.ExitLoop()

    def on_browse_audio(ev):
        p = fusion.RequestFile()
        if p:
            itm["Audio"].Text = str(p)

    def on_browse_dir(ev):
        p = fusion.RequestDir()
        if p:
            itm["MediaDir"].Text = str(p)

    def on_build(ev):
        state["go"] = True
        disp.ExitLoop()

    win.On.MDWin.Close = on_close
    win.On.Cancel.Clicked = on_close
    win.On.BrowseAudio.Clicked = on_browse_audio
    win.On.BrowseDir.Clicked = on_browse_dir
    win.On.Build.Clicked = on_build

    win.Show()
    disp.RunLoop()
    cfg = None
    if state["go"]:
        cfg = {
            "audio": itm["Audio"].Text.strip().strip('"'),
            "media_dir": itm["MediaDir"].Text.strip().strip('"'),
            "every_n": int(itm["EveryN"].Value),
            "shuffle": bool(itm["Shuffle"].Checked),
            "timeline_name": itm["TlName"].Text.strip() or default_name,
        }
    win.Hide()
    return cfg


def show_message(fusion, bmd, title, text):
    try:
        ui = fusion.UIManager
        disp = bmd.UIDispatcher(ui)
        win = disp.AddWindow(
            {"ID": "MDMsg", "WindowTitle": title, "Geometry": [450, 350, 480, 160]},
            [ui.VGroup({"Spacing": 8}, [
                ui.Label({"ID": "Msg", "Text": text, "WordWrap": True}),
                ui.HGroup([
                    ui.Label({"Text": "", "Weight": 1}),
                    ui.Button({"ID": "OK", "Text": "OK", "Weight": 0}),
                ]),
            ])],
        )

        def on_ok(ev):
            disp.ExitLoop()

        win.On.MDMsg.Close = on_ok
        win.On.OK.Clicked = on_ok
        win.Show()
        disp.RunLoop()
        win.Hide()
    except Exception:
        print(f"[{title}] {text}")


# ---------------------------------------------------------------- エントリポイント
def _make_logger(log_file):
    def log(text):
        text = str(text)
        print(text)
        if log_file:
            try:
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + text + "\n")
            except Exception:
                pass
    return log


def run(g, log_file=None):
    log = _make_logger(log_file)
    resolve = g.get("resolve")
    fusion = g.get("fusion")
    bmd = g.get("bmd")
    if not (resolve and fusion and bmd):
        log("DaVinci Resolve の ワークスペース → スクリプト メニューから実行してください。"
            f" (resolve={bool(resolve)} fusion={bool(fusion)} bmd={bool(bmd)})")
        return

    log("ダイアログを表示します (tkinter)")
    cfg = show_dialog_tk(load_settings())
    log(f"ダイアログ結果: {cfg}")
    if not cfg:
        return

    if not os.path.isfile(cfg["audio"]):
        show_message_tk("エラー", "音楽ファイルが見つかりません:\n" + cfg["audio"])
        return
    if not os.path.isdir(cfg["media_dir"]):
        show_message_tk("エラー", "素材フォルダが見つかりません:\n" + cfg["media_dir"])
        return
    save_settings(cfg)

    try:
        data = analyze(cfg["audio"], log)
        summary = build(resolve, cfg, data, log)
        log(summary)
        show_message_tk("完了", summary)
    except Exception as e:
        import traceback
        log("ERROR:\n" + traceback.format_exc())
        show_message_tk("エラー", str(e))
        raise
