# Creates a Harbor shortcut on the desktop. Run: right click -> "Run with PowerShell"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $here "harbor.pyw"

$pythonw = (Get-Command pythonw -ErrorAction SilentlyContinue).Source
if (-not $pythonw) {
    $python = (Get-Command python -ErrorAction Stop).Source
    $pythonw = Join-Path (Split-Path $python) "pythonw.exe"
}

$desktop = [Environment]::GetFolderPath("Desktop")
$lnk = Join-Path $desktop "Harbor.lnk"

$shell = New-Object -ComObject WScript.Shell
$sc = $shell.CreateShortcut($lnk)
$sc.TargetPath = $pythonw
$sc.Arguments = "`"$script`""
$sc.WorkingDirectory = $here
$icon = Join-Path $here "harbor.ico"
if (Test-Path $icon) { $sc.IconLocation = "$icon,0" } else { $sc.IconLocation = "$env:SystemRoot\System32\shell32.dll,4" }
$sc.Description = "Harbor - VS Code project launcher"
$sc.Save()

Write-Host "Shortcut created: $lnk"
