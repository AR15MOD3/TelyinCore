# The batch file opens this interactive pre-launch Bot selector.
param([int]$Port = 7799)
# Package admission runs before dependency installation, Bot/config reads or spawn.
$requiredSources = @(
    'startup.py', 'bot_launcher_child.py', 'bot_controller_shutdown.py', 'bot_launcher_lifecycle.py', 'bot_instance_publisher.py', 'bot_owned_instance.py', 'bot_launcher_options.py',
    'startup_terminal.py', 'console_output.py', 'launch.py',
    'scripts\ensure_runtime.py'
)
foreach ($relativeSource in $requiredSources) {
    $sourcePath = Join-Path $PSScriptRoot $relativeSource
    if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
        Write-Host ('[startup-package] missing regular source: ' + $relativeSource) -ForegroundColor Red
        exit 2
    }
}
# End package admission. Presence is NOT integrity, dependency or host authority.
try { chcp 65001 > $null } catch {}
try { [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false) } catch {}
try { [Console]::InputEncoding = [Text.UTF8Encoding]::new($false) } catch {}
$env:PYTHONIOENCODING = 'utf-8'
$root = $PSScriptRoot
Set-Location $root
$py = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $py)) {
    Write-Host '缺少 Python 环境，请先安装核心依赖。' -ForegroundColor Red
    exit 1
}
# Appearance only: the PowerShell preflight runs before the Python console.
# It must not retain a second hard-coded cyan palette.
function Format-CorePreflight {
    param([string]$Text, [string]$Hex, [bool]$UseColor)
    if (-not $UseColor -or $Hex -notmatch '^#[0-9a-fA-F]{6}$') { return $Text }
    $rgb = @([Convert]::ToInt32($Hex.Substring(1, 2), 16),
             [Convert]::ToInt32($Hex.Substring(3, 2), 16),
             [Convert]::ToInt32($Hex.Substring(5, 2), 16)) -join ';'
    $esc = [char]27
    return "${esc}[38;2;${rgb}m${Text}${esc}[0m"
}
$preflightAccent = '#52749e'
try {
    $preset = Get-Content -LiteralPath (Join-Path $root 'presets\default.json') -Raw -Encoding UTF8 -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
    if ($preset.'--color-mint' -match '^#[0-9a-fA-F]{6}$') { $preflightAccent = $preset.'--color-mint' }
} catch { } # A missing optional palette must not block dependency checks.
$preflightColor = (-not [Console]::IsOutputRedirected) -and $Host.UI.SupportsVirtualTerminal -and
                  (-not (Test-Path Env:NO_COLOR)) -and ($env:TERM -ne 'dumb')
Write-Host (Format-CorePreflight '  TELYIN  CORE  ·  正在检查运行环境…' $preflightAccent $preflightColor)
$check = & $py (Join-Path $root 'scripts\ensure_runtime.py') --install 2>&1
if ($LASTEXITCODE -ne 0) { $check | Write-Host; exit $LASTEXITCODE }
if (-not (Test-Path -LiteralPath (Join-Path $root 'dist\web\index.html'))) {
    Write-Host '前端尚未构建，请先执行 pnpm run build:web。' -ForegroundColor Red
    exit 1
}
# Direct invocation preserves arrow-key input and Ctrl-C in the same console.
& $py -u (Join-Path $root 'startup.py') --port $Port
exit $LASTEXITCODE
