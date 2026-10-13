#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Как оригинал, но при оценке длин блоков применяется полный HTML-энкодинг:
- спец. символы (&<>") экранируются через html.escape
- все символы с ord > 127 кодируются как числовые сущности &#NNN;
Это влияет только на подсчёт символов для разбиения; сохраняемый BBCode остаётся неизменным.
"""
import os
import re
import html
import tkinter as tk
from tkinter import filedialog

# Конфигурация
ANSI_ENCODING = "cp1251"
MAX_CHARS_PER_FILE = 120000

# ------------------------- чтение и определение кодировок -------------------------
def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()

def detect_encoding_from_list(data: bytes):
    if not data:
        return ("utf-8", "none")
    if data.startswith(b'\xef\xbb\xbf'):
        if b'\r\n' in data: return ("utf-8-sig", "crlf")
        if b'\n' in data: return ("utf-8-sig", "lf")
        return ("utf-8-sig", "none")
    if data.startswith(b'\xff\xfe'):
        if b'\r\x00\n\x00' in data: return ("utf-16-le", "crlf")
        if b'\n\x00' in data: return ("utf-16-le", "lf")
        return ("utf-16-le", "none")
    sample = data[:4096]
    even_nulls = sum(1 for i,b in enumerate(sample) if b == 0 and (i % 2 == 0))
    odd_nulls  = sum(1 for i,b in enumerate(sample) if b == 0 and (i % 2 == 1))
    total = max(1, len(sample))
    if odd_nulls / total > 0.03 and odd_nulls > even_nulls * 2.5:
        if b'\r\x00\n\x00' in data: return ("utf-16-le", "crlf")
        if b'\n\x00' in data: return ("utf-16-le", "lf")
        return ("utf-16-le", "none")
    try:
        decoded = data.decode("utf-8")
        if "\r\n" in decoded: return ("utf-8", "crlf")
        if "\n" in decoded: return ("utf-8", "lf")
        return ("utf-8", "none")
    except Exception:
        if b'\r\n' in data: return (ANSI_ENCODING, "crlf")
        if b'\n' in data: return (ANSI_ENCODING, "lf")
        return (ANSI_ENCODING, "none")

def decode_bytes_with_encoding(data: bytes, encoding: str):
    try:
        if encoding == "utf-8-sig":
            return data.decode("utf-8-sig")
        if encoding == "utf-16-le" or encoding == "utf-16":
            return data.decode("utf-16-le")
        return data.decode(encoding)
    except Exception:
        return data.decode(encoding, errors="replace")

def normalize_leading(text: str):
    if not text:
        return text
    if text.startswith('\ufeff'):
        text = text.lstrip('\ufeff')
        if not text:
            return text
    if text.startswith(' '):
        if len(text) >= 2 and text[1] not in ('\n', '\r', ' '):
            text = text[1:]
    return text

def read_text_preserve_newlines(path: str):
    raw = read_bytes(path)
    enc, nl = detect_encoding_from_list(raw)
    text = decode_bytes_with_encoding(raw, enc)
    text = normalize_leading(text)
    return text, enc, nl

# ------------------------- построение дерева и блоков -------------------------
def build_nodes(root_dir):
    nodes = {}
    for dirpath, dirnames, filenames in os.walk(root_dir):
        nodes[dirpath] = {
            "name": os.path.basename(dirpath) or dirpath,
            "subdirs": sorted([os.path.join(dirpath, d) for d in dirnames], key=lambda s: s.lower()),
            "files": sorted(filenames, key=lambda s: s.lower())
        }
    return nodes

def has_cue_or_log(files):
    return any(f.lower().endswith((".cue", ".log")) for f in files)

def compute_included(nodes, root_dir):
    included = set()
    all_dirs = sorted(nodes.keys(), key=lambda p: p.count(os.sep), reverse=True)
    for d in all_dirs:
        node = nodes[d]
        include = has_cue_or_log(node["files"])
        for child in node["subdirs"]:
            if child in included:
                include = True
                break
        if include:
            included.add(d)
    return included

def first_by_ext(files, dirpath, ext):
    for f in files:
        if f.lower().endswith(ext):
            return os.path.join(dirpath, f)
    return None

# --------------------- HTML-энкодинг для подсчёта длины ---------------------
def html_encode_for_count(s: str) -> str:
    """
    Возвращает строку, где:
    - спецсимволы & < > " заменены через html.escape
    - все символы с ord > 127 заменены на числовые сущности &#NNN;
    Newline остаётся '\n' (не заменяется на <br>) чтобы учитывать их как один символ.
    """
    # сначала экранируем &, <, >, " (чтобы не мешать числовым заменам)
    escaped = html.escape(s, quote=True)
    # затем заменяем все не-ASCII символы на &#NNN;
    # делаем это по символам исходной escaped строки (note: escaped содержит только ASCII + &...;)
    out_chars = []
    for ch in escaped:
        if ord(ch) > 127:
            out_chars.append(f'&#{ord(ch)};')
        else:
            out_chars.append(ch)
    return "".join(out_chars)

# Блоки: "open_spoiler" (arg=name), "close_spoiler", "pre" (arg=content)
class Block:
    def __init__(self, typ, arg=None):
        self.typ = typ
        self.arg = arg

    # Длина для разбиения: считаем длину строки после полного HTML-энкодинга
    def approximate_length(self):
        if self.typ == "open_spoiler":
            s = f'[spoiler="{self.arg}"]\n'
            enc = html_encode_for_count(s)
            return len(enc)
        if self.typ == "close_spoiler":
            s = '[/spoiler]\n'
            enc = html_encode_for_count(s)
            return len(enc)
        if self.typ == "pre":
            s = '[pre]\n' + (self.arg or "") + '\n[/pre]\n'
            enc = html_encode_for_count(s)
            return len(enc)
        return 0

    # Рендер возвращает исходный BBCode (без HTML-энкодинга) — для записи в файл
    def render(self):
        if self.typ == "open_spoiler":
            return f'[spoiler="{self.arg}"]\n'
        if self.typ == "close_spoiler":
            return '[/spoiler]\n'
        if self.typ == "pre":
            return '[pre]\n' + (self.arg or "") + '\n[/pre]\n'
        return ''

def generate_blocks_for_dir(dirpath, nodes):
    node = nodes.get(dirpath)
    if node is None:
        return []
    files_lower = [f.lower() for f in node["files"]]
    audiochecker_present = "audiochecker.log" in files_lower
    log_label = "Лог проверки качества:" if audiochecker_present else "Лог создания рипа:"

    # выбираем .log: prefer audiochecker.log, иначе первый найденный .log
    logf = None
    for f in node["files"]:
        if f.lower().endswith(".log"):
            if f.lower() == "audiochecker.log":
                logf = os.path.join(dirpath, f)
                break
            if logf is None:
                logf = os.path.join(dirpath, f)
    cuef = None
    for f in node["files"]:
        if f.lower().endswith(".cue"):
            cuef = os.path.join(dirpath, f)
            break

    blocks = []
    # если ни .log ни .cue — ничего не добавляем
    if not (logf or cuef):
        return blocks

    # добавляем блок логa только если файл .log найден
    if logf:
        blocks.append(Block("open_spoiler", log_label))
        text, enc, nl = read_text_preserve_newlines(logf)
        blocks.append(Block("pre", text))
        blocks.append(Block("close_spoiler"))

    # добавляем блок cue только если файл .cue найден
    if cuef:
        blocks.append(Block("open_spoiler", "Содержание индексной карты (.CUE):"))
        text, enc, nl = read_text_preserve_newlines(cuef)
        blocks.append(Block("pre", text))
        blocks.append(Block("close_spoiler"))

    return blocks

def collect_structure_as_blocks(root_dir, nodes):
    included = compute_included(nodes, root_dir)
    if root_dir not in included:
        return []
    blocks = []
    def recurse(dirpath):
        node = nodes.get(dirpath)
        if node is None:
            return
        if has_cue_or_log(node["files"]):
            blocks.extend(generate_blocks_for_dir(dirpath, nodes))
        for child in node["subdirs"]:
            if child not in included:
                continue
            child_name = nodes[child]["name"]
            blocks.append(Block("open_spoiler", child_name))
            recurse(child)
            blocks.append(Block("close_spoiler"))
    recurse(root_dir)
    return blocks

# ------------------ вычисление соответствий open/close и размеров поддеревьев ------------------
def compute_matching_and_subtree_sizes(blocks):
    match = {}
    stack = []
    for i, b in enumerate(blocks):
        if b.typ == "open_spoiler":
            stack.append(i)
        elif b.typ == "close_spoiler":
            if not stack:
                continue
            o = stack.pop()
            match[o] = i
    subtree_size = {}
    for o, c in match.items():
        s = sum(blocks[j].approximate_length() for j in range(o, c+1))
        subtree_size[o] = s
    return match, subtree_size

# ---------------------- логическое разбиение блоков (спойлеры не разрываются) ----------------------
def split_blocks_into_parts(blocks, limit):
    match, subtree_size = compute_matching_and_subtree_sizes(blocks)
    parts = []
    current = []
    current_len = 0
    open_stack = []
    i = 0
    n = len(blocks)
    while i < n:
        b = blocks[i]
        blen = b.approximate_length()
        if b.typ == "open_spoiler":
            subtree = subtree_size.get(i, None)
            if subtree is not None and current_len + subtree > limit and current:
                for _ in range(len(open_stack)):
                    current.append(Block("close_spoiler"))
                parts.append(current)
                current = []
                current_len = 0
                for name in open_stack:
                    current.append(Block("open_spoiler", name))
                    current_len += Block("open_spoiler", name).approximate_length()
            current.append(b)
            open_stack.append(b.arg)
            current_len += blen
            i += 1
            continue
        if b.typ == "close_spoiler":
            if open_stack:
                open_stack.pop()
            current.append(b)
            current_len += blen
            i += 1
            continue
        if b.typ == "pre":
            if current_len + blen > limit and current:
                for _ in range(len(open_stack)):
                    current.append(Block("close_spoiler"))
                parts.append(current)
                current = []
                current_len = 0
                for name in open_stack:
                    current.append(Block("open_spoiler", name))
                    current_len += Block("open_spoiler", name).approximate_length()
            current.append(b)
            current_len += blen
            i += 1
            continue
    if current:
        for _ in range(len(open_stack)):
            current.append(Block("close_spoiler"))
        parts.append(current)
    return parts

def render_part_content(blocks):
    return "".join(b.render() for b in blocks)

def write_parts_with_rootname(base_folder, root_name, parts):
    paths = []
    safe_root = root_name.strip()
    safe_root = re.sub(r'[\\/:*?"<>|]', '_', safe_root)
    multiple = len(parts) > 1
    for idx, part in enumerate(parts, start=1):
        if multiple:
            top_title = f'Логи, куе часть {idx}'
        else:
            top_title = 'Логи, куе'
        content = f'[spoiler="{top_title}"]\n' + render_part_content(part) + '[/spoiler]\n'
        if multiple:
            name = f"{safe_root} log cue, part {idx}.txt"
        else:
            name = f"{safe_root} log cue.txt"
        path = os.path.join(base_folder, name)
        with open(path, "w", encoding="utf-8-sig", newline='') as f:
            f.write(content)
        paths.append(os.path.abspath(path))
    return paths

# ---------------------- основная логика ----------------------
def main():
    root = tk.Tk()
    root.withdraw()
    folder = filedialog.askdirectory()
    root.destroy()
    if not folder:
        print("Папка не выбрана. Выход.")
        return
    nodes = build_nodes(folder)
    blocks = collect_structure_as_blocks(folder, nodes)
    if not blocks:
        print("Нет .log или .cue для включения. Файл не создан.")
        return
    parts = split_blocks_into_parts(blocks, MAX_CHARS_PER_FILE)
    root_name = os.path.basename(folder) or folder
    written = write_parts_with_rootname(folder, root_name, parts)
    print("Готово. Создано файлов:", len(written))
    for p in written:
        print(p)

if __name__ == "__main__":
    main()