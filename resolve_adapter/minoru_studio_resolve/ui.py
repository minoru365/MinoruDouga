import json
import os
import tkinter as tk
import uuid
from tkinter import filedialog, messagebox, ttk

from minoru_studio_resolve.gateway import ResolveGateway
from minoru_studio_resolve.job_io import ApplicationStore
from minoru_studio_resolve.media_service import MediaPlacementService
from minoru_studio_resolve.service import AdapterService
from minoru_studio_resolve.state import next_action


def _config_path():
    return os.path.join(
        os.environ.get("APPDATA", ""),
        "MinoruStudio",
        "resolve-adapter.json",
    )


def load_last_job(path=None):
    target = path or _config_path()
    try:
        with open(target, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return ""
    if not isinstance(payload, dict):
        return ""
    value = payload.get("last_job_path", "")
    return value if isinstance(value, str) else ""


def save_last_job(path, job_path=None):
    if job_path is None:
        job_path = path
        path = _config_path()
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    temporary = path + ".{0}.tmp".format(uuid.uuid4())
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(
                {"last_job_path": job_path},
                handle,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def action_for_detail(detail):
    if detail is None:
        return "素材を取り込む", "start", False
    action = next_action(detail)
    if action == "resume_in_out":
        return "In/Out設定後に再開", "resume", False
    if action == "retry_still":
        return "スチル設定変更後に再測定", "resume", False
    if action == "apply":
        return "タイムラインを生成", "apply", False
    if action == "confirm_subtitles":
        return "字幕読み込みを確認", "confirm_subtitles", False
    if action == "new_attempt":
        return "新しい適用を開始", "start", True
    return "処理状態を確認してください", None, False


def instruction_for_detail(detail):
    if not detail:
        return "準備済みジョブを選択してください。"
    state = detail.get("state")
    if state == "awaiting_in_out":
        return (
            "素材ビンの動画クリップで In/Out を設定してください。\n"
            "設定後、Resolve のスクリプトメニューから MinoruStudio を"
            "もう一度実行してください。"
        )
    if state == "awaiting_still_setting":
        still = detail.get("still") or {}
        return (
            "Resolve の 環境設定 → ユーザー → 編集 → 一般設定 で、"
            "標準スチルの長さを {0} フレーム相当に変更してください。\n"
            "現在の測定値は {1} フレームです。変更後、MinoruStudio を"
            "もう一度実行してください。"
        ).format(
            still.get("required_frames", "?"),
            still.get("actual_frames", "?"),
        )
    if state == "awaiting_subtitle_import":
        timeline = detail.get("timeline") or {}
        subtitle = detail.get("subtitle") or {}
        return (
            "タイムライン {0} を生成しました。\n"
            "Resolve 標準UIで次の字幕ファイルを字幕トラックへ"
            "手動で読み込んでください:\n{1}\n"
            "字幕トラックが編集可能なことを確認したら、MinoruStudio を"
            "もう一度実行して「字幕読み込みを確認」を押してください。"
        ).format(
            timeline.get("name", "timeline"),
            subtitle.get("path", ""),
        )
    if state == "ready":
        return "設定確認済みです。タイムラインを生成できます。"
    if state == "applied":
        timeline = detail.get("timeline") or {}
        return "適用完了: {0}".format(timeline.get("name", "timeline"))
    if state == "failed":
        return "前回の適用は失敗しました: {0}".format(
            detail.get("last_error") or "詳細記録を確認してください"
        )
    return "現在の適用状態: {0}".format(state or "未適用")


def confirmation_text(summary):
    if summary.get("mode") in ("transcribe", "narrate"):
        return (
            "新しいタイムラインを生成します。\n\n"
            "名前: {timeline_name}\n"
            "V1: 元動画\n"
            "A1: {audio_label}\n"
            "字幕: Resolveで手動読み込み\n"
            "字幕ファイル: {subtitle_path}\n\n"
            "既存のタイムラインやビンは上書きしません。続行しますか？"
        ).format(
            timeline_name=summary.get("timeline_name", ""),
            audio_label=summary.get("audio_label", ""),
            subtitle_path=summary.get("subtitle_path", ""),
        )
    counts = summary.get("material_counts") or {}
    still = summary.get("still") or {}
    still_text = "対象なし"
    if still:
        still_text = "{0} / {1} frames (必要 / 実測)".format(
            still.get("required_frames", "?"),
            still.get("actual_frames", "?"),
        )
    return (
        "新しいタイムラインを生成します。\n\n"
        "名前: {timeline_name}\n"
        "BPM: {bpm}\n"
        "ビート間隔: {interval}\n"
        "およそのカット長: {approximate_cut_ms} ms\n"
        "BGM長: {duration_ms} ms\n"
        "素材: 写真 {photo} / 動画 {video}\n"
        "スチル: {still}\n\n"
        "既存のタイムラインやビンは上書きしません。続行しますか？"
    ).format(
        timeline_name=summary.get("timeline_name", ""),
        bpm=summary.get("bpm", ""),
        interval=summary.get("interval", ""),
        approximate_cut_ms=summary.get("approximate_cut_ms", ""),
        duration_ms=summary.get("duration_ms", ""),
        photo=counts.get("photo", 0),
        video=counts.get("video", 0),
        still=still_text,
    )


def show_error(title, text):
    messagebox.showerror(title, text)


def _read_job(job_dir):
    path = os.path.realpath(job_dir)
    if not path.lower().endswith(".media-job") or not os.path.isdir(path):
        raise ValueError(".media-job ディレクトリを選択してください")
    with open(os.path.join(path, "job.json"), encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("job.json が不正です")
    return path, payload


class AdapterWindow(object):
    def __init__(self, root, resolve):
        self.root = root
        self.gateway = ResolveGateway(resolve)
        self.applications = ApplicationStore()
        self.beat_service = AdapterService(self.gateway, self.applications)
        self.media_service = MediaPlacementService(
            self.gateway,
            self.applications,
        )
        self.service = None
        self.detail = None
        self.action = None
        self.new_attempt = False
        self.busy = False

        self.job_path = tk.StringVar(value=load_last_job())
        self.job_name = tk.StringVar(value="-")
        self.preparation_state = tk.StringVar(value="-")
        self.application_state = tk.StringVar(value="未適用")
        self.project_name = tk.StringVar(value="-")
        self.status_text = tk.StringVar(value="準備済みジョブを選択してください。")

        self._build()
        if self.job_path.get():
            self.refresh(show_errors=False)

    def _build(self):
        self.root.title("MinoruStudio Resolve Adapter")
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)
        frame = ttk.Frame(self.root, padding=14)
        frame.grid(row=0, column=0, sticky="nsew")

        ttk.Label(frame, text="ジョブ").grid(row=0, column=0, sticky="w")
        ttk.Entry(
            frame,
            textvariable=self.job_path,
            state="readonly",
            width=66,
        ).grid(row=1, column=0, sticky="ew", padx=(0, 8))
        ttk.Button(
            frame,
            text=".media-job を選択",
            command=self.select_job,
        ).grid(row=1, column=1, sticky="ew")

        values = (
            ("ジョブ名", self.job_name),
            ("準備状態", self.preparation_state),
            ("適用状態", self.application_state),
            ("現在のプロジェクト", self.project_name),
        )
        for offset, pair in enumerate(values, start=2):
            ttk.Label(frame, text=pair[0]).grid(
                row=offset,
                column=0,
                sticky="w",
                pady=(7, 0),
            )
            ttk.Label(frame, textvariable=pair[1]).grid(
                row=offset,
                column=1,
                sticky="e",
                pady=(7, 0),
            )

        ttk.Separator(frame).grid(
            row=6,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=10,
        )
        ttk.Label(
            frame,
            textvariable=self.status_text,
            wraplength=620,
            justify="left",
        ).grid(row=7, column=0, columnspan=2, sticky="w")
        self.primary = ttk.Button(frame, command=self.run_primary)
        self.primary.grid(
            row=8,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(12, 0),
        )
        self.new_attempt_button = ttk.Button(
            frame,
            text="新しい適用を開始",
            command=self.run_new_attempt,
        )
        self.new_attempt_button.grid(
            row=8,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(12, 0),
        )
        self.new_attempt_button.grid_remove()
        frame.columnconfigure(0, weight=1)
        self._set_action(None)

    def _set_action(self, detail):
        label, action, new_attempt = action_for_detail(detail)
        self.action = action
        self.new_attempt = new_attempt
        terminal = detail is not None and detail.get("state") in (
            "applied",
            "failed",
        )
        if terminal:
            self.primary.grid_remove()
            self.new_attempt_button.grid()
            self.new_attempt_button.configure(
                state="disabled" if self.busy else "normal"
            )
        else:
            self.new_attempt_button.grid_remove()
            self.primary.grid()
            self.primary.configure(text=label)
            state = "normal" if action and not self.busy else "disabled"
            self.primary.configure(state=state)

    def select_job(self):
        current = self.job_path.get()
        initial = current if os.path.isdir(current) else os.getcwd()
        selected = filedialog.askdirectory(
            title=".media-job を選択",
            initialdir=initial,
            mustexist=True,
            parent=self.root,
        )
        if not selected:
            return
        try:
            job_dir, unused_manifest = _read_job(selected)
            self.job_path.set(job_dir)
            save_last_job(job_dir)
            self.refresh(show_errors=True)
        except Exception as exc:
            messagebox.showerror(
                "ジョブ読込失敗",
                str(exc),
                parent=self.root,
            )

    def refresh(self, show_errors=True):
        path = self.job_path.get()
        if not path:
            self.detail = None
            self._set_action(None)
            return
        try:
            job_dir, manifest = _read_job(path)
            if manifest.get("mode") == "beat-sync":
                self.service = self.beat_service
            elif manifest.get("mode") in ("transcribe", "narrate"):
                self.service = self.media_service
            else:
                raise ValueError(
                    "このジョブモードはResolve適用に対応していません"
                )
            project = self.gateway.current_project()
            self.job_name.set(str(manifest.get("name", "-")))
            self.preparation_state.set(str(manifest.get("status", "-")))
            self.project_name.set(project["name"])
            detail = self.applications.latest(job_dir, project["id"])
            self.detail = detail
            self.application_state.set(
                "未適用" if detail is None else str(detail.get("state", "-"))
            )
            self.status_text.set(instruction_for_detail(detail))
            self._set_action(detail)
        except Exception as exc:
            self.detail = None
            self.service = None
            self.application_state.set("読込失敗")
            self.status_text.set(str(exc))
            self.action = None
            self.primary.configure(state="disabled")
            self.new_attempt_button.configure(state="disabled")
            if show_errors:
                messagebox.showerror(
                    "状態更新失敗",
                    str(exc),
                    parent=self.root,
                )

    def _confirm(self, summary):
        return messagebox.askokcancel(
            "タイムライン生成の確認",
            confirmation_text(summary),
            parent=self.root,
        )

    def _confirm_subtitles(self, summary):
        return messagebox.askokcancel(
            "字幕読み込みの確認",
            (
                "Resolve 標準UIで次の字幕ファイルの手動読み込みは"
                "完了しましたか？\n{0}\n\n"
                "OK を押すと適用完了として記録します。"
            ).format(summary.get("subtitle_path", "")),
            parent=self.root,
        )

    def _run(self, action, new_attempt):
        if self.busy:
            return
        path = self.job_path.get()
        if not path:
            messagebox.showerror(
                "ジョブ未選択",
                ".media-job を選択してください。",
                parent=self.root,
            )
            return
        self.busy = True
        self._set_action(self.detail)
        self.status_text.set("Resolve 処理を実行しています…")
        self.root.update_idletasks()
        try:
            if self.service is None:
                raise ValueError("現在の状態では操作できません")
            if action == "start":
                result = self.service.start(path, new_attempt=new_attempt)
            elif action == "resume":
                result = self.service.resume(path)
            elif action == "apply":
                result = self.service.apply_ready(path, confirm=self._confirm)
            elif action == "confirm_subtitles":
                result = self.service.confirm_subtitle_import(
                    path,
                    confirm=self._confirm_subtitles,
                )
            else:
                raise ValueError("現在の状態では操作できません")
            self.busy = False
            self.refresh(show_errors=False)
            if result.get("state") in (
                "awaiting_in_out",
                "awaiting_still_setting",
                "awaiting_subtitle_import",
            ):
                messagebox.showinfo(
                    "次の操作",
                    instruction_for_detail(result),
                    parent=self.root,
                )
                self.root.destroy()
        except Exception as exc:
            self.busy = False
            self.refresh(show_errors=False)
            messagebox.showerror(
                "MinoruStudio エラー",
                str(exc),
                parent=self.root,
            )
        finally:
            if self.busy:
                self.busy = False
            try:
                if self.root.winfo_exists():
                    self._set_action(self.detail)
            except tk.TclError:
                pass

    def run_primary(self):
        self._run(self.action, self.new_attempt)

    def run_new_attempt(self):
        self._run("start", True)


def launch(resolve):
    root = tk.Tk()
    AdapterWindow(root, resolve)
    root.mainloop()
