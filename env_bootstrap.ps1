# ============================================================
#  env_bootstrap.ps1 - Protein Data Processing System environment bootstrap
#  Called automatically by run.bat when the project virtualenv is missing.
#    1. Locate a system Python interpreter (>= 3.9) - PATH / registry / common paths
#    2. Create the project-local virtual environment (.venv)
#    3. Install dependencies from requirements.txt into .venv
#    4. If no Python is found, offer to download & silently install Python
#  Exit codes: 0 = environment ready; 1 = not ready / cancelled / failed
#  Usage: powershell -NoProfile -ExecutionPolicy Bypass -File env_bootstrap.ps1 [-Auto]
#    -Auto: unattended mode (no dialogs, download into the user's Downloads folder)
#  All messages are plain ASCII on purpose (safe on every Windows codepage).
# ============================================================

param([switch]$Auto)

$ErrorActionPreference = 'Continue'
$script:PY_VERSION = '3.11.9'
$script:PY_URL = "https://www.python.org/ftp/python/$($script:PY_VERSION)/python-$($script:PY_VERSION)-amd64.exe"
$script:PY_INSTALLER_NAME = "python-$($script:PY_VERSION)-amd64.exe"
# Fallback dependency list (kept in sync with requirements.txt); the real list
# is parsed from requirements.txt at runtime by Load-RequiredModules.
$script:REQUIRED_MODS = @('flask', 'pandas', 'openpyxl', 'requests', 'urllib3', 'bs4', 'yaml')
$script:PACKAGE_TO_MODULE = @{ 'beautifulsoup4' = 'bs4'; 'pyyaml' = 'yaml' }
# Reverse map: import name -> PyPI package name (used when topping up only the
# missing modules, so already-installed packages are never touched).
$script:MODULE_TO_PACKAGE = @{ 'bs4' = 'beautifulsoup4'; 'yaml' = 'PyYAML' }
# Original requirement lines from requirements.txt, keyed by lower-case package
# name (e.g. 'pandas' -> 'pandas>=2.0,<3.0'); filled in by Load-RequiredModules.
$script:REQ_LINES = @{}
$script:MIN_MAJOR = 3
$script:MIN_MINOR = 9
# Fallback interpreter search patterns (version-agnostic, no machine-specific paths)
$script:FALLBACK_PY_PATTERNS = @(
    (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python3*\python.exe'),
    (Join-Path $env:ProgramFiles 'Python3*\python.exe')
)

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

function Write-Log {
    param([string]$Msg)
    Write-Host "[env] $Msg"
}

# ---------- 1. 查找 Python ----------
function Find-Python {
    # Search a SYSTEM Python interpreter (>= 3.9). The project virtualenv is
    # handled separately by Get-VenvPython.
    $found = $null
    # a) interpreters on PATH (py launcher first: it picks the newest version)
    foreach ($name in @('py.exe', 'python3.exe', 'python.exe')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source -and $cmd.Source -notmatch 'WindowsApps') {
            return $cmd.Source
        }
    }
    # b) common per-user install locations (version-agnostic, no user names)
    $patterns = @(
        "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe",
        "$env:ProgramFiles\Python3*\python.exe",
        "C:\Python3*\python.exe"
    )
    foreach ($pat in $patterns) {
        $hit = Get-ChildItem -Path $pat -ErrorAction SilentlyContinue |
               Sort-Object FullName -Descending | Select-Object -First 1
        if ($hit) { return $hit.FullName }
    }
    # c) registry InstallPath entries
    foreach ($ver in @('3.13','3.12','3.11','3.10','3.9')) {
        foreach ($hive in @('HKCU:\SOFTWARE\Python\PythonCore','HKLM:\SOFTWARE\Python\PythonCore','HKLM:\SOFTWARE\WOW6432Node\Python\PythonCore')) {
            $key = "$hive\$ver\InstallPath"
            $val = (Get-ItemProperty -Path $key -ErrorAction SilentlyContinue).'(default)'
            if ($val) {
                $p = Join-Path $val 'python.exe'
                if (Test-Path $p) { return $p }
            }
        }
    }
    return $found
}

function Get-VenvPython {
    # Project-local virtualenv interpreter (Windows and POSIX layout).
    foreach ($rel in @('.venv\Scripts\python.exe', '.venv/bin/python')) {
        $p = Join-Path $PSScriptRoot $rel
        if (Test-Path $p) { return $p }
    }
    return $null
}

function Load-RequiredModules {
    # Parse requirements.txt so the bootstrap check never drifts from the repo.
    $req = Join-Path $PSScriptRoot 'requirements.txt'
    if (-not (Test-Path $req)) {
        Write-Log "requirements.txt not found; using built-in dependency list."
        return
    }
    $mods = @()
    $lines = @{}
    foreach ($line in @(Get-Content -Path $req -Encoding UTF8)) {
        $l = $line.Trim()
        if (-not $l -or $l.StartsWith('#')) { continue }
        $l = ($l -split ';')[0].Trim()
        $l = ($l -split '#')[0].Trim()
        if (-not $l) { continue }
        $pkgName = (($l -split '[<>=!~\[ ]+')[0]).Trim()
        if (-not $pkgName) { continue }
        # Keep the ORIGINAL line (with its version constraint) per package so a
        # single missing package can be installed without re-resolving the rest.
        $pkgLower = $pkgName.ToLower()
        if (-not $lines.ContainsKey($pkgLower)) { $lines[$pkgLower] = $l }
        $pkg = $pkgLower
        if ($script:PACKAGE_TO_MODULE.ContainsKey($pkg)) { $pkg = $script:PACKAGE_TO_MODULE[$pkg] }
        if ($mods -notcontains $pkg) { $mods += $pkg }
    }
    if ($mods.Count -gt 0) { $script:REQUIRED_MODS = $mods }
    if ($lines.Count -gt 0) { $script:REQ_LINES = $lines }
}

# ---------- 2. 读取 Python 版本 ----------
function Get-PythonVersion {
    param([string]$PyExe)
    try {
        $v = & $PyExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')" 2>$null
        return ($v | Select-Object -Last 1).Trim()
    } catch { return $null }
}

# ---------- 3. 检查依赖库 (against $script:REQUIRED_MODS) ----------
function Check-Modules {
    param([string]$PyExe)
    if (-not $PyExe) { return $script:REQUIRED_MODS }
    $list = ($script:REQUIRED_MODS | ForEach-Object { "'$_'" }) -join ','
    $code = "import importlib.util as u; mods=[$list]; print(','.join(m for m in mods if u.find_spec(m) is None))"
    try {
        $out = & $PyExe -c $code 2>$null
        $line = ($out | Select-Object -Last 1)
        if ($line) { $line = $line.Trim() }
        if ($line) { return @($line -split ',') }
        return @()
    } catch {
        return $script:REQUIRED_MODS
    }
}

# ---------- 4. 可视化清单(交互模式 WinForms) ----------
function Show-StatusForm {
    param(
        [string]$PythonInfo,
        [string[]]$MissingMods,
        [bool]$PythonOk
    )
    $form = New-Object System.Windows.Forms.Form
    $form.Text = "Environment Check - Protein Data Processing System"
    $form.Size = New-Object System.Drawing.Size(520, 460)
    $form.StartPosition = 'CenterScreen'
    $form.FormBorderStyle = 'FixedDialog'
    $form.MaximizeBox = $false
    $form.MinimizeBox = $false

    $lbl = New-Object System.Windows.Forms.Label
    $lbl.Text = "Environment check result:"
    $lbl.Location = New-Object System.Drawing.Point(15, 12)
    $lbl.Size = New-Object System.Drawing.Size(470, 22)
    $form.Controls.Add($lbl)

    $txt = New-Object System.Windows.Forms.TextBox
    $txt.Multiline = $true
    $txt.ReadOnly = $true
    $txt.ScrollBars = 'Vertical'
    $txt.Location = New-Object System.Drawing.Point(15, 40)
    $txt.Size = New-Object System.Drawing.Size(470, 250)
    $txt.Font = New-Object System.Drawing.Font('Consolas', 10)
    $txt.Text = $PythonInfo
    $form.Controls.Add($txt)

    $btnOk = New-Object System.Windows.Forms.Button
    $btnOk.Text = "Continue"
    $btnOk.Location = New-Object System.Drawing.Point(160, 310)
    $btnOk.Size = New-Object System.Drawing.Size(100, 34)
    $btnOk.DialogResult = 'OK'
    $form.Controls.Add($btnOk)

    $btnCancel = New-Object System.Windows.Forms.Button
    $btnCancel.Text = "Cancel"
    $btnCancel.Location = New-Object System.Drawing.Point(280, 310)
    $btnCancel.Size = New-Object System.Drawing.Size(100, 34)
    $btnCancel.DialogResult = 'Cancel'
    $form.Controls.Add($btnCancel)

    $tip = New-Object System.Windows.Forms.Label
    if ($PythonOk -and $MissingMods.Count -eq 0) {
        $tip.Text = "All requirements are satisfied. The Web service will start."
        $tip.ForeColor = [System.Drawing.Color]::Green
    } else {
        $tip.Text = "Some items are missing. Click Continue to configure automatically, then start the Web service."
        $tip.ForeColor = [System.Drawing.Color]::DarkOrange
    }
    $tip.Location = New-Object System.Drawing.Point(15, 360)
    $tip.Size = New-Object System.Drawing.Size(470, 50)
    $form.Controls.Add($tip)

    $form.AcceptButton = $btnOk
    $form.CancelButton = $btnCancel
    $res = $form.ShowDialog()
    return ($res -eq [System.Windows.Forms.DialogResult]::OK)
}

# ---------- 5. 选择下载目录(交互模式) ----------
function Select-DownloadDir {
    $dlg = New-Object System.Windows.Forms.FolderBrowserDialog
    $dlg.Description = "选择安装包下载位置 (Choose download folder)"
    $dlg.SelectedPath = $env:USERPROFILE
    if ($dlg.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
        return $dlg.SelectedPath
    }
    return $null
}

# ---------- 6. 下载 Python 安装器 ----------
function Download-PythonInstaller {
    param([string]$DestDir)
    $dest = Join-Path $DestDir $script:PY_INSTALLER_NAME
    if (Test-Path $dest) {
        $size = (Get-Item $dest).Length
        if ($size -gt 20MB) {
            Write-Log "Installer already exists: $dest ($([math]::Round($size/1MB,1)) MB)"
            return $dest
        }
        if ($size -eq 0) {
            Remove-Item $dest -Force -ErrorAction SilentlyContinue
        } else {
            Write-Log "Partial file found ($([math]::Round($size/1MB,2)) MB), will resume download."
        }
    }
    Write-Log "Downloading Python $($script:PY_VERSION) ..."
    Write-Log "  $($script:PY_URL)"
    curl.exe -L -C - -o $dest $script:PY_URL --connect-timeout 30
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $dest)) {
        Write-Log "Download failed."
        return $null
    }
    $size = (Get-Item $dest).Length
    if ($size -lt 10MB) {
        Write-Log "Downloaded file looks invalid (size $size bytes)."
        return $null
    }
    Write-Log "Download OK: $dest ($([math]::Round($size/1MB,1)) MB)"
    return $dest
}

# ---------- 7. 静默安装 Python ----------
function Install-PythonSilent {
    param([string]$InstallerPath)
    Write-Log "Installing Python $($script:PY_VERSION) silently (user install, PrependPath=1) ..."
    $argsList = @('/quiet', 'InstallAllUsers=0', 'PrependPath=1', 'Include_test=0', 'Include_launcher=1')
    $proc = Start-Process -FilePath $InstallerPath -ArgumentList $argsList -Wait -PassThru
    if ($proc.ExitCode -eq 0) {
        Write-Log "Python install finished."
        return $true
    }
    # 退出码 1602/1603/3010 等处理
    Write-Log "Python installer exit code: $($proc.ExitCode)"
    return ($proc.ExitCode -eq 0)
}

# ---------- 8. 安装依赖库 (into the project virtualenv) ----------
function Get-InstallSpecs {
    # Map missing IMPORT names back to the ORIGINAL requirement lines taken from
    # requirements.txt (version constraint kept for that package only).
    param([string[]]$MissingMods)
    $specs = @()
    foreach ($m in @($MissingMods)) {
        if (-not $m) { continue }
        $modKey = "$m".Trim().ToLower()
        $pkg = "$m".Trim()
        if ($script:MODULE_TO_PACKAGE.ContainsKey($modKey)) { $pkg = $script:MODULE_TO_PACKAGE[$modKey] }
        $pkgKey = $pkg.ToLower()
        if ($script:REQ_LINES.ContainsKey($pkgKey)) { $specs += $script:REQ_LINES[$pkgKey] }
        elseif ($script:REQ_LINES.ContainsKey($modKey)) { $specs += $script:REQ_LINES[$modKey] }
        else { $specs += $pkg }
    }
    return $specs
}

function Install-Modules {
    # Install ONLY the packages that are actually missing. A single missing
    # module must never drag the already-installed (and working) packages
    # through a full requirements.txt re-resolve (which could downgrade them).
    param([string]$PyExe, [string[]]$MissingMods)
    if (-not $PyExe) { return $false }
    $specs = @(Get-InstallSpecs -MissingMods $MissingMods)
    if ($specs.Count -eq 0) {
        Write-Log "No missing dependencies to install."
        return $true
    }
    Write-Log "Installing missing dependencies only: $($specs -join ', ') ..."
    & $PyExe -m pip install --disable-pip-version-check --no-warn-script-location $specs
    if ($LASTEXITCODE -ne 0) {
        Write-Log "pip install exit code: $LASTEXITCODE"
        return $false
    }
    Write-Log "Missing modules installed."
    return $true
}

# ---------- 9. 创建/复用工程内虚拟环境 .venv ----------
function New-ProjectVenv {
    param([string]$SystemPython)
    $venvPy = Get-VenvPython
    if ($venvPy) {
        Write-Log "Virtualenv already exists: $venvPy"
        # Top up only what is missing; already-installed packages stay untouched.
        $need = @(Check-Modules $venvPy)
        if ($need.Count -eq 0) {
            Write-Log "All required modules are present; nothing to install."
            return $venvPy
        }
        if (-not (Install-Modules $venvPy $need)) { return $null }
        return $venvPy
    }
    if (-not $SystemPython) { return $null }
    Write-Log "Creating project virtualenv at .venv ..."
    & $SystemPython -m venv (Join-Path $PSScriptRoot '.venv')
    if ($LASTEXITCODE -ne 0) {
        Write-Log "venv creation failed (exit code $LASTEXITCODE)"
        return $null
    }
    $venvPy = Get-VenvPython
    if (-not $venvPy) {
        Write-Log "venv was created but the interpreter was not found."
        return $null
    }
    if (-not (Install-Modules $venvPy $script:REQUIRED_MODS)) { return $null }
    return $venvPy
}

# ============================================================
#  Main
# ============================================================
Write-Log "Environment bootstrap started (Python >= $($script:MIN_MAJOR).$($script:MIN_MINOR) required)"

# Parse requirements.txt (module list + original constraint lines) before any
# check/install, so the bootstrap never drifts from the repo and missing
# packages can be installed individually.
Load-RequiredModules

$pythonExe = $null
$pythonVersion = $null
$systemPy = $null
$systemVersion = $null
$systemVersionOk = $false
$venvPy = Get-VenvPython

if ($venvPy) {
    $pythonExe = $venvPy
    $pythonVersion = Get-PythonVersion $venvPy
    Write-Log "Found project virtualenv: $venvPy (Python $pythonVersion)"
} else {
    $systemPy = Find-Python
    if ($systemPy) {
        $systemVersion = Get-PythonVersion $systemPy
        Write-Log "Found system Python: $systemPy (version $systemVersion)"
        if ($systemVersion) {
            $parts = $systemVersion -split '\.'
            $vmajor = [int]$parts[0]; $vminor = [int]$parts[1]
            $systemVersionOk = ($vmajor -gt $script:MIN_MAJOR) -or ($vmajor -eq $script:MIN_MAJOR -and $vminor -ge $script:MIN_MINOR)
        }
    } else {
        Write-Log "Python not found."
    }
}

$missingMods = @(Check-Modules $pythonExe)

# Console/UI summary
$sb = New-Object System.Text.StringBuilder
if ($venvPy) {
    [void]$sb.AppendLine(("Python 3.9+        OK   (venv {0})" -f $pythonVersion))
    [void]$sb.AppendLine("Path: $venvPy")
} elseif ($systemPy) {
    [void]$sb.AppendLine(("Python 3.9+        {0}  ({1})" -f $(if ($systemVersionOk) { 'OK ' } else { 'TOO OLD' }), $systemVersion))
    [void]$sb.AppendLine("Path: $systemPy")
    [void]$sb.AppendLine("Virtualenv      WILL BE CREATED (.venv)")
} else {
    [void]$sb.AppendLine("Python 3.9+        MISSING")
    [void]$sb.AppendLine("Path: (not installed)")
}
foreach ($m in $script:REQUIRED_MODS) {
    $ok = $missingMods -notcontains $m
    [void]$sb.AppendLine(("{0,-18} {1}" -f $m, $(if ($ok) { 'OK' } else { 'MISSING' })))
}
$summary = $sb.ToString()

$allOk = ($venvPy -ne $null) -and ($missingMods.Count -eq 0)

if ($Auto) {
    Write-Host ""
    Write-Host "===== Environment Check ====="
    Write-Host $summary
    Write-Host "============================="
} else {
    $continue = Show-StatusForm -PythonInfo $summary -MissingMods $missingMods -PythonOk ($venvPy -ne $null -or $systemVersionOk)
    if (-not $continue) {
        Write-Log "User cancelled. Environment not ready."
        exit 1
    }
}

if ($allOk) {
    Write-Log "All requirements satisfied. Ready to start."
    exit 0
}

# ---------- 需要配置 ----------
$needPythonInstall = -not ($venvPy -or $systemVersionOk)
if ($Auto) {
    Write-Log "Missing items detected. Auto mode: will install automatically."
} else {
    $msg = "Environment is not ready.`n`n"
    if ($needPythonInstall) { $msg += "- Python $($script:PY_VERSION) is not installed (or too old)`n" }
    if (-not $venvPy) { $msg += "- A project virtualenv (.venv) will be created in this folder`n" }
    $msg += "- Dependencies from requirements.txt will be installed`n"
    $msg += "`nDownload and install everything required now? The launcher will start the local Web service afterwards."
    $ans = [System.Windows.Forms.MessageBox]::Show($msg, "Environment Setup", 'YesNo', 'Question')
    if ($ans -ne 'Yes') {
        Write-Log "User declined setup. Environment not ready."
        exit 1
    }
}

# 1) install Python only when no usable system interpreter exists
if ($needPythonInstall) {
    $downloadDir = $null
    if ($Auto) {
        $downloadDir = Join-Path $env:USERPROFILE 'Downloads'
        if (-not (Test-Path $downloadDir)) { New-Item -ItemType Directory -Path $downloadDir -Force | Out-Null }
    } else {
        $downloadDir = Select-DownloadDir
        if (-not $downloadDir) {
            Write-Log "No download folder selected. Cancelled."
            exit 1
        }
    }
    Write-Log "Download folder: $downloadDir"

    $installer = Download-PythonInstaller $downloadDir
    if (-not $installer) {
        if (-not $Auto) {
            [System.Windows.Forms.MessageBox]::Show("Python download failed. Please check the network and try again.", "Error", 'OK', 'Error') | Out-Null
        }
        exit 1
    }
    if (-not (Install-PythonSilent $installer)) {
        if (-not $Auto) {
            [System.Windows.Forms.MessageBox]::Show("Python installation failed. Please install Python $($script:PY_VERSION) manually from https://python.org", "Error", 'OK', 'Error') | Out-Null
        }
        exit 1
    }
    $systemPy = Find-Python
    if (-not $systemPy) {
        foreach ($pat in $script:FALLBACK_PY_PATTERNS) {
            $hit = Get-ChildItem -Path $pat -ErrorAction SilentlyContinue |
                   Sort-Object FullName -Descending | Select-Object -First 1
            if ($hit) { $systemPy = $hit.FullName; break }
        }
    }
    if (-not $systemPy) {
        if (-not $Auto) {
            [System.Windows.Forms.MessageBox]::Show("Python was installed but not found. Please restart this launcher.", "Error", 'OK', 'Error') | Out-Null
        }
        exit 1
    }
    $systemVersion = Get-PythonVersion $systemPy
    Write-Log "Python now at: $systemPy ($systemVersion)"
}

# 2) create the project virtualenv and install dependencies into it
$venvPy = New-ProjectVenv -SystemPython $systemPy
if (-not $venvPy) {
    Write-Log "Failed to prepare the project virtualenv (.venv)."
    if (-not $Auto) {
        [System.Windows.Forms.MessageBox]::Show("Failed to create .venv. Please run manually:`n  python -m venv .venv`n  .venv\Scripts\python.exe -m pip install -r requirements.txt", "Error", 'OK', 'Error') | Out-Null
    }
    exit 1
}

# 3) final re-check
$missingMods = @(Check-Modules $venvPy)
if ($missingMods.Count -eq 0) {
    Write-Log "Environment is now ready. Starting the local Web service (run.py) next."
    if (-not $Auto) {
        [System.Windows.Forms.MessageBox]::Show("Environment is ready. The local Web service will start now.", "Setup Complete", 'OK', 'Information') | Out-Null
    }
    exit 0
} else {
    Write-Log "Still missing: $($missingMods -join ', ')"
    if (-not $Auto) {
        [System.Windows.Forms.MessageBox]::Show("Environment setup incomplete. Missing: $($missingMods -join ', ')`nPlease try again.", "Error", 'OK', 'Error') | Out-Null
    }
    exit 1
}
