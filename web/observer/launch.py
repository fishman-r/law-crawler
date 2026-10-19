"""保留终端交互的 Claude 启动器，只记录 PID 身份和心跳，不保存命令参数。"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import uuid


TERMINATE_TIMEOUT = 5


def stamp():
    return datetime.now(timezone.utc).isoformat()


def process_identity(pid):
    try:
        boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        text = Path(f'/proc/{pid}/stat').read_text()
        # comm 字段可以含空格甚至括号；从最后的 ')' 后解析字段 3 起。
        process_start = text.rsplit(')',1)[1].split()[19]
        return dict(pid=pid,boot_id=boot_id,process_start=process_start)
    except (OSError,IndexError):
        return dict(pid=pid,boot_id=None,process_start=None)


def save(path, value):
    fd, temporary = tempfile.mkstemp(prefix='.run-',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            json.dump(value,stream)
        os.chmod(temporary,0o600)
        os.replace(temporary,path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _stop_and_reap(process):
    """记录链路中断时回收已启动的进程；不能留下没有运行记录的 Claude。"""
    if process.poll() is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass  # 进程可能刚好自行退出，仍须 wait 回收退出状态。
        try:
            process.wait(timeout=TERMINATE_TIMEOUT)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            process.wait()
    else:
        process.wait()


def run(binary, workspace, runtime_dir, arguments):
    directory = Path(runtime_dir)/'runs'
    directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    if directory.is_symlink():
        raise ValueError('运行记录目录不能使用符号链接')
    run_id = uuid.uuid4().hex
    path = directory/(run_id+'.json')
    environment = dict(os.environ,LAW_CRAWLER_RUN_ID=run_id)
    process = None
    old = {}
    # 前台进程和启动器共享终端进程组，终端 Ctrl-C 会自行传到 Claude；
    # 启动器保留到退出以写回真实结果，不把用户中断当作部署崩溃。
    interrupted = False
    def forward(signum, frame):
        nonlocal interrupted
        interrupted = True
        if signum != signal.SIGINT and process is not None and process.poll() is None:
            try:
                process.send_signal(signum)
            except ProcessLookupError:
                pass
    try:
        process = subprocess.Popen([str(binary),*arguments],cwd=workspace,env=environment)
        # 逐项登记成功替换的 handler，后续初始化失败也能完整恢复。
        for sig in (signal.SIGINT,signal.SIGTERM):
            old[sig] = signal.signal(sig,forward)
        value = dict(id=run_id,started_at=stamp(),heartbeat_at=stamp(),finished_at=None,
                     exit_code=None,**process_identity(process.pid))
        save(path,value)
        while process.poll() is None:
            value['heartbeat_at'] = stamp()
            save(path,value)
            time.sleep(2)
        value.update(finished_at=stamp(),heartbeat_at=stamp(),exit_code=process.returncode,
                     user_interrupt_observed=interrupted)
        save(path,value)
        return process.returncode if process.returncode >= 0 else 128-process.returncode
    except BaseException:
        if process is not None:
            _stop_and_reap(process)
        raise
    finally:
        for sig, handler in old.items():
            signal.signal(sig,handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',type=Path,required=True)
    parser.add_argument('--workspace',type=Path,required=True)
    parser.add_argument('--runtime-dir',type=Path,required=True)
    parser.add_argument('arguments',nargs=argparse.REMAINDER)
    args = parser.parse_args()
    arguments = args.arguments[1:] if args.arguments[:1] == ['--'] else args.arguments
    try:
        return run(args.binary,args.workspace,args.runtime_dir,arguments)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        # 原始 exception 可能携带 Popen 参数；只输出异常类别与排查方向。
        print('启动器失败：'+type(exc).__name__+'；请检查可执行文件及运行目录权限。', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
