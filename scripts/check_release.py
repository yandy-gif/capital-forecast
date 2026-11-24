"""检查 Git 暂存快照，阻止本地数据和内嵌财务记录进入公开仓库。"""
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

# 内嵌初始数据的页面（v9 源 + v10 产物）：需要核对 DATA 里没有业务记录
EMBEDDED_PAGES = {'珀研Q4资金预测看板v9.html', '生态资金计划系统v10.html',
                  '珀研Q4资金预测看板v8.html'}


def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args])


def main():
    paths = git('ls-files', '-z').decode('utf-8').split('\0')
    errors = []
    forbidden = {'.xlsx','.xls','.xlsm','.csv','.tsv','.zip','.db','.sqlite','.sqlite3','.png','.jpg','.pdf','.log','.pid'}
    dirs = {'uploads','backup','修复备份','测试样例','tmp','.runtime','node_modules','.venv','__pycache__','.workbuddy','.archify','.codex'}
    for name in filter(None, paths):
        p = pathlib.PurePosixPath(name)
        if p.suffix.lower() in forbidden or any(x in dirs or x.startswith('_archive') for x in p.parts) or re.search(r'\.(db-(wal|shm)|bak\d*)$', name):
            errors.append('禁止发布本地数据或运行文件：'+name)
            continue
        content = git('show', ':'+name).decode('utf-8-sig')
        if re.search(r'(?i)(gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----)', content):
            errors.append('疑似凭据：'+name)
        if re.search(r'[A-Za-z]:[\\/]+Users[\\/]+', content):
            errors.append('个人绝对路径：'+name)
        if p.suffix == '.html' and name not in EMBEDDED_PAGES:
            # v11 单页看板从 /api 取数，产物里不该内嵌任何业务记录
            if re.search(r'const DATA\s*=', content):
                errors.append('页面内嵌了业务数据：'+name)
            elif re.search(r'"(counterparty|cp_account|biz_key|restricted_kind)"\s*:', content) and \
                    not re.search(r'/api/(snapshot|meta|import)', content):
                errors.append('页面含业务字段：'+name)
            continue
        if p.suffix == '.html':
            found = re.search(r'const DATA = (.*);', content)
            if not found:
                errors.append('缺少可核对的页面初始数据：'+name)
                continue
            data = json.loads(found.group(1))
            for key in ('plan','actAll','actBig','unmatched','kaCheck'):
                if data.get(key): errors.append('页面含业务记录 '+key+'：'+name)
            for key in ('actInTotal','actOutTotal','invest','internalNet','unclassifiedNet','kaPlan'):
                if data.get(key): errors.append('页面含历史金额 '+key+'：'+name)
            if any(x is not None for x in data.get('balHist', [])):
                errors.append('页面含历史余额：'+name)
            def nonzero(value):
                if isinstance(value,dict): return any(nonzero(v) for v in value.values())
                if isinstance(value,(int,float)): return value != 0
                return bool(value)
            if nonzero(data.get('budget',{})): errors.append('页面含历史预算：'+name)
        if name == '数仓/defaults.json':
            data = json.loads(content)
            if set(data) != {'subjects','bridge','params','fx'}:
                errors.append('基础配置包含未许可的数据分类')
    if errors:
        print('\n'.join(errors))
        return 1
    print('发布检查通过：暂存快照未包含禁止的数据文件、页面业务记录或常见凭据。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
