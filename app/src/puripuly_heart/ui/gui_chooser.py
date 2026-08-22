"""Startup dialog — choose which GUI to launch."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk


def ask_gui_choice() -> str:
    """Show a dialog asking which GUI to use. Returns 'flet' or 'tkinter'."""
    result = "flet"

    root = tk.Tk()
    root.title("PuriPuly Heart")
    root.geometry("360x180")
    root.resizable(False, False)
    root.attributes("-topmost", True)

    # Center on screen
    root.update_idletasks()
    x = (root.winfo_screenwidth() - 360) // 2
    y = (root.winfo_screenheight() - 180) // 2
    root.geometry(f"+{x}+{y}")

    frame = ttk.Frame(root, padding=24)
    frame.pack(fill=tk.BOTH, expand=True)

    ttk.Label(
        frame,
        text="Select interface:",
        font=("Segoe UI", 14, "bold"),
    ).pack(pady=(0, 16))

    btn_frame = ttk.Frame(frame)
    btn_frame.pack(fill=tk.X)

    def choose_flet():
        nonlocal result
        result = "flet"
        root.destroy()

    def choose_tkinter():
        nonlocal result
        result = "tkinter"
        root.destroy()

    ttk.Button(
        btn_frame,
        text="Flet (default)",
        command=choose_flet,
        width=16,
    ).pack(side=tk.LEFT, expand=True, padx=(0, 8))

    ttk.Button(
        btn_frame,
        text="Tkinter",
        command=choose_tkinter,
        width=16,
    ).pack(side=tk.LEFT, expand=True, padx=(8, 0))

    root.mainloop()
    return result
