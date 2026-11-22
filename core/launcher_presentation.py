"""Presentation only: coherent launcher messages and durable failure reports."""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import sys
import traceback
from uuid import uuid4


def notice(area: str, text: str, *, level: str = 'INFO') -> None:
    from startup_terminal import render_record
    sys.stdout.write(render_record(datetime.now().strftime('%H:%M:%S'), area, text, level, stream=sys.stdout))
    sys.stdout.flush()


def startup_header(text: str) -> None:
    from startup_terminal import header
    header(text)


def report_failure(core: Path, error: BaseException) -> int:
    """Never clear guards, relabel an unknown native subtree, or print success."""
    admission = getattr(error, '_host_admission', None)
    rows = []
    for row in getattr(admission, 'records', ()):
        rows.append(dict(empty_confirmed=bool(row.get('empty')), retired=bool(row.get('retired')),
                         error_type=type(row['error']).__name__ if row.get('error') is not None else None,
                         error=str(row['error'])[:512] if row.get('error') is not None else None,
                         process_exit_code=getattr(row.get('process'), 'returncode', None),
                         process_pid=getattr(row.get('process'), 'pid', None)))
    known = 'original native birth/job responsibility unresolved' in str(error)
    outcome_only = getattr(error, 'native_retirement_confirmed', False) is True
    exit_code = getattr(error, 'controller_exit_code', None)
    interrupted = outcome_only and exit_code == 130
    phase = '运行中断' if interrupted else '运行异常结束' if outcome_only else '退出收尾' if known or getattr(error, '_launcher_failure_phase', '') == 'shutdown' else '启动或运行'
    code = ('LAUNCHER_INTERRUPTED' if interrupted else 'LAUNCHER_EXIT_FAILED' if outcome_only
            else 'LAUNCHER_SHUTDOWN_FAILED' if phase == '退出收尾' else 'LAUNCHER_START_FAILED')
    report = dict(code=code, time=datetime.now().astimezone().isoformat(), phase=phase,
                  error_type=type(error).__name__, error=str(error),
                  owner_marker_retained=bool(admission is not None and not getattr(admission, 'completed', False)),
                  owner_marker_name=getattr(getattr(admission, 'owner', None), 'name', None),
                  native_retirement=rows, controller_exit_code=exit_code, native_retirement_confirmed=outcome_only, traceback=''.join(traceback.format_exception(error)))
    path = None
    try:
        folder = Path(core) / 'data' / 'diagnostics'
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / ('launcher-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid4().hex[:8] + '.json')
        with path.open('x', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        path = None
    startup_header('本次运行已中断' if interrupted else '本次运行异常结束' if outcome_only else phase + '尚未完成')
    notice('shutdown' if known or outcome_only else 'startup',
           ('控制器以中断状态 130 退出，原生进程组收尾已确认。' if interrupted
            else '控制器异常退出（退出码 ' + str(exit_code) + '），原生进程组收尾已确认。') if outcome_only else
           '原生进程组的收尾未通过确认，退出保护记录仍需核对。' if known
           else '本次操作遇到异常，尚未确认完成。', level='WARN' if interrupted else 'ERROR')
    notice('report', '故障编号 ' + code + ' · ' + type(error).__name__)
    if report['owner_marker_retained']:
        notice('shutdown', '运行完成状态尚未确认，启动保护记录已保留。' if outcome_only
               else '启动保护记录已保留；请勿手动删除或把它当作正常离线。', level='WARN')
    if rows:
        notice('shutdown', '控制器退出码：' + ', '.join(str(r['process_exit_code']) for r in rows)
               + '；进程组收尾已确认：' + str(sum(r['retired'] and not r['error_type'] for r in rows)) + '/' + str(len(rows)))
    if path is not None:
        notice('report', '完整诊断：' + str(path))
        notice('report', '可将这份诊断交给开发者核对，保护记录暂时保留。' if report['owner_marker_retained']
               else '错误详情已保留；本次启动器不再阻止档案管理。' if outcome_only
               else '可将这份诊断交给开发者核对。')
    else:
        notice('report', '诊断文件未能保存，以下保留原始错误。', level='ERROR')
        traceback.print_exception(error)
    return 130 if interrupted else 1
