from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from minoru_studio.beat_sync.service import BeatSyncRequest, BeatSyncService
from minoru_studio.beat_sync.settings import (
    load_settings as load_beat_sync_settings,
)
from minoru_studio.beat_sync.settings import (
    save_settings as save_beat_sync_settings,
)
from minoru_studio.jobs.model import JobManifest, JobMode
from minoru_studio.jobs.store import JobStore
from minoru_studio.script_draft.gui_state import ScriptDraftFormValues
from minoru_studio.script_draft.service import (
    ScriptDraftFailed,
    ScriptDraftInterrupted,
    ScriptDraftService,
)
from minoru_studio.script_draft.settings import (
    load_settings as load_script_draft_settings,
)
from minoru_studio.script_draft.settings import (
    save_settings as save_script_draft_settings,
)
from minoru_studio.transcribe.gui_state import (
    TranscribeFormValues,
    model_prompt,
    normalize_language,
)
from minoru_studio.transcribe.media import probe_media
from minoru_studio.transcribe.models import ModelCapacityError
from minoru_studio.transcribe.service import (
    TranscribeService,
    TranscriptionInterrupted,
)
from minoru_studio.transcribe.settings import (
    load_settings as load_transcribe_settings,
)
from minoru_studio.transcribe.settings import (
    save_settings as save_transcribe_settings,
)


class LauncherController:
    def __init__(
        self,
        store: JobStore | None = None,
        beat_sync_service: BeatSyncService | None = None,
        transcribe_service: TranscribeService | None = None,
        script_draft_service: ScriptDraftService | None = None,
    ):
        self.store = store or JobStore()
        self.beat_sync_service = (
            beat_sync_service if beat_sync_service is not None else BeatSyncService()
        )
        self.transcribe_service = (
            transcribe_service if transcribe_service is not None else TranscribeService()
        )
        self.script_draft_service = (
            script_draft_service if script_draft_service is not None else ScriptDraftService()
        )

    def create_job(self, mode: str, name: str, output_dir: str) -> Path:
        return self.store.create(
            root=Path(output_dir),
            name=name,
            mode=JobMode(mode),
        )

    def inspect_job(self, job_dir: str) -> JobManifest:
        return self.store.load(Path(job_dir), recover_interrupted=False)

    def prepare_beat_sync(
        self,
        *,
        music,
        media_dir,
        every_n,
        order,
        timeline_name,
        name,
        output_dir,
    ):
        return self.beat_sync_service.create_and_prepare(
            BeatSyncRequest(
                Path(music),
                Path(media_dir),
                every_n,
                order,
                timeline_name,
                name,
                Path(output_dir),
            )
        )

    def prepare_transcription(
        self,
        *,
        input_path,
        name,
        output_dir,
        model,
        language,
        normalize,
        denoise,
        preview,
        allow_model_download=False,
        cancel_event=None,
        progress=None,
    ):
        return self.transcribe_service.create_and_run(
            TranscribeFormValues(
                input_path=input_path,
                name=name,
                output_dir=output_dir,
                model=model,
                language=language,
                normalize=normalize,
                denoise=denoise,
                preview=preview,
            ).to_request(),
            allow_model_download=allow_model_download,
            cancel_event=cancel_event,
            progress=progress,
        )

    def resume_transcription(
        self,
        job_dir,
        *,
        allow_model_download=False,
        cancel_event=None,
        progress=None,
    ):
        return self.transcribe_service.resume(
            Path(job_dir),
            allow_model_download=allow_model_download,
            cancel_event=cancel_event,
            progress=progress,
        )

    def transcription_model_prompt(self, model):
        return model_prompt(model)

    def prepare_script_draft(
        self,
        *,
        input_path,
        name,
        output_dir,
        cancel_event=None,
        progress=None,
    ):
        return self.script_draft_service.create_and_run(
            ScriptDraftFormValues(
                input_path=input_path,
                name=name,
                output_dir=output_dir,
            ).to_request(),
            cancel_event=cancel_event,
            progress=progress,
        )

    def resume_script_draft(
        self,
        job_dir,
        *,
        cancel_event=None,
        progress=None,
    ):
        return self.script_draft_service.resume(
            Path(job_dir),
            cancel_event=cancel_event,
            progress=progress,
        )


def launch_gui(controller: LauncherController | None = None) -> None:
    if controller is None:
        controller = LauncherController()
    root = tk.Tk()
    root.title("MinoruStudio")
    root.geometry("720x690")
    root.resizable(False, False)

    frame = ttk.Frame(root, padding=20)
    frame.pack(fill="both", expand=True)

    saved = load_beat_sync_settings()
    saved_transcribe = load_transcribe_settings()
    saved_script_draft = load_script_draft_settings()
    saved_every_n = saved.get("every_n", "auto")
    if saved_every_n != "auto":
        try:
            saved_every_n = min(16, max(1, int(saved_every_n)))
        except (TypeError, ValueError):
            saved_every_n = "auto"
    saved_order = saved.get("order", "asc")
    if saved_order not in {"asc", "random"}:
        saved_order = "asc"

    mode_var = tk.StringVar(value=JobMode.BEAT_SYNC.value)
    name_var = tk.StringVar(value=saved.get("name", "video-job"))
    output_var = tk.StringVar(
        value=saved.get(
            "output_dir",
            str(Path.home() / "Videos" / "MinoruStudio"),
        )
    )
    music_var = tk.StringVar(value=saved.get("music", ""))
    media_var = tk.StringVar(value=saved.get("media_dir", ""))
    auto_var = tk.BooleanVar(value=saved_every_n == "auto")
    every_n_var = tk.StringVar(
        value="1" if saved_every_n == "auto" else str(saved_every_n)
    )
    order_var = tk.StringVar(value=saved_order)
    timeline_var = tk.StringVar(value=saved.get("timeline_name", "Beat Sync Demo"))
    transcribe_input_var = tk.StringVar(value=saved_transcribe.get("input", ""))
    transcribe_model_var = tk.StringVar(value=saved_transcribe.get("model", "small"))
    transcribe_language_var = tk.StringVar(value=saved_transcribe.get("language", "ja"))
    transcribe_normalize_var = tk.BooleanVar(value=bool(saved_transcribe.get("normalize", False)))
    transcribe_denoise_var = tk.BooleanVar(value=bool(saved_transcribe.get("denoise", False)))
    transcribe_preview_var = tk.BooleanVar(value=bool(saved_transcribe.get("preview", False)))
    script_draft_input_var = tk.StringVar(value=saved_script_draft.get("input", ""))
    status_var = tk.StringVar(value="新しいジョブを作成するか、既存ジョブを開いてください。")
    mode_defaults = {
        JobMode.BEAT_SYNC.value: {
            "name": saved.get("name", "video-job"),
            "output_dir": saved.get("output_dir", str(Path.home() / "Videos" / "MinoruStudio")),
        },
        JobMode.TRANSCRIBE.value: {
            "name": saved_transcribe.get("name", "transcribe-job"),
            "output_dir": saved_transcribe.get("output_dir", str(Path.home() / "Videos" / "MinoruStudio")),
        },
        JobMode.SCRIPT_DRAFT.value: {
            "name": saved_script_draft.get("name", "script-draft-job"),
            "output_dir": saved_script_draft.get("output_dir", str(Path.home() / "Videos" / "MinoruStudio")),
        },
    }

    ttk.Label(frame, text="モード").grid(row=0, column=0, sticky="w")
    mode_box = ttk.Combobox(
        frame,
        textvariable=mode_var,
        values=[mode.value for mode in JobMode],
        state="readonly",
        width=28,
    )
    mode_box.grid(row=0, column=1, columnspan=2, sticky="ew", pady=4)

    ttk.Label(frame, text="ジョブ名").grid(row=1, column=0, sticky="w")
    name_entry = ttk.Entry(frame, textvariable=name_var, width=31)
    name_entry.grid(
        row=1, column=1, columnspan=2, sticky="ew", pady=4
    )

    ttk.Label(frame, text="保存先").grid(row=2, column=0, sticky="w")
    output_entry = ttk.Entry(frame, textvariable=output_var, width=31)
    output_entry.grid(
        row=2, column=1, sticky="ew", pady=4
    )

    def choose_output() -> None:
        selected = filedialog.askdirectory(initialdir=output_var.get())
        if selected:
            output_var.set(selected)

    output_button = ttk.Button(frame, text="選択", command=choose_output)
    output_button.grid(row=2, column=2, padx=4)

    beat_frame = ttk.LabelFrame(frame, text="音ハメ準備", padding=12)
    beat_frame.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(14, 4))
    beat_frame.columnconfigure(1, weight=1)

    ttk.Label(beat_frame, text="BGM").grid(row=0, column=0, sticky="w")
    ttk.Entry(beat_frame, textvariable=music_var, width=48).grid(
        row=0,
        column=1,
        sticky="ew",
        pady=4,
    )

    def choose_music() -> None:
        selected = filedialog.askopenfilename(
            title="BGMを選択",
            filetypes=(
                ("Audio", "*.wav *.flac *.mp3 *.ogg *.m4a *.aac"),
                ("All files", "*.*"),
            ),
        )
        if selected:
            music_var.set(selected)

    ttk.Button(beat_frame, text="選択", command=choose_music).grid(
        row=0,
        column=2,
        padx=4,
    )

    ttk.Label(beat_frame, text="素材フォルダ").grid(row=1, column=0, sticky="w")
    ttk.Entry(beat_frame, textvariable=media_var, width=48).grid(
        row=1,
        column=1,
        sticky="ew",
        pady=4,
    )

    def choose_media() -> None:
        selected = filedialog.askdirectory(
            title="静止画・動画フォルダを選択",
            initialdir=media_var.get() or None,
        )
        if selected:
            media_var.set(selected)

    ttk.Button(beat_frame, text="選択", command=choose_media).grid(
        row=1,
        column=2,
        padx=4,
    )

    ttk.Label(beat_frame, text="カット間隔").grid(row=2, column=0, sticky="w")
    interval_frame = ttk.Frame(beat_frame)
    interval_frame.grid(row=2, column=1, columnspan=2, sticky="w", pady=4)
    every_n_box = ttk.Spinbox(
        interval_frame,
        from_=1,
        to=16,
        textvariable=every_n_var,
        width=5,
    )

    def update_interval_state() -> None:
        if auto_var.get():
            every_n_box.state(["disabled"])
        else:
            every_n_box.state(["!disabled"])

    ttk.Checkbutton(
        interval_frame,
        text="自動",
        variable=auto_var,
        command=update_interval_state,
    ).pack(side="left")
    every_n_box.pack(side="left", padx=(12, 4))
    ttk.Label(interval_frame, text="拍ごと").pack(side="left")
    update_interval_state()

    ttk.Label(beat_frame, text="並び順").grid(row=3, column=0, sticky="w")
    order_frame = ttk.Frame(beat_frame)
    order_frame.grid(row=3, column=1, columnspan=2, sticky="w", pady=4)
    ttk.Radiobutton(
        order_frame,
        text="ファイル名順",
        variable=order_var,
        value="asc",
    ).pack(side="left")
    ttk.Radiobutton(
        order_frame,
        text="ランダム",
        variable=order_var,
        value="random",
    ).pack(side="left", padx=12)

    ttk.Label(beat_frame, text="タイムライン名").grid(row=4, column=0, sticky="w")
    ttk.Entry(beat_frame, textvariable=timeline_var, width=48).grid(
        row=4,
        column=1,
        columnspan=2,
        sticky="ew",
        pady=4,
    )

    transcribe_frame = ttk.LabelFrame(frame, text="文字起こし", padding=12)
    transcribe_frame.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(14, 4))
    transcribe_frame.columnconfigure(1, weight=1)
    ttk.Label(transcribe_frame, text="入力動画・音声").grid(row=0, column=0, sticky="w")
    transcribe_input_entry = ttk.Entry(transcribe_frame, textvariable=transcribe_input_var, width=48)
    transcribe_input_entry.grid(row=0, column=1, sticky="ew", pady=4)

    def choose_transcribe_input() -> None:
        selected = filedialog.askopenfilename(
            title="文字起こしする動画・音声を選択",
            filetypes=(("Video / Audio", "*.mp4 *.mov *.mkv *.avi *.m4a *.wav *.mp3 *.flac"), ("All files", "*.*")),
        )
        if selected:
            transcribe_input_var.set(selected)

    transcribe_input_button = ttk.Button(transcribe_frame, text="選択", command=choose_transcribe_input)
    transcribe_input_button.grid(row=0, column=2, padx=4)
    ttk.Label(transcribe_frame, text="モデル").grid(row=1, column=0, sticky="w")
    transcribe_model_box = ttk.Combobox(transcribe_frame, textvariable=transcribe_model_var, values=("small", "medium"), state="readonly", width=16)
    transcribe_model_box.grid(row=1, column=1, sticky="w", pady=4)
    ttk.Label(transcribe_frame, text="言語").grid(row=2, column=0, sticky="w")
    transcribe_language_box = ttk.Combobox(transcribe_frame, textvariable=transcribe_language_var, values=("ja", "auto"), width=16)
    transcribe_language_box.grid(row=2, column=1, sticky="w", pady=4)
    transcribe_normalize_check = ttk.Checkbutton(transcribe_frame, text="音量を正規化", variable=transcribe_normalize_var)
    transcribe_normalize_check.grid(row=3, column=0, sticky="w", pady=4)
    transcribe_denoise_check = ttk.Checkbutton(transcribe_frame, text="ノイズを軽減", variable=transcribe_denoise_var)
    transcribe_denoise_check.grid(row=3, column=1, sticky="w", pady=4)
    transcribe_preview_check = ttk.Checkbutton(transcribe_frame, text="字幕付きプレビューを作成", variable=transcribe_preview_var)
    transcribe_preview_check.grid(row=4, column=0, columnspan=2, sticky="w", pady=4)

    script_draft_frame = ttk.LabelFrame(frame, text="台本下書き", padding=12)
    script_draft_frame.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(14, 4))
    script_draft_frame.columnconfigure(1, weight=1)
    ttk.Label(script_draft_frame, text="入力動画").grid(row=0, column=0, sticky="w")
    script_draft_input_entry = ttk.Entry(
        script_draft_frame,
        textvariable=script_draft_input_var,
        width=48,
    )
    script_draft_input_entry.grid(row=0, column=1, sticky="ew", pady=4)

    def choose_script_draft_input() -> None:
        selected = filedialog.askopenfilename(
            title="台本下書きする動画を選択",
            filetypes=(
                ("Video", "*.mp4 *.mov *.mkv *.avi *.webm *.m4v"),
                ("All files", "*.*"),
            ),
        )
        if selected:
            script_draft_input_var.set(selected)

    script_draft_input_button = ttk.Button(
        script_draft_frame,
        text="選択",
        command=choose_script_draft_input,
    )
    script_draft_input_button.grid(row=0, column=2, padx=4)

    def create_job() -> None:
        if active_cancel_event is not None:
            return
        try:
            job_dir = controller.create_job(
                mode_var.get(), name_var.get(), output_var.get()
            )
        except Exception as exc:
            messagebox.showerror("ジョブ作成失敗", str(exc), parent=root)
            return
        status_var.set(f"作成: {job_dir}")
        messagebox.showinfo(
            "ジョブを作成しました",
            f"{job_dir}\n\n状態: pending",
            parent=root,
        )

    current_transcribe_job: Path | None = None
    current_transcribe_model: str | None = None
    completed_transcribe_inspection = False
    current_script_draft_job: Path | None = None
    completed_script_draft_inspection = False
    active_cancel_event: threading.Event | None = None

    def open_job() -> None:
        nonlocal current_transcribe_job, current_transcribe_model, completed_transcribe_inspection
        nonlocal current_script_draft_job, completed_script_draft_inspection
        if active_cancel_event is not None:
            return
        selected = filedialog.askdirectory(title=".media-job を選択")
        if not selected:
            return
        try:
            manifest = controller.inspect_job(selected)
        except Exception as exc:
            messagebox.showerror("ジョブ読込失敗", str(exc), parent=root)
            return
        status_var.set(
            f"{manifest.name} / {manifest.mode.value} / {manifest.status.value}"
        )
        if manifest.mode is JobMode.TRANSCRIBE:
            model = manifest.settings.get("model", "small")
            language = manifest.settings.get("language", "ja")
            transcribe_model_var.set(model if model in {"small", "medium"} else "small")
            try:
                transcribe_language_var.set(normalize_language(language))
            except ValueError:
                transcribe_language_var.set("ja")
            mode_var.set(JobMode.TRANSCRIBE.value)
            if manifest.status.value in {"failed", "interrupted"}:
                current_transcribe_job = Path(selected)
                current_transcribe_model = transcribe_model_var.get()
                completed_transcribe_inspection = False
                transcribe_button.configure(text="文字起こしを再開")
                transcribe_button.state(["!disabled"])
            elif manifest.status.value == "succeeded":
                current_transcribe_job = None
                current_transcribe_model = None
                completed_transcribe_inspection = True
                transcribe_button.configure(text="完了済みジョブ（確認のみ）")
                transcribe_button.state(["disabled"])
            update_mode_fields()
        elif manifest.mode is JobMode.SCRIPT_DRAFT:
            mode_var.set(JobMode.SCRIPT_DRAFT.value)
            if manifest.status.value in {"failed", "interrupted"}:
                current_script_draft_job = Path(selected)
                completed_script_draft_inspection = False
                script_draft_button.configure(text="台本下書きを再開")
                script_draft_button.state(["!disabled"])
            elif manifest.status.value == "succeeded":
                current_script_draft_job = None
                completed_script_draft_inspection = True
                script_draft_button.configure(text="完了済みジョブ（確認のみ）")
                script_draft_button.state(["disabled"])
            update_mode_fields()

    def finish_error(message: str) -> None:
        prepare_button.state(["!disabled"])
        status_var.set(f"準備失敗: {message}")
        messagebox.showerror("音ハメ準備失敗", message, parent=root)

    def finish_success(job_dir: Path) -> None:
        prepare_button.state(["!disabled"])
        resolved = Path(job_dir).resolve()
        status_var.set(f"準備完了: {resolved}")
        messagebox.showinfo(
            "音ハメ準備が完了しました",
            (
                f"{resolved}\n\n"
                "outputs/beat-sync-plan.json を作成しました。\n"
                "次はDaVinci Resolve内のMinoruStudioアダプターから適用します。"
            ),
            parent=root,
        )

    def prepare_beat_sync_job() -> None:
        if active_cancel_event is not None:
            return
        try:
            every_n = "auto" if auto_var.get() else int(every_n_var.get())
            if every_n != "auto" and not 1 <= every_n <= 16:
                raise ValueError("カット間隔は1〜16を指定してください")
            values = {
                "music": music_var.get().strip(),
                "media_dir": media_var.get().strip(),
                "every_n": every_n,
                "order": order_var.get(),
                "timeline_name": timeline_var.get().strip(),
                "name": name_var.get().strip(),
                "output_dir": output_var.get().strip(),
            }
            save_beat_sync_settings(values)
        except Exception as exc:
            finish_error(str(exc))
            return
        prepare_button.state(["disabled"])
        status_var.set("BGMを解析しています…")

        def worker() -> None:
            try:
                job_dir = controller.prepare_beat_sync(**values)
            except Exception as exc:
                root.after(0, lambda message=str(exc): finish_error(message))
            else:
                root.after(0, lambda path=job_dir: finish_success(path))

        threading.Thread(target=worker, daemon=True).start()

    def transcribe_values() -> TranscribeFormValues:
        return TranscribeFormValues(
            input_path=transcribe_input_var.get(),
            name=name_var.get(),
            output_dir=output_var.get(),
            model=transcribe_model_var.get(),
            language=transcribe_language_var.get(),
            normalize=transcribe_normalize_var.get(),
            denoise=transcribe_denoise_var.get(),
            preview=transcribe_preview_var.get(),
        )

    def set_transcribe_mutable(enabled: bool) -> None:
        state = ["!disabled"] if enabled else ["disabled"]
        for widget in (
            transcribe_input_entry,
            transcribe_input_button,
            transcribe_model_box,
            transcribe_language_box,
            transcribe_normalize_check,
            transcribe_denoise_check,
            transcribe_preview_check,
            transcribe_button,
            mode_box,
            name_entry,
            output_entry,
            output_button,
            prepare_button,
            create_button,
            open_button,
        ):
            widget.state(state)
        cancel_button.state(["disabled"] if enabled else ["!disabled"])

    def finish_transcription_success(job_dir: Path) -> None:
        nonlocal active_cancel_event, current_transcribe_job, current_transcribe_model
        active_cancel_event = None
        current_transcribe_job = None
        current_transcribe_model = None
        set_transcribe_mutable(True)
        transcribe_button.configure(text="文字起こしを開始")
        status_var.set(f"文字起こし完了: {Path(job_dir).resolve()}")

    def finish_transcription_error(message: str, interrupted: bool = False) -> None:
        nonlocal active_cancel_event
        active_cancel_event = None
        set_transcribe_mutable(True)
        status_var.set("文字起こしを中断しました" if interrupted else f"文字起こし失敗: {message}")
        if not interrupted:
            messagebox.showerror("文字起こし失敗", message, parent=root)

    def start_or_resume_transcription() -> None:
        nonlocal active_cancel_event
        try:
            selected_job = current_transcribe_job
            if selected_job is None:
                values = transcribe_values()
                request = values.to_request()
                if request.preview:
                    request = values.to_request(media_info=probe_media(request.input_path))
                save_transcribe_settings(
                    {
                        "input": str(request.input_path),
                        "name": request.name,
                        "output_dir": str(request.output_dir),
                        "model": request.model,
                        "language": request.language,
                        "normalize": request.normalize,
                        "denoise": request.denoise,
                        "preview": request.preview,
                    }
                )
                model = request.model
            else:
                request = None
                model = current_transcribe_model
                if model is None:
                    raise RuntimeError("文字起こしジョブのモデル情報がありません")
            prompt = controller.transcription_model_prompt(model)
            if not prompt.cached:
                if prompt.free_bytes < prompt.required_free_bytes:
                    raise ModelCapacityError("モデルの保存先に十分な空き容量がありません")
                approved = messagebox.askyesno(
                    "モデルをダウンロード",
                    (
                        f"{prompt.model} モデル（約 {prompt.estimated_download_bytes:,} bytes）が見つかりません。\n"
                        f"保存先: {prompt.cache_dir}\n\n今回だけダウンロードを許可しますか？"
                    ),
                    parent=root,
                )
                if not approved:
                    return
            else:
                approved = False
        except Exception as exc:
            finish_transcription_error(str(exc))
            return
        active_cancel_event = threading.Event()
        cancel_event = active_cancel_event
        set_transcribe_mutable(False)
        status_var.set("文字起こしを開始しています…")

        def progress(step: str) -> None:
            root.after(0, lambda step=step: status_var.set(f"文字起こし: {step}"))

        def worker() -> None:
            try:
                if selected_job is None:
                    assert request is not None
                    job_dir = controller.prepare_transcription(
                        input_path=str(request.input_path), name=request.name,
                        output_dir=str(request.output_dir), model=request.model,
                        language=request.language, normalize=request.normalize,
                        denoise=request.denoise, preview=request.preview,
                        allow_model_download=approved, cancel_event=cancel_event,
                        progress=progress,
                    )
                else:
                    job_dir = controller.resume_transcription(
                        selected_job, allow_model_download=approved,
                        cancel_event=cancel_event, progress=progress,
                    )
            except TranscriptionInterrupted:
                root.after(0, lambda: finish_transcription_error("", interrupted=True))
            except Exception as exc:
                root.after(0, lambda message=str(exc): finish_transcription_error(message))
            else:
                root.after(0, lambda path=job_dir: finish_transcription_success(path))

        threading.Thread(target=worker, daemon=True).start()

    def cancel_transcription() -> None:
        if active_cancel_event is not None and not active_cancel_event.is_set():
            active_cancel_event.set()
            status_var.set("キャンセル中…")

    def script_draft_values() -> ScriptDraftFormValues:
        return ScriptDraftFormValues(
            input_path=script_draft_input_var.get(),
            name=name_var.get(),
            output_dir=output_var.get(),
        )

    def set_script_draft_mutable(enabled: bool) -> None:
        state = ["!disabled"] if enabled else ["disabled"]
        for widget in (
            script_draft_input_entry,
            script_draft_input_button,
            script_draft_button,
            mode_box,
            name_entry,
            output_entry,
            output_button,
            prepare_button,
            create_button,
            open_button,
        ):
            widget.state(state)
        script_draft_cancel_button.state(["disabled"] if enabled else ["!disabled"])

    def finish_script_draft_success(job_dir: Path) -> None:
        nonlocal active_cancel_event, current_script_draft_job
        active_cancel_event = None
        current_script_draft_job = None
        set_script_draft_mutable(True)
        script_draft_button.configure(text="台本下書きを開始")
        status_var.set(f"台本下書き完了: {Path(job_dir).resolve()}")

    def finish_script_draft_error(category: str, interrupted: bool = False) -> None:
        nonlocal active_cancel_event
        active_cancel_event = None
        set_script_draft_mutable(True)
        status_var.set(
            "台本下書きを中断しました"
            if interrupted
            else f"台本下書き失敗: {category}"
        )
        if not interrupted:
            messagebox.showerror("台本下書き失敗", category, parent=root)

    def start_or_resume_script_draft() -> None:
        nonlocal active_cancel_event
        try:
            selected_job = current_script_draft_job
            if selected_job is None:
                request = script_draft_values().to_request()
                save_script_draft_settings(
                    {
                        "input": str(request.input_path),
                        "name": request.name,
                        "output_dir": str(request.output_dir),
                    }
                )
            else:
                request = None
        except Exception:
            finish_script_draft_error("input validation")
            return

        active_cancel_event = threading.Event()
        cancel_event = active_cancel_event
        set_script_draft_mutable(False)
        status_var.set("台本下書きを開始しています…")

        def progress(step: str) -> None:
            root.after(0, lambda step=step: status_var.set(f"台本下書き: {step}"))

        def worker() -> None:
            try:
                if selected_job is None:
                    assert request is not None
                    job_dir = controller.prepare_script_draft(
                        input_path=str(request.input_path),
                        name=request.name,
                        output_dir=str(request.output_dir),
                        cancel_event=cancel_event,
                        progress=progress,
                    )
                else:
                    job_dir = controller.resume_script_draft(
                        selected_job,
                        cancel_event=cancel_event,
                        progress=progress,
                    )
            except ScriptDraftInterrupted:
                root.after(0, lambda: finish_script_draft_error("", interrupted=True))
            except ScriptDraftFailed as exc:
                root.after(
                    0,
                    lambda category=exc.category: finish_script_draft_error(category),
                )
            except Exception:
                root.after(0, lambda: finish_script_draft_error("execution failure"))
            else:
                root.after(0, lambda path=job_dir: finish_script_draft_success(path))

        threading.Thread(target=worker, daemon=True).start()

    transcribe_actions = ttk.Frame(transcribe_frame)
    transcribe_actions.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(10, 0))
    transcribe_actions.columnconfigure(0, weight=1)
    transcribe_button = ttk.Button(transcribe_actions, text="文字起こしを開始", command=start_or_resume_transcription)
    transcribe_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
    cancel_button = ttk.Button(transcribe_actions, text="キャンセル", command=cancel_transcription)
    cancel_button.grid(row=0, column=1, sticky="ew")
    cancel_button.state(["disabled"])

    script_draft_actions = ttk.Frame(script_draft_frame)
    script_draft_actions.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(10, 0))
    script_draft_actions.columnconfigure(0, weight=1)
    script_draft_button = ttk.Button(
        script_draft_actions,
        text="台本下書きを開始",
        command=start_or_resume_script_draft,
    )
    script_draft_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
    script_draft_cancel_button = ttk.Button(
        script_draft_actions,
        text="キャンセル",
        command=cancel_transcription,
    )
    script_draft_cancel_button.grid(row=0, column=1, sticky="ew")
    script_draft_cancel_button.state(["disabled"])

    action_frame = ttk.Frame(frame)
    action_frame.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(14, 8))
    action_frame.columnconfigure(0, weight=1)
    action_frame.columnconfigure(1, weight=1)
    action_frame.columnconfigure(2, weight=1)
    create_button = ttk.Button(
        action_frame,
        text="新しいジョブを作成",
        command=create_job,
    )
    create_button.grid(row=0, column=0, columnspan=2, sticky="ew", padx=(0, 4))
    prepare_button = ttk.Button(
        action_frame,
        text="音ハメ準備を開始",
        command=prepare_beat_sync_job,
    )
    prepare_button.grid(row=0, column=0, columnspan=2, sticky="ew", padx=(0, 4))
    open_button = ttk.Button(action_frame, text="既存ジョブを開く", command=open_job)
    open_button.grid(
        row=0,
        column=2,
        sticky="ew",
    )

    displayed_mode = JobMode.BEAT_SYNC.value

    def update_mode_fields(event=None) -> None:
        nonlocal current_transcribe_job, current_transcribe_model, completed_transcribe_inspection
        nonlocal current_script_draft_job, completed_script_draft_inspection, displayed_mode
        if displayed_mode in mode_defaults:
            mode_defaults[displayed_mode] = {
                "name": name_var.get(),
                "output_dir": output_var.get(),
            }
        selected_mode = mode_var.get()
        if (
            selected_mode != JobMode.TRANSCRIBE.value
            and completed_transcribe_inspection
            and active_cancel_event is None
        ):
            current_transcribe_job = None
            current_transcribe_model = None
            completed_transcribe_inspection = False
            transcribe_button.configure(text="文字起こしを開始")
            transcribe_button.state(["!disabled"])
        if (
            selected_mode != JobMode.SCRIPT_DRAFT.value
            and completed_script_draft_inspection
            and active_cancel_event is None
        ):
            current_script_draft_job = None
            completed_script_draft_inspection = False
            script_draft_button.configure(text="台本下書きを開始")
            script_draft_button.state(["!disabled"])
        if selected_mode in mode_defaults:
            name_var.set(mode_defaults[selected_mode]["name"])
            output_var.set(mode_defaults[selected_mode]["output_dir"])
        displayed_mode = selected_mode
        if mode_var.get() == JobMode.BEAT_SYNC.value:
            beat_frame.grid()
            transcribe_frame.grid_remove()
            script_draft_frame.grid_remove()
            prepare_button.grid()
            create_button.grid_remove()
        elif mode_var.get() == JobMode.TRANSCRIBE.value:
            beat_frame.grid_remove()
            transcribe_frame.grid()
            script_draft_frame.grid_remove()
            prepare_button.grid_remove()
            create_button.grid_remove()
        elif mode_var.get() == JobMode.SCRIPT_DRAFT.value:
            beat_frame.grid_remove()
            transcribe_frame.grid_remove()
            script_draft_frame.grid()
            prepare_button.grid_remove()
            create_button.grid_remove()
        else:
            beat_frame.grid_remove()
            transcribe_frame.grid_remove()
            script_draft_frame.grid_remove()
            prepare_button.grid_remove()
            create_button.grid()

    mode_box.bind("<<ComboboxSelected>>", update_mode_fields)
    update_mode_fields()

    ttk.Separator(frame).grid(row=5, column=0, columnspan=3, sticky="ew", pady=10)
    ttk.Label(frame, textvariable=status_var, wraplength=500).grid(
        row=6,
        column=0,
        columnspan=3,
        sticky="w",
    )
    frame.columnconfigure(1, weight=1)
    root.mainloop()
