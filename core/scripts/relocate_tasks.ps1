param([Parameter(Mandatory=$true)][string]$Previous, [Parameter(Mandatory=$true)][string]$Current)
$ErrorActionPreference='Stop'
$prefix=[regex]::Escape($Previous)+'(?=[\\/])'
$pattern=[regex]::new($prefix,[Text.RegularExpressions.RegexOptions]::IgnoreCase)
$replace=[Text.RegularExpressions.MatchEvaluator]{param($match) $Current}
$repairs=0
foreach ($task in Get-ScheduledTask) {
    if ($task.Actions.Count -ne 1) { continue }
    $action=$task.Actions[0]
    $exe=[string]$action.Execute
    $argsText=[string]$action.Arguments
    $work=[string]$action.WorkingDirectory
    if (!$pattern.IsMatch("$exe $work $argsText")) { continue }
    $python=Join-Path $Previous 'core\.venv\Scripts\python.exe'
    $oldCore=Join-Path $Previous 'core'
    $cmd=($exe -eq 'cmd.exe' -and $work -eq $oldCore -and $argsText.StartsWith('/d /s /c ""'+$python+'" -u "'+$oldCore+'\startup.py"',[StringComparison]::OrdinalIgnoreCase))
    $storage=($exe -eq $python -and $argsText -eq ('-B '+$oldCore+'\tools\storage_watch.py --once'))
    $launcher=($exe -eq $python -and $work -eq $oldCore -and ($argsText -match '(^|\s)startup\.py(\s|$)' -or $argsText.Contains($oldCore+'\scripts\windows_acceptance.py')))
    if (!($cmd -or $storage -or $launcher)) { continue }
    if ($cmd -and $Current -match '[%!"^&|<>\r\n]') { throw 'Cannot safely quote cmd task path' }
    $newExe=$pattern.Replace($exe,$replace)
    $newArgs=$pattern.Replace($argsText,$replace)
    $newWork=$pattern.Replace($work,$replace)
    if ($storage) { $newArgs='-B "'+(Join-Path $Current 'core\tools\storage_watch.py')+'" --once' }
    if ($launcher -and $argsText.Contains($oldCore+'\scripts\windows_acceptance.py') -and $Current.Contains(' ')) {
        $entry=Join-Path $Current 'core\scripts\windows_acceptance.py'
        if (!$newArgs.Contains('"'+$entry+'"')) {$newArgs=$newArgs.Replace($entry,'"'+$entry+'"')}
    }
    $script=if ($storage) {'core\tools\storage_watch.py'} elseif ($argsText.Contains('windows_acceptance.py')) {'core\scripts\windows_acceptance.py'} else {'core\startup.py'}
    if (!(Test-Path -LiteralPath (Join-Path $Current $script) -PathType Leaf) -or !(Test-Path -LiteralPath (Join-Path $Current 'core\.venv\Scripts\python.exe') -PathType Leaf)) { throw 'Missing task entry point' }
    $xml=Export-ScheduledTask -TaskName $task.TaskName -TaskPath $task.TaskPath
    $hash=[BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::Unicode.GetBytes($xml))).Replace('-','')
    $dir=Join-Path $Current 'runtime\relocation-backups\scheduled-tasks'
    [IO.Directory]::CreateDirectory($dir) | Out-Null
    $archive=Join-Path $dir ($hash+'.xml')
    if (!(Test-Path -LiteralPath $archive)) {[IO.File]::WriteAllText($archive,$xml,[Text.Encoding]::Unicode)}
    if ([IO.File]::ReadAllText($archive) -cne $xml) {throw 'Task backup mismatch'}
    $params=@{Execute=$newExe;Argument=$newArgs}
    if ($newWork){$params.WorkingDirectory=$newWork}
    if ($action.Id){$params.Id=$action.Id}
    $next=New-ScheduledTaskAction @params
    Set-ScheduledTask -TaskName $task.TaskName -TaskPath $task.TaskPath -Action $next | Out-Null
    $checked=Get-ScheduledTask -TaskName $task.TaskName -TaskPath $task.TaskPath
    if ($checked.Settings.Enabled -ne $task.Settings.Enabled -or $checked.Actions[0].Execute -ne $newExe -or $checked.Actions[0].Arguments -ne $newArgs -or [string]$checked.Actions[0].WorkingDirectory -ne $newWork) {throw 'Task verification failed'}
    $repairs++
}
Write-Output $repairs
