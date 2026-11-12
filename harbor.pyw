"""Harbor — лаунчер проектов для VS Code.

Слева — свои «папки» (группы), справа — проекты в выбранной папке.
Клик по проекту открывает его в VS Code. Всё хранится в harbor.json рядом со скриптом.
"""
import base64
import ctypes
import ctypes.wintypes
import json
import os
import queue
import re
import shutil
import string
import subprocess
import tempfile
import threading
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


# ---------- Lagoon: отдельная учётная запись Windows для Claude ----------
# Права на папки ставит сама Windows (icacls): что закрыто для учётки Lagoon, то её программы
# не могут ни прочитать, ни изменить. Твоя учётная запись при этом не затрагивается.

LAGOON_DEFAULT_ACCOUNT = "lagoon"
ADMINS_SID = "S-1-5-32-544"

# что делает icacls для каждого изменения; {sid} — учётка Lagoon
LAGOON_OPS = {
    "close": ["/deny", "*{sid}:(OI)(CI)(F)"],    # запретить всё, включая вложенные папки
    "unclose": ["/remove:d", "*{sid}"],          # снять запрет
    "open": ["/grant", "*{sid}:(OI)(CI)(M)"],    # разрешить читать, менять, создавать, удалять
    "unopen": ["/remove:g", "*{sid}"],           # снять разрешение
}
LAGOON_OP_ORDER = ["unopen", "unclose", "close", "open"]

# системные библиотеки Windows, через которые Harbor работает с правами, паролями и запуском
_advapi = ctypes.WinDLL("advapi32", use_last_error=True)
_authz = ctypes.WinDLL("authz", use_last_error=True)
_kernel = ctypes.WinDLL("kernel32", use_last_error=True)
_shell32 = ctypes.WinDLL("shell32", use_last_error=True)
_kernel.LocalFree.argtypes = [ctypes.c_void_p]
_kernel.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
_kernel.WaitForSingleObject.argtypes = [ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD]
_kernel.GetExitCodeProcess.argtypes = [ctypes.wintypes.HANDLE, ctypes.POINTER(ctypes.wintypes.DWORD)]


def _powershell_json(script, timeout=120):
    """Выполняет PowerShell-скрипт и разбирает его JSON-вывод (UTF-8)."""
    full = "[Console]::OutputEncoding = [Text.Encoding]::UTF8\n" + script
    encoded = base64.b64encode(full.encode("utf-16-le")).decode("ascii")
    r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                       capture_output=True, timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW)
    out = r.stdout.decode("utf-8", errors="replace").strip()
    if r.returncode != 0 or not out:
        raise OSError(r.stderr.decode("utf-8", errors="replace").strip() or "PowerShell завершился с ошибкой")
    return json.loads(out)


def _ps_json_literal(value):
    """JSON внутри PowerShell here-string: так пути с пробелами, кириллицей и кавычками доходят как есть."""
    return "@'\n" + json.dumps(value, ensure_ascii=False) + "\n'@"


def fixed_drive_roots():
    roots = []
    for letter in string.ascii_uppercase:
        root = f"{letter}:\\"
        if ctypes.windll.kernel32.GetDriveTypeW(root) == 3:  # DRIVE_FIXED
            roots.append(root)
    return roots


def lagoon_status(account, paths):
    """Учётная запись (есть ли, не администратор ли) и явные правила для неё на каждой папке."""
    script = f"""
$in = {_ps_json_literal({"account": account, "paths": paths})} | ConvertFrom-Json
$res = @{{ account = $null; paths = @() }}
$u = Get-LocalUser -Name $in.account -ErrorAction SilentlyContinue
if ($u) {{
    $sid = $u.SID.Value
    $admin = $false
    try {{ $admin = @(Get-LocalGroupMember -SID '{ADMINS_SID}' -ErrorAction Stop |
                    Where-Object {{ $_.SID.Value -eq $sid }}).Count -gt 0 }} catch {{ }}
    $res.account = @{{ sid = $sid; enabled = $u.Enabled; admin = $admin }}
    foreach ($p in $in.paths) {{
        $item = @{{ path = $p; exists = (Test-Path -LiteralPath $p); deny = $false; allow = $false }}
        if ($item.exists) {{
            try {{
                $acl = Get-Acl -LiteralPath $p
                foreach ($r in $acl.GetAccessRules($true, $false, [Security.Principal.SecurityIdentifier])) {{
                    if ($r.IdentityReference.Value -eq $sid) {{
                        if ($r.AccessControlType -eq 'Deny') {{ $item.deny = $true }} else {{ $item.allow = $true }}
                    }}
                }}
            }} catch {{ $item.error = $_.Exception.Message }}
        }}
        $res.paths += $item
    }}
}}
ConvertTo-Json -InputObject $res -Depth 4 -Compress
"""
    return _powershell_json(script)


def lagoon_apply_script(sid, ops, result_file):
    """Скрипт, который запускается от администратора и меняет права через icacls."""
    plan = [{"path": p, "action": a, "args": [x.format(sid=sid) for x in LAGOON_OPS[a]]} for a, p in ops]
    return f"""
$in = {_ps_json_literal({"ops": plan, "result": str(result_file)})} | ConvertFrom-Json
$results = @()
foreach ($op in $in.ops) {{
    $out = & icacls.exe $op.path @($op.args) /c 2>&1 | Out-String
    $results += @{{ path = $op.path; action = $op.action; code = $LASTEXITCODE; output = $out }}
}}
ConvertTo-Json -InputObject $results -Depth 3 | Out-File -LiteralPath $in.result -Encoding utf8
"""


class _ShellExecuteInfo(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.wintypes.DWORD), ("fMask", ctypes.c_ulong),
                ("hwnd", ctypes.wintypes.HWND), ("lpVerb", ctypes.wintypes.LPCWSTR),
                ("lpFile", ctypes.wintypes.LPCWSTR), ("lpParameters", ctypes.wintypes.LPCWSTR),
                ("lpDirectory", ctypes.wintypes.LPCWSTR), ("nShow", ctypes.c_int),
                ("hInstApp", ctypes.wintypes.HINSTANCE), ("lpIDList", ctypes.c_void_p),
                ("lpClass", ctypes.wintypes.LPCWSTR), ("hkeyClass", ctypes.wintypes.HKEY),
                ("dwHotKey", ctypes.wintypes.DWORD), ("hIconOrMonitor", ctypes.wintypes.HANDLE),
                ("hProcess", ctypes.wintypes.HANDLE)]


def run_and_wait(file, params, elevate=True):
    """Запускает программу (с окном Windows «разрешить изменения», если elevate) и ждёт её конца."""
    info = _ShellExecuteInfo(cbSize=ctypes.sizeof(_ShellExecuteInfo), fMask=0x40 | 0x100,  # NOCLOSEPROCESS|NOASYNC
                             lpVerb="runas" if elevate else "open", lpFile=file, lpParameters=params, nShow=0)
    if not _shell32.ShellExecuteExW(ctypes.byref(info)):
        err = ctypes.get_last_error()
        if err == 1223:  # ERROR_CANCELLED: нажали «Нет» в окне Windows
            raise PermissionError("отменено")
        raise ctypes.WinError(err)
    try:
        _kernel.WaitForSingleObject(info.hProcess, 0xFFFFFFFF)
        code = ctypes.wintypes.DWORD()
        _kernel.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        return code.value
    finally:
        _kernel.CloseHandle(info.hProcess)


def lagoon_apply(sid, ops, elevate=True):
    """Применяет изменения одним запуском от администратора. Возвращает результаты icacls по каждой папке."""
    work = Path(tempfile.mkdtemp(prefix="harbor-lagoon-"))
    script, result = work / "apply.ps1", work / "result.json"
    script.write_text(lagoon_apply_script(sid, ops, result), encoding="utf-8-sig")
    try:
        run_and_wait("powershell.exe", f'-NoProfile -ExecutionPolicy Bypass -File "{script}"', elevate)
        if not result.exists():
            raise OSError("скрипт не оставил результата")
        data = json.loads(result.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, list) else [data]
    finally:
        shutil.rmtree(work, ignore_errors=True)


def plural(n, forms):
    """plural(3, ("папка", "папки", "папок")) -> "3 папки"."""
    if n % 10 == 1 and n % 100 != 11:
        form = forms[0]
    elif 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        form = forms[1]
    else:
        form = forms[2]
    return f"{n} {form}"


def lagoon_plan(cfg, state):
    """Что нужно сделать, чтобы реальные права совпали с тем, что в списках.
    cfg — списки из harbor.json, state — {normcase(path): {"deny": bool, "allow": bool}}."""
    def actual(p, key):
        return state.get(os.path.normcase(p), {}).get(key, False)

    ops = [("unopen", p) for p in cfg["removing_open"] if actual(p, "allow")]
    ops += [("unclose", p) for p in cfg["removing_closed"] if actual(p, "deny")]
    ops += [("close", p) for p in cfg["closed"] if not actual(p, "deny")]
    ops += [("open", p) for p in cfg["open"] if not actual(p, "allow")]
    return sorted(ops, key=lambda op: LAGOON_OP_ORDER.index(op[0]))


# --- запуск от имени Lagoon ---


class _Credential(ctypes.Structure):
    _fields_ = [("Flags", ctypes.wintypes.DWORD), ("Type", ctypes.wintypes.DWORD),
                ("TargetName", ctypes.wintypes.LPWSTR), ("Comment", ctypes.wintypes.LPWSTR),
                ("LastWritten", ctypes.wintypes.FILETIME), ("CredentialBlobSize", ctypes.wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)), ("Persist", ctypes.wintypes.DWORD),
                ("AttributeCount", ctypes.wintypes.DWORD), ("Attributes", ctypes.c_void_p),
                ("TargetAlias", ctypes.wintypes.LPWSTR), ("UserName", ctypes.wintypes.LPWSTR)]


def _cred_target(account):
    return f"Harbor/Lagoon/{account}"


def save_lagoon_password(account, password):
    """Пароль хранится в Диспетчере учётных данных Windows: зашифрован и доступен только твоей учётке."""
    blob = password.encode("utf-16-le")
    buf = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
    cred = _Credential(Type=1, TargetName=_cred_target(account), CredentialBlobSize=len(blob),  # CRED_TYPE_GENERIC
                       CredentialBlob=ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)),
                       Persist=2, UserName=account)  # CRED_PERSIST_LOCAL_MACHINE
    if not _advapi.CredWriteW(ctypes.byref(cred), 0):
        raise ctypes.WinError(ctypes.get_last_error())


def load_lagoon_password(account):
    pcred = ctypes.POINTER(_Credential)()
    if not _advapi.CredReadW(_cred_target(account), 1, 0, ctypes.byref(pcred)):
        return None
    try:
        c = pcred.contents
        return ctypes.string_at(c.CredentialBlob, c.CredentialBlobSize).decode("utf-16-le")
    finally:
        _advapi.CredFree(pcred)


def delete_lagoon_password(account):
    _advapi.CredDeleteW(_cred_target(account), 1, 0)


LOGON_ERRORS = {
    1326: "Windows не приняла этот пароль. Попробуй ещё раз.",
    1909: "Учётная запись заблокирована после нескольких неверных паролей. Подожди несколько минут.",
    1907: "Windows требует сменить пароль этой учётной записи. Зайди в неё один раз и смени его.",
    1330: "Срок действия пароля этой учётной записи истёк. Зайди в неё один раз и смени его.",
    1331: "Эта учётная запись отключена.",
}


def check_lagoon_password(account, password):
    """Проверяет пароль у Windows, ничего не запуская. None — подошёл, иначе код ошибки Windows."""
    token = ctypes.wintypes.HANDLE()
    if not _advapi.LogonUserW(account, ".", password, 2, 0, ctypes.byref(token)):  # LOGON32_LOGON_INTERACTIVE
        return ctypes.get_last_error()
    _kernel.CloseHandle(token)
    return None


def lookup_sid(account):
    """SID учётки по имени, или None, если её нет."""
    sid_buf, sid_size = ctypes.create_string_buffer(256), ctypes.wintypes.DWORD(256)
    dom_buf, dom_size = ctypes.create_unicode_buffer(256), ctypes.wintypes.DWORD(256)
    use = ctypes.c_int()
    if not _advapi.LookupAccountNameW(None, account, sid_buf, ctypes.byref(sid_size),
                                      dom_buf, ctypes.byref(dom_size), ctypes.byref(use)):
        return None
    text = ctypes.c_wchar_p()
    if not _advapi.ConvertSidToStringSidW(sid_buf, ctypes.byref(text)):
        return None
    try:
        return text.value
    finally:
        _kernel.LocalFree(text)


class _AuthzRequest(ctypes.Structure):
    _fields_ = [("DesiredAccess", ctypes.wintypes.DWORD), ("PrincipalSelfSid", ctypes.c_void_p),
                ("ObjectTypeList", ctypes.c_void_p), ("ObjectTypeListLength", ctypes.wintypes.DWORD),
                ("OptionalArguments", ctypes.c_void_p)]


class _AuthzReply(ctypes.Structure):
    _fields_ = [("ResultListLength", ctypes.wintypes.DWORD),
                ("GrantedAccessMask", ctypes.POINTER(ctypes.wintypes.DWORD)),
                ("SaclEvaluationResults", ctypes.c_void_p), ("Error", ctypes.POINTER(ctypes.wintypes.DWORD))]


class _Luid(ctypes.Structure):
    _fields_ = [("LowPart", ctypes.wintypes.DWORD), ("HighPart", ctypes.wintypes.LONG)]


def lagoon_can_work_in(path, sid):
    """Может ли учётка с этим SID читать и создавать файлы в папке — спрашиваем у самой Windows."""
    psid, sd = ctypes.c_void_p(), ctypes.c_void_p()
    owner, group, dacl = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    rm, ctx = ctypes.c_void_p(), ctypes.c_void_p()
    try:
        if not _advapi.ConvertStringSidToSidW(sid, ctypes.byref(psid)):
            return False
        if _advapi.GetNamedSecurityInfoW(str(path), 1, 1 | 2 | 4, ctypes.byref(owner), ctypes.byref(group),
                                         ctypes.byref(dacl), None, ctypes.byref(sd)) != 0:
            return False
        if not _authz.AuthzInitializeResourceManager(1, None, None, None, None, ctypes.byref(rm)):
            return False
        if not _authz.AuthzInitializeContextFromSid(0, psid, rm, None, _Luid(0, 0), None, ctypes.byref(ctx)):
            return False
        granted, error = ctypes.wintypes.DWORD(), ctypes.wintypes.DWORD()
        request = _AuthzRequest(0x02000000, None, None, 0, None)  # MAXIMUM_ALLOWED
        reply = _AuthzReply(1, ctypes.pointer(granted), None, ctypes.pointer(error))
        if not _authz.AuthzAccessCheck(0, ctx, ctypes.byref(request), None, sd, None, 0, ctypes.byref(reply), None):
            return False
        return granted.value & 0x3 == 0x3  # FILE_LIST_DIRECTORY | FILE_ADD_FILE
    finally:
        if ctx:
            _authz.AuthzFreeContext(ctx)
        if rm:
            _authz.AuthzFreeResourceManager(rm)
        for p in (psid, sd):
            if p:
                _kernel.LocalFree(p)


def lagoon_vscode(sid):
    """Code.exe, который запустится у Lagoon: общий для всех или из её профиля."""
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if base and Path(base, "Microsoft VS Code", "Code.exe").is_file():
            return str(Path(base, "Microsoft VS Code", "Code.exe"))
    import winreg
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                        rf"SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\{sid}") as key:
        profile = os.path.expandvars(winreg.QueryValueEx(key, "ProfileImagePath")[0])
    # в профиль Lagoon твоя учётка заглянуть не может, поэтому путь не проверяем — его найдёт сама Lagoon
    return os.path.join(profile, "AppData", "Local", "Programs", "Microsoft VS Code", "Code.exe")


class _StartupInfo(ctypes.Structure):
    _fields_ = [("cb", ctypes.wintypes.DWORD), ("lpReserved", ctypes.wintypes.LPWSTR),
                ("lpDesktop", ctypes.wintypes.LPWSTR), ("lpTitle", ctypes.wintypes.LPWSTR),
                ("dwX", ctypes.wintypes.DWORD), ("dwY", ctypes.wintypes.DWORD),
                ("dwXSize", ctypes.wintypes.DWORD), ("dwYSize", ctypes.wintypes.DWORD),
                ("dwXCountChars", ctypes.wintypes.DWORD), ("dwYCountChars", ctypes.wintypes.DWORD),
                ("dwFillAttribute", ctypes.wintypes.DWORD), ("dwFlags", ctypes.wintypes.DWORD),
                ("wShowWindow", ctypes.wintypes.WORD), ("cbReserved2", ctypes.wintypes.WORD),
                ("lpReserved2", ctypes.c_void_p), ("hStdInput", ctypes.wintypes.HANDLE),
                ("hStdOutput", ctypes.wintypes.HANDLE), ("hStdError", ctypes.wintypes.HANDLE)]


class _ProcessInfo(ctypes.Structure):
    _fields_ = [("hProcess", ctypes.wintypes.HANDLE), ("hThread", ctypes.wintypes.HANDLE),
                ("dwProcessId", ctypes.wintypes.DWORD), ("dwThreadId", ctypes.wintypes.DWORD)]


class LagoonLaunchError(OSError):
    pass


def open_in_lagoon(account, password, sid, path):
    """Открывает папку в VS Code от имени Lagoon, на твоём рабочем столе."""
    exe = lagoon_vscode(sid)
    command = ctypes.create_unicode_buffer(subprocess.list2cmdline([exe, path]))
    si, pi = _StartupInfo(cb=ctypes.sizeof(_StartupInfo)), _ProcessInfo()
    # LOGON_WITH_PROFILE: окружение и настройки берутся из профиля Lagoon, а не из твоего
    ok = _advapi.CreateProcessWithLogonW(account, ".", password, 1, None, command,
                                         0x400, None, path, ctypes.byref(si), ctypes.byref(pi))
    if not ok:
        err = ctypes.get_last_error()
        reasons = {
            1326: "неверный пароль",
            2: f"у {account} не найден VS Code ({exe}). Зайди в учётку {account} и установи его.",
            3: f"у {account} не найден VS Code ({exe}). Зайди в учётку {account} и установи его.",
            5: f"у {account} нет доступа к папке проекта",
            267: f"у {account} нет доступа к папке проекта",
            1058: "выключена служба Windows «Вторичный вход в систему» (seclogon)",
        }
        raise LagoonLaunchError(err, reasons.get(err, ctypes.FormatError(err)))
    _kernel.CloseHandle(pi.hProcess)
    _kernel.CloseHandle(pi.hThread)


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

    def __init__(self, parent, title, message, initial=None, buttons=(("OK", True, "primary"),), secret=False):
        super().__init__(parent)
        self.withdraw()
        self.secret = secret
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
                             font=("Segoe UI", 11), width=38, highlightthickness=1, show="•" if secret else "",
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
            text = self.var.get()
            value = (text if self.secret else text.strip()) or None  # пароль — как есть, с пробелами
        self.result = value
        self.grab_release()
        self.destroy()


def ask_string(parent, title, prompt, initial="", secret=False):
    return Dialog(parent, title, prompt, initial=initial, secret=secret,
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
        self.view = "projects"      # или "lagoon"
        self.lagoon_state = None    # реальные права папок, читаются при открытии экрана Lagoon
        self.lagoon_busy = False    # False, "reading" или "applying"
        self._sid_cache = {}
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
        view = self.view
        self.build_ui(select=self.current_group_index() or self.data.get("last_group", 0) or 0)
        if view == "lagoon":
            self.show_lagoon()

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

        lagoon_btn = make_button(side, "≈  Lagoon", self.show_lagoon, "ghost")
        lagoon_btn.configure(fg=CYAN, anchor="w")
        lagoon_btn.pack(fill="x", padx=10, pady=(8, 0))
        Tooltip(lagoon_btn, "Пространство для Claude: какие папки ему закрыты и открыты")

        bottom = tk.Frame(side, bg=SIDEBAR_BG)
        bottom.pack(fill="x", padx=10, pady=(4, 12))
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
        self.view = "projects"
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

        # где откроется проект: в Lagoon (Claude без вопросов, только свои папки) или от твоего имени
        badge = None
        if exists and self.lagoon_sid():
            in_lagoon = self.opens_in_lagoon(project)
            badge = tk.Label(row, text="≈ Lagoon" if in_lagoon else "от моего имени", bg=CARD_BG,
                             fg=CYAN if in_lagoon else MUTED, font=FONT_SMALL, padx=14)
            badge.pack(side="right")
            Tooltip(badge, "Откроется в VS Code учётки Lagoon: Claude работает без вопросов и видит только "
                           "открытые для неё папки" if in_lagoon else
                    "Откроется в твоём VS Code: Claude спрашивает разрешения как обычно")

        body = tk.Frame(row, bg=CARD_BG)
        body.pack(side="left", fill="both", expand=True)
        name = tk.Label(body, text=project["name"], bg=CARD_BG, fg=TEXT if exists else MUTED,
                        font=FONT_BOLD, anchor="w")
        name.pack(fill="x", padx=14, pady=(9, 0))
        path_text = project["path"] if exists else f"{project['path']}   — папка не найдена"
        path = tk.Label(body, text=path_text, bg=CARD_BG, fg=MUTED if exists else ERROR,
                        font=FONT_SMALL, anchor="w")
        path.pack(fill="x", padx=14, pady=(0, 9))

        widgets = tuple(w for w in (row, strip, body, name, path, badge) if w)
        card_widgets = tuple(w for w in (body, name, path, badge) if w)

        def hover(on):
            for w in card_widgets:
                w.configure(bg=CARD_HOVER if on else CARD_BG)
            row.configure(highlightbackground=(CYAN if on else BORDER) if exists else BORDER)
            strip.configure(bg=(CYAN if on else VIOLET) if exists else ERROR)
            name.configure(fg=(NAME_HOVER if on else TEXT) if exists else MUTED)

        for w in widgets:
            # add="+": не затирать подсказку, которая висит на значке «≈ Lagoon»
            w.bind("<Enter>", lambda e: hover(True), add="+")
            w.bind("<Leave>", lambda e: hover(False), add="+")
            w.bind("<Button-1>", lambda e: self.open_project(project), add="+")
            w.bind("<Button-3>", lambda e: self._show_project_menu(e, index))

    def _show_project_menu(self, event, index):
        g = self.current_group()
        project = g["projects"][index]
        m = self.project_menu
        m.delete(0, "end")
        m.add_command(label="Открыть в VS Code", command=lambda: self.open_project(project))
        if self.lagoon_sid() and os.path.isdir(project["path"]):
            if self.lagoon_can_reach(project):
                in_lagoon = project.get("lagoon", True)
                m.add_command(label="Открывать от моего имени" if in_lagoon else "Открывать в Lagoon",
                              command=lambda: self.set_project_lagoon(project, not in_lagoon))
            else:
                m.add_command(label="Lagoon: эта папка для неё закрыта", state="disabled")
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
        if self.opens_in_lagoon(project):
            self.open_project_in_lagoon(project)
            return
        try:
            open_in_vscode(project["path"])
            self.set_status(f"Открываю {project['name']} в VS Code…")
        except OSError as e:
            show_message(self, "Harbor", str(e))

    # --- запуск в Lagoon ---

    def lagoon_sid(self):
        """SID учётки Lagoon или None, если её нет. Запоминается, чтобы не спрашивать Windows на каждой карточке."""
        account = self.lagoon_cfg()["account"]
        if self._sid_cache.get("account") != account:
            self._sid_cache = {"account": account, "sid": lookup_sid(account)}
        return self._sid_cache["sid"]

    def lagoon_can_reach(self, project):
        sid = self.lagoon_sid()
        return bool(sid) and lagoon_can_work_in(project["path"], sid)

    def opens_in_lagoon(self, project):
        """По умолчанию — в Lagoon, если ей открыта папка проекта; можно переключить на «от моего имени»."""
        return project.get("lagoon", True) and os.path.isdir(project["path"]) and self.lagoon_can_reach(project)

    def set_project_lagoon(self, project, on):
        if on:
            project.pop("lagoon", None)
        else:
            project["lagoon"] = False
        self.save()
        self.show_projects()

    def ask_lagoon_password(self, reason=""):
        account = self.lagoon_cfg()["account"]
        while True:
            password = ask_string(self, "Пароль Lagoon", (reason + "\n\n" if reason else "") +
                                  f"Пароль учётной записи «{account}». Harbor сохранит его в Диспетчере учётных "
                                  "данных Windows — зашифрованным, доступным только твоей учётной записи.",
                                  secret=True)
            if not password:
                return None
            error = check_lagoon_password(account, password)
            if error is None:
                save_lagoon_password(account, password)
                return password
            if error != 1326:  # не «неверный пароль» — повторный ввод не поможет
                show_message(self, "Lagoon", LOGON_ERRORS.get(error, ctypes.FormatError(error)))
                return None
            reason = LOGON_ERRORS[1326]

    def open_project_in_lagoon(self, project, retry=True):
        account, sid = self.lagoon_cfg()["account"], self.lagoon_sid()
        password = load_lagoon_password(account) or self.ask_lagoon_password()
        if not password:
            return
        self.set_status(f"Открываю {project['name']} в Lagoon…")

        def work():
            try:
                open_in_lagoon(account, password, sid, project["path"])
                return None
            except (OSError, LagoonLaunchError) as e:
                return e

        def done(error):
            if error is None:
                self.set_status(f"{project['name']} открыт в Lagoon")
            elif isinstance(error, LagoonLaunchError) and error.errno == 1326 and retry:
                delete_lagoon_password(account)  # пароль сменили — спросим новый
                if self.ask_lagoon_password("Сохранённый пароль больше не подходит."):
                    self.open_project_in_lagoon(project, retry=False)
            else:
                text = error.strerror if isinstance(error, LagoonLaunchError) else str(error)
                show_message(self, "Lagoon", f"Не удалось открыть «{project['name']}» в Lagoon:\n{text}")

        self.run_in_background(work, done)

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

    # --- Lagoon: пространство для Claude ---

    def run_in_background(self, work, done):
        """work() выполняется в отдельном потоке, done(результат) — потом в окне.
        Окно tkinter трогаем только из главного потока: результат забираем через очередь."""
        results = queue.Queue()
        threading.Thread(target=lambda: results.put(work()), daemon=True).start()

        def poll():
            try:
                result = results.get_nowait()
            except queue.Empty:
                self.after(100, poll)
                return
            done(result)

        self.after(100, poll)

    def lagoon_cfg(self):
        cfg = self.data.get("lagoon")
        if not isinstance(cfg, dict):
            cfg = self.data["lagoon"] = {}
        cfg.setdefault("account", LAGOON_DEFAULT_ACCOUNT)
        for key in ("closed", "open", "removing_closed", "removing_open"):
            if not isinstance(cfg.get(key), list):
                cfg[key] = []
        return cfg

    def _lagoon_actual(self, path, key):
        state = self.lagoon_state or {}
        return state.get("paths", {}).get(os.path.normcase(path), {}).get(key, False)

    def show_lagoon(self):
        self.view = "lagoon"
        self.groups_box.selection_clear(0, "end")
        self.add_btn.pack_forget()
        self.title_label.config(text="Lagoon")
        self.render_lagoon()
        if self.lagoon_state is None:
            self.refresh_lagoon()

    def refresh_lagoon(self):
        """Читает в фоне реальные права папок и состояние учётки, потом перерисовывает экран."""
        if self.lagoon_busy:
            return
        self.lagoon_busy = "reading"
        cfg = self.lagoon_cfg()
        seen, paths = set(), []
        for p in cfg["closed"] + cfg["open"] + cfg["removing_closed"] + cfg["removing_open"] + fixed_drive_roots():
            if os.path.normcase(p) not in seen:
                seen.add(os.path.normcase(p))
                paths.append(p)

        def work():
            try:
                return lagoon_status(cfg["account"], paths)
            except (OSError, ValueError, subprocess.TimeoutExpired) as e:
                return {"error": str(e)}

        def done(result):
            self.lagoon_busy = False
            if "error" in result:
                self.lagoon_state = {"error": result["error"], "paths": {}}
            else:
                self.lagoon_state = {"account": result.get("account"),
                                     "paths": {os.path.normcase(p["path"]): p for p in result.get("paths", [])}}
                self._lagoon_adopt()
            if self.view == "lagoon":
                self.render_lagoon()

        if self.view == "lagoon":
            self.render_lagoon()
        self.run_in_background(work, done)

    def _lagoon_adopt(self):
        """Списки следуют за реальностью: правила, поставленные вручную, появляются в списках,
        а снятые — пропадают из «будет снято»."""
        cfg = self.lagoon_cfg()
        for key, kind in (("deny", "closed"), ("allow", "open")):
            removing = cfg["removing_" + kind]
            cfg["removing_" + kind] = [p for p in removing if self._lagoon_actual(p, key)]
            known = {os.path.normcase(p) for p in cfg[kind] + cfg["removing_" + kind]}
            for info in self.lagoon_state["paths"].values():
                if info.get(key) and os.path.normcase(info["path"]) not in known:
                    cfg[kind].append(info["path"])
        self.save()

    def _lagoon_link(self, master, text, command, fg=None):
        lbl = tk.Label(master, text=text, bg=master["bg"], fg=fg or CYAN, font=FONT_SMALL, cursor="hand2")
        lbl.bind("<Button-1>", lambda e: command())
        return lbl

    def render_lagoon(self):
        for w in self.list_frame.winfo_children():
            w.destroy()
        cfg = self.lagoon_cfg()
        state = self.lagoon_state
        f = self.list_frame
        wrap = int(600 * self.k)

        def caps(text, master=f, pady=(16, 4)):
            tk.Label(master, text=text, bg=BG, fg=MUTED, font=FONT_CAPS, anchor="w").pack(fill="x", pady=pady)

        def note(text, fg=MUTED, master=f):
            lbl = tk.Label(master, text=text, bg=BG, fg=fg, font=FONT_SMALL, anchor="w", justify="left",
                           wraplength=wrap)
            lbl.pack(fill="x")
            return lbl

        note("Lagoon — отдельная учётная запись Windows для Claude. Что закрыто здесь, программы этой "
             "учётки не могут ни прочитать, ни изменить — это запрещает сама Windows. Твоя учётная запись "
             "и твои программы не затрагиваются. Всё, чего нет в списках, остаётся как есть.")

        # учётная запись
        caps("УЧЁТНАЯ ЗАПИСЬ")
        acc_row = tk.Frame(f, bg=BG)
        acc_row.pack(fill="x")
        account = state.get("account") if state else None
        name = cfg["account"]
        if state is None or self.lagoon_busy == "reading":
            tk.Label(acc_row, text=f"Проверяю «{name}»…", bg=BG, fg=MUTED, font=FONT).pack(side="left")
        elif state.get("error"):
            tk.Label(acc_row, text="Не удалось прочитать права", bg=BG, fg=ERROR, font=FONT).pack(side="left")
            self._lagoon_link(acc_row, "повторить", self.refresh_lagoon).pack(side="left", padx=(10, 0))
            note(state["error"], ERROR)
        elif account is None:
            tk.Label(acc_row, text=f"Учётная запись «{name}» не найдена", bg=BG, fg=ERROR,
                     font=FONT).pack(side="left")
            self._lagoon_link(acc_row, "проверить снова", self.refresh_lagoon).pack(side="left", padx=(10, 0))
            note("Создай её: Параметры → Учётные записи → Другие пользователи → «Добавить учётную запись» → "
                 "«У меня нет данных для входа этого человека» → «Добавить пользователя без учётной записи "
                 "Майкрософт». Имя латиницей, пароль обязателен, тип — «Стандартный».")
        elif account.get("admin"):
            tk.Label(acc_row, text=f"⚠ «{name}» — администратор", bg=BG, fg=ERROR, font=FONT).pack(side="left")
            self._lagoon_link(acc_row, "проверить снова", self.refresh_lagoon).pack(side="left", padx=(10, 0))
            note("Так защита не работает: администратор может снять любые запреты. Сделай её обычной: "
                 "Параметры → Учётные записи → Другие пользователи → «Изменить тип учётной записи» → «Стандартный».",
                 ERROR)
        else:
            tk.Label(acc_row, text=f"✓ «{name}» — обычный пользователь", bg=BG, fg=CYAN,
                     font=FONT).pack(side="left")
        self._lagoon_link(acc_row, "другое имя", self.rename_lagoon_account).pack(side="left", padx=(10, 0))

        if account and not account.get("admin"):
            pw_row = tk.Frame(f, bg=BG)
            pw_row.pack(fill="x", pady=(4, 0))
            if load_lagoon_password(name):
                tk.Label(pw_row, text="✓ пароль сохранён — проекты открываются в Lagoon одним кликом",
                         bg=BG, fg=MUTED, font=FONT_SMALL).pack(side="left")
                self._lagoon_link(pw_row, "изменить", self.change_lagoon_password).pack(side="left", padx=(10, 0))
                self._lagoon_link(pw_row, "удалить", self.forget_lagoon_password, ERROR).pack(side="left", padx=(10, 0))
            else:
                tk.Label(pw_row, text="Пароль не сохранён — Harbor спросит его при первом открытии проекта",
                         bg=BG, fg=MUTED, font=FONT_SMALL).pack(side="left")
                self._lagoon_link(pw_row, "задать сейчас", self.change_lagoon_password).pack(side="left", padx=(10, 0))

        # списки
        for kind, title, add_text in (("closed", "ЗАКРЫТО ДЛЯ CLAUDE", "+ закрыть папку или диск"),
                                      ("open", "ОТКРЫТО ДЛЯ CLAUDE", "+ открыть папку")):
            head = tk.Frame(f, bg=BG)
            head.pack(fill="x", pady=(16, 4))
            tk.Label(head, text=title, bg=BG, fg=MUTED, font=FONT_CAPS).pack(side="left")
            self._lagoon_link(head, add_text, lambda k=kind: self.lagoon_add(k)).pack(side="left", padx=(12, 0))
            items = cfg[kind] + cfg["removing_" + kind]
            if not items:
                note("пусто")
            for p in items:
                self._lagoon_row(p, kind, removing=p in cfg["removing_" + kind])
        note("Открытое внутри закрытого работает как исключение: например, одна папка внутри закрытого диска.")

        # применение
        plan = lagoon_plan(cfg, state.get("paths", {})) if state and not state.get("error") else []
        foot = tk.Frame(f, bg=BG)
        foot.pack(fill="x", pady=(20, 8), padx=(0, 12))
        if self.lagoon_busy == "applying":
            text, fg = "Применяю… Windows проставляет права на все файлы внутри, это может занять несколько минут.", VIOLET
        elif state is None or self.lagoon_busy == "reading":
            text, fg = "", MUTED
        elif plan:
            text = "Будет изменено: " + plural(len(plan), ("папка", "папки", "папок")) + \
                   ". Windows спросит разрешение администратора."
            fg = VIOLET
        else:
            text, fg = "Всё применено, права папок совпадают со списками.", MUTED
        can_apply = bool(plan) and not self.lagoon_busy and account and not account.get("admin")
        btn = make_button(foot, "Применить", self.apply_lagoon, "primary" if can_apply else "subtle")
        if not can_apply:
            btn.configure(state="disabled", cursor="arrow")
        btn.pack(side="right")
        tk.Label(foot, text=text, bg=BG, fg=fg, font=FONT_SMALL, anchor="w", justify="left",
                 wraplength=int(440 * self.k)).pack(side="left", fill="x", expand=True)

    def _lagoon_row(self, path, kind, removing):
        state = self.lagoon_state
        info = (state or {}).get("paths", {}).get(os.path.normcase(path))
        applied = self._lagoon_actual(path, "deny" if kind == "closed" else "allow")
        row = tk.Frame(self.list_frame, bg=CARD_BG, highlightthickness=1, highlightbackground=BORDER)
        row.pack(fill="x", pady=2, padx=(0, 12))
        strip = tk.Frame(row, bg=(ERROR if kind == "closed" else CYAN) if not removing else BORDER, width=3)
        strip.pack(side="left", fill="y")
        actions = tk.Frame(row, bg=CARD_BG)
        actions.pack(side="right", padx=12)  # раньше пути: длинный путь обрежется, а кнопки останутся
        label = path + ("   (весь диск)" if re.fullmatch(r"[A-Za-z]:\\", path) else "")
        path_label = tk.Label(row, text=label, bg=CARD_BG, fg=MUTED if removing else TEXT,
                              font=("Segoe UI", 10, "overstrike") if removing else FONT, anchor="w")
        path_label.pack(side="left", fill="x", expand=True, padx=(12, 0), pady=6)
        Tooltip(path_label, path)

        if state is None or self.lagoon_busy == "reading":
            status, fg = "проверяю…", MUTED
        elif info and not info.get("exists", True):
            status, fg = "папка не найдена", ERROR
        elif removing:
            status, fg = "будет снято", PINK
        elif applied:
            status, fg = "✓ действует", CYAN
        else:
            status, fg = "будет применено", VIOLET
        tk.Label(actions, text=status, bg=CARD_BG, fg=fg, font=FONT_SMALL).pack(side="left")
        if removing:
            self._lagoon_link(actions, "вернуть", lambda: self.lagoon_restore(kind, path)).pack(side="left", padx=(10, 0))
        else:
            self._lagoon_link(actions, "убрать", lambda: self.lagoon_remove(kind, path)).pack(side="left", padx=(10, 0))

    def _lagoon_system_path(self, path):
        """Папки, без которых учётка Lagoon не сможет работать: их закрывать нельзя."""
        norm = os.path.normcase(os.path.normpath(path))
        windir = os.environ.get("SystemRoot", r"C:\Windows")
        users = str(Path.home().parent)
        # сами эти папки и всё внутри них
        whole = [windir, os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", ""),
                 os.environ.get("ProgramData", ""), os.path.join(users, self.lagoon_cfg()["account"])]
        # только сами папки: системный диск и папка всех пользователей
        exact = [os.path.splitdrive(windir)[0] + "\\", users]
        return (any(norm == os.path.normcase(os.path.normpath(c)) for c in exact) or
                any(c and (norm == os.path.normcase(c) or norm.startswith(os.path.normcase(c) + "\\"))
                    for c in whole))

    def lagoon_add(self, kind):
        title = "Что закрыть для Claude" if kind == "closed" else "Что открыть для Claude"
        folder = filedialog.askdirectory(parent=self, title=title, mustexist=True)
        if not folder:
            return
        folder = os.path.normpath(folder)
        if re.fullmatch(r"[A-Za-z]:", folder):
            folder += "\\"
        if kind == "closed" and self._lagoon_system_path(folder):
            show_message(self, "Lagoon", f"{folder} — системная папка. Если её закрыть, учётка Lagoon не сможет "
                                         "запускать программы и работать вообще.")
            return
        cfg = self.lagoon_cfg()
        other = "open" if kind == "closed" else "closed"
        same = lambda lst: [p for p in lst if os.path.normcase(p) != os.path.normcase(folder)]
        # папка не может быть одновременно закрытой и открытой
        if any(os.path.normcase(p) == os.path.normcase(folder) for p in cfg[other]):
            cfg[other] = same(cfg[other])
            if self._lagoon_actual(folder, "deny" if other == "closed" else "allow"):
                cfg["removing_" + other].append(folder)
        cfg["removing_" + kind] = same(cfg["removing_" + kind])
        if not any(os.path.normcase(p) == os.path.normcase(folder) for p in cfg[kind]):
            cfg[kind].append(folder)
        self.save()
        self.render_lagoon()

    def lagoon_remove(self, kind, path):
        cfg = self.lagoon_cfg()
        cfg[kind] = [p for p in cfg[kind] if p != path]
        if self._lagoon_actual(path, "deny" if kind == "closed" else "allow"):
            cfg["removing_" + kind].append(path)
        self.save()
        self.render_lagoon()

    def lagoon_restore(self, kind, path):
        cfg = self.lagoon_cfg()
        cfg["removing_" + kind] = [p for p in cfg["removing_" + kind] if p != path]
        cfg[kind].append(path)
        self.save()
        self.render_lagoon()

    def change_lagoon_password(self):
        if self.ask_lagoon_password():
            self.set_status("Пароль Lagoon сохранён")
        self.render_lagoon()

    def forget_lagoon_password(self):
        account = self.lagoon_cfg()["account"]
        if ask_yes_no(self, "Удалить пароль", f"Удалить сохранённый пароль «{account}» из Диспетчера учётных "
                                              "данных? Harbor спросит его при следующем открытии проекта."):
            delete_lagoon_password(account)
            self.render_lagoon()

    def rename_lagoon_account(self):
        cfg = self.lagoon_cfg()
        name = ask_string(self, "Учётная запись", "Имя учётной записи Windows для Claude:", cfg["account"])
        if name and name != cfg["account"]:
            cfg["account"] = name
            self.save()
            self.lagoon_state = None
            self.refresh_lagoon()

    def apply_lagoon(self):
        cfg = self.lagoon_cfg()
        state = self.lagoon_state
        if self.lagoon_busy or not state or not state.get("account"):
            return
        plan = lagoon_plan(cfg, state["paths"])
        if not plan:
            return
        words = {"close": "закрыть", "unclose": "снять запрет", "open": "открыть", "unopen": "снять доступ"}
        lines = "\n".join(f"• {words[a]}: {p}" for a, p in plan)
        slow = any(a in ("close", "unclose") and re.fullmatch(r"[A-Za-z]:\\", p) for a, p in plan)
        if not ask_yes_no(self, "Применить", f"Изменения для учётки «{cfg['account']}»:\n\n{lines}\n\n"
                                            + ("Права целого диска применяются несколько минут — Harbor можно "
                                               "не закрывать, просто подожди.\n\n" if slow else "")
                                            + "Windows попросит разрешение администратора. Продолжить?"):
            return
        sid = state["account"]["sid"]
        self.lagoon_busy = "applying"
        self.render_lagoon()

        def work():
            try:
                return lagoon_apply(sid, plan)
            except PermissionError:
                return "cancelled"
            except (OSError, ValueError) as e:
                return e

        def done(result):
            self.lagoon_busy = False
            if result == "cancelled":
                self.set_status("Отменено: разрешение администратора не дано")
            elif isinstance(result, Exception):
                show_message(self, "Lagoon", f"Не удалось применить изменения:\n{result}")
            else:
                failed = [r for r in result if r.get("code") != 0]
                if failed:
                    show_message(self, "Lagoon", "Не получилось для:\n" + "\n".join(
                        f"• {r['path']}: {(r.get('output') or '').strip()[-200:]}" for r in failed))
                else:
                    self.set_status("Права применены")
            self.refresh_lagoon()

        self.run_in_background(work, done)

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
