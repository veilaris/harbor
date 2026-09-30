"""Harbor — лаунчер проектов для VS Code.

Слева — свои «папки» (группы), справа — проекты в выбранной папке.
Клик по проекту открывает его в VS Code. Всё хранится в harbor.json рядом со скриптом.
"""
import ctypes
import ctypes.wintypes
import json
import os
import re
import shutil
import subprocess
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

APP_DIR = Path(__file__).resolve().parent
DATA_FILE = APP_DIR / "harbor.json"
ICON_FILE = APP_DIR / "harbor.ico"
LOGO_FILE = APP_DIR / "logo.png"

# неоновые палитры: бирюзовый / фиолетовый / розовый акценты на тёмно-синем или светлом фоне
THEMES = {
    "dark": {
        "IS_DARK": True,
        "BG": "#0b1026",
        "SIDEBAR_BG": "#0e1430",
        "CARD_BG": "#131a3d",
        "CARD_HOVER": "#1a2352",
        "BORDER": "#26306a",
        "SELECT_BG": "#2a2168",
        "SELECT_FG": "#3ee8f0",
        "CYAN": "#3ee8f0",
        "CYAN_HOVER": "#9af5f8",
        "NAME_HOVER": "#9af5f8",
        "BTN_FG": "#0b1026",
        "VIOLET": "#8b6cff",
        "PINK": "#e38df5",
        "TEXT": "#e7ecff",
        "MUTED": "#7d88b8",
        "ERROR": "#ff6b9a",
        "ERROR_HOVER": "#ff9ab8",
    },
    "light": {
        "IS_DARK": False,
        "BG": "#f5f6fd",
        "SIDEBAR_BG": "#ebeefa",
        "CARD_BG": "#ffffff",
        "CARD_HOVER": "#effbfc",
        "BORDER": "#d9ddf2",
        "SELECT_BG": "#e3dcfd",
        "SELECT_FG": "#5b3fd9",
        "CYAN": "#0aa5b5",
        "CYAN_HOVER": "#14bccc",
        "NAME_HOVER": "#08899a",
        "BTN_FG": "#ffffff",
        "VIOLET": "#7b5cf0",
        "PINK": "#c24fdc",
        "TEXT": "#1b2045",
        "MUTED": "#6b7399",
        "ERROR": "#e0406f",
        "ERROR_HOVER": "#ea6a91",
    },
}


# текущие цвета; заполняются из THEMES в apply_theme()
IS_DARK = True
BG = SIDEBAR_BG = CARD_BG = CARD_HOVER = BORDER = SELECT_BG = SELECT_FG = ""
CYAN = CYAN_HOVER = NAME_HOVER = BTN_FG = VIOLET = PINK = ""
TEXT = MUTED = ERROR = ERROR_HOVER = ""


def apply_theme(name):
    """Делает цвета выбранной темы текущими (BG, CYAN, ...)."""
    globals().update(THEMES[name])


def system_theme():
    """Тема Windows по умолчанию: светлая или тёмная."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return "light" if winreg.QueryValueEx(key, "AppsUseLightTheme")[0] else "dark"
    except OSError:
        return "dark"


apply_theme("dark")

FONT = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI Semibold", 11)
FONT_TITLE = ("Segoe UI Semibold", 16)
FONT_LOGO = ("Segoe UI Semibold", 15)
FONT_CAPS = ("Segoe UI Semibold", 8)
FONT_SMALL = ("Segoe UI", 8)


# ---------- данные ----------

def load_data():
    if not DATA_FILE.exists():
        return {"groups": [], "last_group": 0}
    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data.get("groups"), list):
            raise ValueError("нет списка groups")
        return data
    except (OSError, ValueError) as e:
        backup = DATA_FILE.with_suffix(".broken.json")
        shutil.copyfile(DATA_FILE, backup)
        messagebox.showwarning(
            "Harbor",
            f"Не удалось прочитать {DATA_FILE.name}: {e}\n"
            f"Копия сохранена в {backup.name}, начинаю с пустого списка.",
        )
        return {"groups": [], "last_group": 0}


def save_data(data):
    tmp = DATA_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DATA_FILE)


# ---------- VS Code ----------

def find_vscode():
    """Возвращает путь к Code.exe (без консольного окна) или к code.cmd."""
    candidates = []
    cli = shutil.which("code")
    if cli:
        candidates.append(Path(cli).resolve().parent.parent / "Code.exe")
    for env in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(env)
        if base:
            candidates.append(Path(base) / "Programs" / "Microsoft VS Code" / "Code.exe")
            candidates.append(Path(base) / "Microsoft VS Code" / "Code.exe")
    for c in candidates:
        if c.is_file():
            return str(c)
    return cli


def open_in_vscode(path):
    exe = find_vscode()
    if not exe:
        raise FileNotFoundError("VS Code не найден. Установи его или добавь `code` в PATH.")
    env = os.environ.copy()
    env.pop("ELECTRON_RUN_AS_NODE", None)
    subprocess.Popen(
        [exe, path],
        env=env,
        creationflags=subprocess.CREATE_NO_WINDOW,
        close_fds=True,
    )


# ---------- оформление ----------

def _colorref(hex_color):
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return b << 16 | g << 8 | r


def style_title_bar(win):
    """Заголовок окна в цвет темы и неоновая рамка (Windows 10/11)."""
    try:
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        dwm = ctypes.windll.dwmapi

        def set_attr(attr, value):
            v = ctypes.c_int(value)
            return dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))

        if set_attr(20, int(IS_DARK)) != 0:  # DWMWA_USE_IMMERSIVE_DARK_MODE
            set_attr(19, int(IS_DARK))       # то же для старых сборок Win10
        set_attr(35, _colorref(BG))          # цвет заголовка (Win11)
        set_attr(34, _colorref(VIOLET))      # цвет рамки (Win11)
        # SWP_FRAMECHANGED | NOMOVE | NOSIZE | NOZORDER | NOACTIVATE — перерисовать рамку сразу
        ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x37)
    except (AttributeError, OSError):
        pass


def make_button(parent, text, command, kind="primary"):
    styles = {
        "primary": (CYAN, BTN_FG, CYAN_HOVER),
        "ghost": (parent["bg"], PINK, CARD_HOVER),
        "subtle": (CARD_HOVER, TEXT, BORDER),
        "danger": (ERROR, BTN_FG, ERROR_HOVER),
    }
    bg, fg, hover = styles[kind]
    btn = tk.Button(parent, text=text, command=command, bg=bg, fg=fg, font=FONT_BOLD,
                    bd=0, relief="flat", padx=14, pady=5, cursor="hand2",
                    activebackground=hover, activeforeground=fg)
    btn.bind("<Enter>", lambda e: btn.configure(bg=hover))
    btn.bind("<Leave>", lambda e: btn.configure(bg=bg))
    return btn


class Tooltip:
    """Подсказка при наведении."""

    def __init__(self, widget, text):
        self.widget, self.text, self.tip = widget, text, None
        widget.bind("<Enter>", self.show, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<Button-1>", self.hide, add="+")

    def show(self, _event=None):
        if self.tip:
            return
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        x = self.widget.winfo_rootx()
        y = self.widget.winfo_rooty() - self.widget.winfo_height() - 4
        self.tip.geometry(f"+{x}+{y}")
        tk.Label(self.tip, text=self.text, bg=CARD_BG, fg=TEXT, font=FONT_SMALL, padx=8, pady=3,
                 highlightthickness=1, highlightbackground=BORDER).pack()

    def hide(self, _event=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None


def make_menu(parent):
    return tk.Menu(parent, tearoff=0, bg=CARD_BG, fg=TEXT, activebackground=SELECT_BG,
                   activeforeground=CYAN, bd=0, font=FONT)


class Dialog(tk.Toplevel):
    """Тёмный диалог вместо стандартных светлых окон Windows."""

    def __init__(self, parent, title, message, initial=None, buttons=(("OK", True, "primary"),)):
        super().__init__(parent)
        self.withdraw()
        self.title(title)
        self.configure(bg=CARD_BG, highlightthickness=1, highlightbackground=BORDER)
        self.resizable(False, False)
        self.transient(parent)
        self.result = None
        self.var = None

        tk.Label(self, text=message, bg=CARD_BG, fg=TEXT, font=FONT, justify="left",
                 wraplength=380, anchor="w").pack(fill="x", padx=22, pady=(20, 10))

        entry = None
        if initial is not None:
            self.var = tk.StringVar(value=initial)
            entry = tk.Entry(self, textvariable=self.var, bg=BG, fg=TEXT, insertbackground=CYAN,
                             selectbackground=SELECT_BG, selectforeground=CYAN, relief="flat",
                             font=("Segoe UI", 11), width=38, highlightthickness=1,
                             highlightbackground=BORDER, highlightcolor=CYAN)
            entry.pack(fill="x", padx=22, pady=(0, 6), ipady=6)

        row = tk.Frame(self, bg=CARD_BG)
        row.pack(fill="x", padx=22, pady=(12, 18))
        for text, value, kind in reversed(buttons):
            make_button(row, text, lambda v=value: self.finish(v), kind).pack(side="right", padx=(8, 0))

        self.bind("<Return>", lambda e: self.finish(buttons[0][1]))
        self.bind("<Escape>", lambda e: self.finish(None))
        self.protocol("WM_DELETE_WINDOW", lambda: self.finish(None))

        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_reqwidth()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_reqheight()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.deiconify()
        style_title_bar(self)
        self.grab_set()
        if entry:
            entry.focus_set()
            entry.select_range(0, "end")
        else:
            self.focus_set()
        self.wait_window()

    def finish(self, value):
        if value is True and self.var is not None:
            value = self.var.get().strip() or None
        self.result = value
        self.grab_release()
        self.destroy()


def ask_string(parent, title, prompt, initial=""):
    return Dialog(parent, title, prompt, initial=initial,
                  buttons=(("Готово", True, "primary"), ("Отмена", None, "subtle"))).result


def ask_yes_no(parent, title, message):
    return Dialog(parent, title, message,
                  buttons=(("Да", True, "danger"), ("Отмена", None, "subtle"))).result is True


def show_message(parent, title, message):
    Dialog(parent, title, message)


# ---------- интерфейс ----------

class Harbor(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Harbor")
        self.k = k = self.winfo_fpixels("1i") / 96  # масштаб экрана (100% = 1.0)
        self.geometry(f"{int(860 * k)}x{int(560 * k)}")
        self.minsize(int(600 * k), int(380 * k))
        if ICON_FILE.exists():
            self.iconbitmap(default=str(ICON_FILE))
        self.logo = tk.PhotoImage(file=str(LOGO_FILE)) if LOGO_FILE.exists() else None

        self.style = ttk.Style(self)
        self.style.theme_use("clam")
        self.style.layout("Neon.Vertical.TScrollbar", [(
            "Vertical.Scrollbar.trough",
            {"sticky": "ns", "children": [("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]},
        )])

        self.data = load_data()
        if self.data.get("theme") not in THEMES:
            self.data["theme"] = system_theme()
        self.restore_window()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        last = self.data.get("last_group", 0)
        self.build_ui(select=last if isinstance(last, int) else 0)

    def restore_window(self):
        """Возвращает размер и положение окна с прошлого запуска."""
        win = self.data.get("window")
        if not isinstance(win, dict):
            return
        try:
            w, h = int(win["width"]), int(win["height"])
            geometry = f"{max(w, self.minsize()[0])}x{max(h, self.minsize()[1])}"
            x, y = int(win["x"]), int(win["y"])
            # позицию берём, только если окно окажется на одном из мониторов
            point = ctypes.wintypes.POINT(x + 60, y + 20)
            if ctypes.windll.user32.MonitorFromPoint(point, 0):  # MONITOR_DEFAULTTONULL
                geometry += f"+{x}+{y}"
            self.geometry(geometry)
        except (KeyError, TypeError, ValueError, AttributeError, OSError):
            pass
        if win.get("zoomed"):
            self.state("zoomed")

    def on_close(self):
        """Запоминает размер окна при закрытии."""
        state = self.state()
        win = self.data.get("window")
        if not isinstance(win, dict):
            win = self.data["window"] = {}
        if state == "normal":
            m = re.fullmatch(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", self.geometry())
            if m:
                win.update(zip(("width", "height", "x", "y"), map(int, m.groups())))
        if state != "iconic":  # свёрнутое в панель задач окно не меняет прошлое состояние
            win["zoomed"] = state == "zoomed"
        self.save()
        self.destroy()

    def build_ui(self, select=0):
        """Строит (или перестраивает после смены темы) всё окно."""
        apply_theme(self.data["theme"])
        for w in self.winfo_children():
            w.destroy()
        self.configure(bg=BG)
        self.style.configure("Neon.Vertical.TScrollbar", troughcolor=BG, background=BORDER,
                             bordercolor=BG, lightcolor=BORDER, darkcolor=BORDER, gripcount=0, width=8)
        self.style.map("Neon.Vertical.TScrollbar", background=[("active", VIOLET), ("pressed", CYAN)])

        self._build_sidebar()
        self._build_main()
        self.refresh_groups(select=select)
        style_title_bar(self)

    def toggle_theme(self):
        self.data["theme"] = "light" if IS_DARK else "dark"
        self.save()
        self.build_ui(select=self.current_group_index() or 0)

    # --- левая колонка: папки ---

    def _build_sidebar(self):
        side = tk.Frame(self, bg=SIDEBAR_BG, width=int(230 * self.k))
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        tk.Frame(self, bg=BORDER, width=1).pack(side="left", fill="y")

        brand = tk.Frame(side, bg=SIDEBAR_BG)
        brand.pack(fill="x", padx=16, pady=(18, 16))
        if self.logo:
            tk.Label(brand, image=self.logo, bg=SIDEBAR_BG).pack(side="left")
        tk.Label(brand, text="Harbor", bg=SIDEBAR_BG, fg=CYAN, font=FONT_LOGO).pack(side="left", padx=(10, 0))

        tk.Label(side, text="ПАПКИ", bg=SIDEBAR_BG, fg=MUTED, font=FONT_CAPS,
                 anchor="w").pack(fill="x", padx=20, pady=(0, 6))

        self.groups_box = tk.Listbox(
            side, bg=SIDEBAR_BG, fg=TEXT, font=("Segoe UI", 11), bd=0,
            highlightthickness=0, activestyle="none", selectbackground=SELECT_BG,
            selectforeground=SELECT_FG, exportselection=False, cursor="hand2",
        )
        self.groups_box.pack(fill="both", expand=True, padx=10)
        self.groups_box.bind("<<ListboxSelect>>", lambda e: self.show_projects())
        self.groups_box.bind("<Double-Button-1>", lambda e: self.rename_group())
        self.groups_box.bind("<Button-3>", self._group_menu)

        bottom = tk.Frame(side, bg=SIDEBAR_BG)
        bottom.pack(fill="x", padx=10, pady=12)
        make_button(bottom, "+  Новая папка", self.add_group, "ghost").pack(side="left", fill="x", expand=True)
        theme_btn = make_button(bottom, "☀" if IS_DARK else "☾", self.toggle_theme, "ghost")
        theme_btn.configure(font=("Segoe UI Symbol", 13), padx=8, pady=0)
        theme_btn.pack(side="right")
        Tooltip(theme_btn, "Светлая тема" if IS_DARK else "Тёмная тема")

        self.group_menu = make_menu(self)
        self.group_menu.add_command(label="Переименовать", command=self.rename_group)
        self.group_menu.add_command(label="Выше", command=lambda: self.move_group(-1))
        self.group_menu.add_command(label="Ниже", command=lambda: self.move_group(1))
        self.group_menu.add_separator()
        self.group_menu.add_command(label="Удалить папку", command=self.delete_group)

    def _group_menu(self, event):
        idx = self.groups_box.nearest(event.y)
        if idx < 0 or not self.data["groups"]:
            return
        self.groups_box.selection_clear(0, "end")
        self.groups_box.selection_set(idx)
        self.show_projects()
        self.group_menu.tk_popup(event.x_root, event.y_root)

    def current_group_index(self):
        sel = self.groups_box.curselection()
        return sel[0] if sel else None

    def current_group(self):
        idx = self.current_group_index()
        return self.data["groups"][idx] if idx is not None else None

    def refresh_groups(self, select=None):
        self.groups_box.delete(0, "end")
        for g in self.data["groups"]:
            self.groups_box.insert("end", f"   {g['name']}   ·  {len(g['projects'])}")
        if self.data["groups"]:
            if select is None:
                select = self.current_group_index() or 0
            select = max(0, min(select, len(self.data["groups"]) - 1))
            self.groups_box.selection_set(select)
            self.groups_box.see(select)
        self.show_projects()

    def add_group(self):
        name = ask_string(self, "Новая папка", "Название папки:")
        if not name:
            return
        self.data["groups"].append({"name": name, "projects": []})
        self.save()
        self.refresh_groups(select=len(self.data["groups"]) - 1)

    def rename_group(self):
        g = self.current_group()
        if not g:
            return
        name = ask_string(self, "Переименовать", "Новое название папки:", g["name"])
        if name:
            g["name"] = name
            self.save()
            self.refresh_groups(select=self.current_group_index())

    def move_group(self, delta):
        idx = self.current_group_index()
        if idx is None:
            return
        new = idx + delta
        groups = self.data["groups"]
        if 0 <= new < len(groups):
            groups[idx], groups[new] = groups[new], groups[idx]
            self.save()
            self.refresh_groups(select=new)

    def delete_group(self):
        g = self.current_group()
        if not g:
            return
        msg = f"Удалить папку «{g['name']}»?"
        if g["projects"]:
            msg += f"\n\nВ ней {len(g['projects'])} проект(ов). Сами файлы на диске не трогаются."
        if ask_yes_no(self, "Удалить папку", msg):
            idx = self.current_group_index()
            del self.data["groups"][idx]
            self.save()
            self.refresh_groups(select=idx - 1 if idx else 0)

    # --- правая часть: проекты ---

    def _build_main(self):
        main = tk.Frame(self, bg=BG)
        main.pack(side="left", fill="both", expand=True)

        header = tk.Frame(main, bg=BG)
        header.pack(fill="x", padx=24, pady=(20, 10))
        self.title_label = tk.Label(header, text="", bg=BG, fg=TEXT, font=FONT_TITLE, anchor="w")
        self.title_label.pack(side="left")
        self.add_btn = make_button(header, "+  Добавить проект", self.add_project, "primary")
        self.add_btn.pack(side="right")

        self.glow = tk.Canvas(main, bg=BG, height=2, highlightthickness=0)
        self.glow.pack(fill="x", padx=24, pady=(0, 14))
        self.glow.bind("<Configure>", self._draw_glow)

        wrap = tk.Frame(main, bg=BG)
        wrap.pack(fill="both", expand=True, padx=(24, 6), pady=(0, 4))
        self.canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0)
        scroll = ttk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview,
                               style="Neon.Vertical.TScrollbar")

        def on_scroll(first, last):
            # полоса прокрутки видна только когда список не помещается
            if float(first) <= 0 and float(last) >= 1:
                scroll.pack_forget()
            elif not scroll.winfo_ismapped():
                scroll.pack(side="right", fill="y", before=self.canvas)
            scroll.set(first, last)

        self.canvas.configure(yscrollcommand=on_scroll)
        self.canvas.pack(side="left", fill="both", expand=True)

        self.list_frame = tk.Frame(self.canvas, bg=BG)
        self._win = self.canvas.create_window((0, 0), window=self.list_frame, anchor="nw")
        self.list_frame.bind("<Configure>", lambda e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        self.bind_all("<MouseWheel>", self._on_wheel)

        self.status = tk.Label(main, text="", bg=BG, fg=CYAN, font=FONT_SMALL, anchor="w")
        self.status.pack(fill="x", padx=24, pady=(0, 10))

        self.project_menu = make_menu(self)

    def _draw_glow(self, event):
        """Градиентная неоновая линия: бирюзовый → фиолетовый → розовый."""
        c = self.glow
        c.delete("all")
        stops = [CYAN, VIOLET, PINK]
        w = max(event.width, 1)
        for x in range(0, w, 2):
            t = x / w * (len(stops) - 1)
            i = min(int(t), len(stops) - 2)
            f = t - i
            a, b = stops[i], stops[i + 1]
            rgb = [round(int(a[k:k + 2], 16) * (1 - f) + int(b[k:k + 2], 16) * f) for k in (1, 3, 5)]
            c.create_line(x, 1, x + 2, 1, fill="#%02x%02x%02x" % tuple(rgb), width=2)

    def _on_wheel(self, event):
        if self.list_frame.winfo_height() > self.canvas.winfo_height():
            self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def show_projects(self):
        for w in self.list_frame.winfo_children():
            w.destroy()
        self.canvas.yview_moveto(0)

        g = self.current_group()
        if g is None:
            self.title_label.config(text="Создай первую папку слева")
            self.add_btn.pack_forget()
            return
        self.add_btn.pack(side="right")
        self.title_label.config(text=g["name"])
        self.data["last_group"] = self.current_group_index()
        self.save()

        if not g["projects"]:
            tk.Label(self.list_frame, text="Пока пусто.\nНажми «+ Добавить проект» и выбери папку проекта.",
                     bg=BG, fg=MUTED, font=FONT, justify="left", anchor="w").pack(fill="x", pady=20)
            return

        for i, p in enumerate(g["projects"]):
            self._project_row(i, p)

    def _project_row(self, index, project):
        exists = os.path.isdir(project["path"])
        row = tk.Frame(self.list_frame, bg=CARD_BG, cursor="hand2" if exists else "arrow",
                       highlightthickness=1, highlightbackground=BORDER)
        row.pack(fill="x", pady=4, padx=(0, 12))

        strip_color = VIOLET if exists else ERROR
        strip = tk.Frame(row, bg=strip_color, width=3)
        strip.pack(side="left", fill="y")

        body = tk.Frame(row, bg=CARD_BG)
        body.pack(side="left", fill="both", expand=True)
        name = tk.Label(body, text=project["name"], bg=CARD_BG, fg=TEXT if exists else MUTED,
                        font=FONT_BOLD, anchor="w")
        name.pack(fill="x", padx=14, pady=(9, 0))
        path_text = project["path"] if exists else f"{project['path']}   — папка не найдена"
        path = tk.Label(body, text=path_text, bg=CARD_BG, fg=MUTED if exists else ERROR,
                        font=FONT_SMALL, anchor="w")
        path.pack(fill="x", padx=14, pady=(0, 9))

        widgets = (row, strip, body, name, path)
        card_widgets = (body, name, path)

        def hover(on):
            for w in card_widgets:
                w.configure(bg=CARD_HOVER if on else CARD_BG)
            row.configure(highlightbackground=(CYAN if on else BORDER) if exists else BORDER)
            strip.configure(bg=(CYAN if on else VIOLET) if exists else ERROR)
            name.configure(fg=(NAME_HOVER if on else TEXT) if exists else MUTED)

        for w in widgets:
            w.bind("<Enter>", lambda e: hover(True))
            w.bind("<Leave>", lambda e: hover(False))
            w.bind("<Button-1>", lambda e: self.open_project(project))
            w.bind("<Button-3>", lambda e: self._show_project_menu(e, index))

    def _show_project_menu(self, event, index):
        g = self.current_group()
        project = g["projects"][index]
        m = self.project_menu
        m.delete(0, "end")
        m.add_command(label="Открыть в VS Code", command=lambda: self.open_project(project))
        m.add_command(label="Показать в проводнике", command=lambda: self.open_explorer(project))
        m.add_command(label="Переименовать", command=lambda: self.rename_project(index))
        m.add_command(label="Сменить путь…", command=lambda: self.change_project_path(index))
        if len(self.data["groups"]) > 1:
            move = make_menu(m)
            for gi, other in enumerate(self.data["groups"]):
                if other is not g:
                    move.add_command(label=other["name"],
                                     command=lambda gi=gi: self.move_project(index, gi))
            m.add_cascade(label="Переместить в папку", menu=move)
        m.add_separator()
        m.add_command(label="Убрать из списка", command=lambda: self.remove_project(index))
        m.tk_popup(event.x_root, event.y_root)

    def add_project(self):
        g = self.current_group()
        if g is None:
            return
        folder = filedialog.askdirectory(parent=self, title="Выбери папку проекта", mustexist=True)
        if not folder:
            return
        folder = os.path.normpath(folder)
        if any(os.path.normcase(p["path"]) == os.path.normcase(folder) for p in g["projects"]):
            show_message(self, "Harbor", "Этот проект уже есть в этой папке.")
            return
        g["projects"].append({"name": os.path.basename(folder) or folder, "path": folder})
        g["projects"].sort(key=lambda p: p["name"].casefold())
        self.save()
        self.refresh_groups(select=self.current_group_index())
        self.set_status(f"Добавлен: {folder}")

    def open_project(self, project):
        if not os.path.isdir(project["path"]):
            show_message(
                self, "Harbor",
                f"Папка не найдена:\n{project['path']}\n\n"
                "Правый клик по проекту → «Сменить путь…» или «Убрать из списка».",
            )
            return
        try:
            open_in_vscode(project["path"])
            self.set_status(f"Открываю {project['name']} в VS Code…")
        except OSError as e:
            show_message(self, "Harbor", str(e))

    def open_explorer(self, project):
        if os.path.isdir(project["path"]):
            os.startfile(project["path"])

    def rename_project(self, index):
        g = self.current_group()
        p = g["projects"][index]
        name = ask_string(self, "Переименовать", "Как показывать проект:", p["name"])
        if name:
            p["name"] = name
            g["projects"].sort(key=lambda x: x["name"].casefold())
            self.save()
            self.show_projects()

    def change_project_path(self, index):
        p = self.current_group()["projects"][index]
        start = p["path"] if os.path.isdir(p["path"]) else None
        folder = filedialog.askdirectory(parent=self, title="Новая папка проекта",
                                         initialdir=start, mustexist=True)
        if folder:
            p["path"] = os.path.normpath(folder)
            self.save()
            self.show_projects()

    def move_project(self, index, target_group):
        g = self.current_group()
        project = g["projects"].pop(index)
        target = self.data["groups"][target_group]["projects"]
        target.append(project)
        target.sort(key=lambda x: x["name"].casefold())
        self.save()
        self.refresh_groups(select=self.current_group_index())

    def remove_project(self, index):
        g = self.current_group()
        p = g["projects"][index]
        if ask_yes_no(self, "Убрать проект",
                      f"Убрать «{p['name']}» из списка?\nФайлы на диске не удаляются."):
            del g["projects"][index]
            self.save()
            self.refresh_groups(select=self.current_group_index())

    # --- прочее ---

    def set_status(self, text):
        self.status.config(text=text)
        self.after(4000, lambda: self.status.config(text="") if self.status["text"] == text else None)

    def save(self):
        try:
            save_data(self.data)
        except OSError as e:
            show_message(self, "Harbor", f"Не удалось сохранить {DATA_FILE.name}:\n{e}")


if __name__ == "__main__":
    try:
        # чтобы в панели задач была своя иконка, а не иконка Python
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Harbor.ProjectLauncher")
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # чёткий текст при масштабе 125%/150%
    except (AttributeError, OSError):
        pass
    Harbor().mainloop()
