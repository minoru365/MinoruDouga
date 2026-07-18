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


class LauncherController:
    def __init__(
        self,
        store: JobStore | None = None,
        beat_sync_service: BeatSyncService | None = None,
    ):
        self.store = store or JobStore()
        self.beat_sync_service = (
            beat_sync_service if beat_sync_service is not None else BeatSyncService()
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


def launch_gui(controller: LauncherController | None = None) -> None:
    if controller is None:
        controller = LauncherController()
    root = tk.Tk()
    root.title("MinoruStudio")
    root.geometry("720x560")
    root.resizable(False, False)

    frame = ttk.Frame(root, padding=20)
    frame.pack(fill="both", expand=True)

    saved = load_beat_sync_settings()
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
    status_var = tk.StringVar(value="新しいジョブを作成するか、既存ジョブを開いてください。")

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
    ttk.Entry(frame, textvariable=name_var, width=31).grid(
        row=1, column=1, columnspan=2, sticky="ew", pady=4
    )

    ttk.Label(frame, text="保存先").grid(row=2, column=0, sticky="w")
    ttk.Entry(frame, textvariable=output_var, width=31).grid(
        row=2, column=1, sticky="ew", pady=4
    )

    def choose_output() -> None:
        selected = filedialog.askdirectory(initialdir=output_var.get())
        if selected:
            output_var.set(selected)

    ttk.Button(frame, text="選択", command=choose_output).grid(row=2, column=2, padx=4)

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

    def create_job() -> None:
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

    def open_job() -> None:
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
    ttk.Button(action_frame, text="既存ジョブを開く", command=open_job).grid(
        row=0,
        column=2,
        sticky="ew",
    )

    def update_mode_fields(event=None) -> None:
        if mode_var.get() == JobMode.BEAT_SYNC.value:
            beat_frame.grid()
            prepare_button.grid()
            create_button.grid_remove()
        else:
            beat_frame.grid_remove()
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
