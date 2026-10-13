import os
import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path


def human_size(size):
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.2f} {unit}"
        value /= 1024


def build_tree(root_path):
    root = Path(root_path)
    lines = [f"{root.name}/"]

    def walk(folder, prefix=""):
        try:
            entries = sorted(
                folder.iterdir(),
                key=lambda p: (p.is_file(), p.name.lower())
            )
        except PermissionError:
            lines.append(prefix + "`-- [ACCESS DENIED]")
            return
        except OSError as e:
            lines.append(prefix + f"`-- [ERROR: {e}]")
            return

        for index, entry in enumerate(entries):
            is_last = index == len(entries) - 1
            branch = "`-- " if is_last else "|-- "
            child_prefix = prefix + ("    " if is_last else "|   ")

            try:
                if entry.is_dir():
                    lines.append(prefix + branch + entry.name + "/")
                    walk(entry, child_prefix)
                else:
                    try:
                        size = human_size(entry.stat().st_size)
                        lines.append(prefix + branch + f"{entry.name} [{size}]")
                    except OSError:
                        lines.append(prefix + branch + entry.name)
            except OSError as e:
                lines.append(prefix + branch + f"{entry.name} [ERROR: {e}]")

    walk(root)
    return "\n".join(lines) + "\n"


def main():
    app = tk.Tk()
    app.withdraw()

    folder = filedialog.askdirectory(title="Select folder to scan")
    if not folder:
        return

    root = Path(folder)
    output = root.parent / f"{root.name}_Structure.txt"

    try:
        tree = build_tree(root)
        output.write_text(tree, encoding="utf-8")
    except Exception as e:
        messagebox.showerror("Folder Structure Scanner", str(e))
        return

    messagebox.showinfo(
        "Folder Structure Scanner",
        f"Saved:\n{output}"
    )

    try:
        os.startfile(output)
    except Exception:
        pass


if __name__ == "__main__":
    main()