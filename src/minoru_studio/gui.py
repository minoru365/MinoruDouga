from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from minoru_studio.jobs.model import JobManifest, JobMode
from minoru_studio.jobs.store import JobStore


class LauncherController:
    def __init__(self, store: JobStore | None = None):
        self.store = store or JobStore()

    def create_job(self, mode: str, name: str, output_dir: str) -> Path:
        return self.store.create(
            root=Path(output_dir),
            name=name,
            mode=JobMode(mode),
        )

    def inspect_job(self, job_dir: str) -> JobManifest:
        return self.store.load(Path(job_dir), recover_interrupted=False)


def launch_gui(controller: LauncherController | None = None) -> None:
    if controller is None:
        controller = LauncherController()
    root = tk.Tk()
    root.title("MinoruStudio")
    root.geometry("560x330")
    root.resizable(False, False)

    frame = ttk.Frame(root, padding=20)
    frame.pack(fill="both", expand=True)

    mode_var = tk.StringVar(value=JobMode.BEAT_SYNC.value)
    name_var = tk.StringVar(value="video-job")
    output_var = tk.StringVar(value=str(Path.home() / "Videos" / "MinoruStudio"))
    status_var = tk.StringVar(value="新しいジョブを作成するか、既存ジョブを開いてください。")

    ttk.Label(frame, text="固定モード").grid(row=0, column=0, sticky="w")
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

    ttk.Button(frame, text="新しいジョブを作成", command=create_job).grid(
        row=3, column=0, columnspan=2, sticky="ew", pady=(18, 8)
    )
    ttk.Button(frame, text="既存ジョブを開く", command=open_job).grid(
        row=3, column=2, sticky="ew", pady=(18, 8)
    )
    ttk.Separator(frame).grid(row=4, column=0, columnspan=3, sticky="ew", pady=10)
    ttk.Label(frame, textvariable=status_var, wraplength=500).grid(
        row=5, column=0, columnspan=3, sticky="w"
    )
    frame.columnconfigure(1, weight=1)
    root.mainloop()
