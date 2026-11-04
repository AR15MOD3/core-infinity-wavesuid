# SPDX-License-Identifier: GPL-3.0-or-later
# Task-created Waves account adapter, publication expressly authorized 2026-11-04.
"""Local operator roster for one Bot. Never log or return credential columns.

Read-only SQLite snapshots while running; deletion requires the existing plugin
lifecycle lock, verified disabled/unloaded state and an unoccupied service port.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import socket
import uuid

NAME='core-infinity-wavesuid'
GAME=3
TABLES=('WavesBind','WavesUser','WavesUserSdk','WavesGachaCloud')

def service_port_busy(port):
    # Failure to obtain an exclusive local bind is not evidence of retirement.
    try:
        with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as s:
            if os.name=='nt':s.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
            s.bind(('127.0.0.1',port))
        return False
    except OSError:
        return True

def safe(path):
    path=Path(path).absolute()
    for part in (path,*path.parents):
        if part.is_symlink() or (part.exists() and getattr(part.stat(),'st_file_attributes',0)&0x400):
            raise ValueError('鸣潮账号路径不允许重定向')
    return path

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()

def uids(value):
    parts=str(value or '').split('_')
    if any(p and re.fullmatch(r'[0-9]{9}',p) is None for p in parts):
        raise ValueError('鸣潮绑定数据格式无法识别；未修改')
    return [p for p in parts if p]

class WavesAccounts:
    def __init__(self,context):
        self.bot=context['bot_id']
        self.scope=safe(Path(context['config_path']).parent)
        self.config=safe(Path(context['config_path']))
        self.resource=safe(Path(context['data_dir']))
        if (not self.bot or self.scope.name!=NAME or self.config.name!='config.json'
                or self.resource!=self.scope/'runtime'):
            raise ValueError('正式插件页面作用域不完整')
        self.plugins=lambda: {'plugins':[dict(context['plugin'])]}
        self.port_busy=service_port_busy
        self.open_private_new=context['open_private_new']

    def settings(self):
        config=self.config
        if not config.is_file():raise ValueError('当前 Bot 尚未配置鸣潮插件')
        data=json.loads(config.read_text(encoding='utf-8-sig'))
        if data.get('ownerBot')!=self.bot:raise ValueError('鸣潮配置归属不匹配')
        root=safe(Path(str(data.get('dataDir') or '')))
        if not Path(str(data.get('dataDir') or '')).is_absolute() or self.resource not in root.parents:
            raise ValueError('鸣潮数据须位于当前 Bot 私有目录')
        port=data.get('port')
        if type(port) is not int or not 1024<=port<=65535:raise ValueError('鸣潮服务端口无效')
        cfg=safe(root/'configs/database_config.json')
        if cfg.exists():
            db=json.loads(cfg.read_text(encoding='utf-8-sig'))
            kind=db.get('db_type','SQLite');kind=kind.get('data') if isinstance(kind,dict) else kind
            if str(kind).lower()!='sqlite':raise ValueError('账号管理暂只支持本 Bot 的 SQLite 数据库')
        return safe(root/'WavesData.db'),port

    def plugin(self):
        rows=[p for p in self.plugins()['plugins'] if p.get('name')==NAME]
        if len(rows)!=1:raise ValueError('当前 Bot 插件状态不可确认')
        return rows[0]

    def stopped(self,plugin,port):
        return (plugin.get('enabled') is False and plugin.get('loaded') is False
            and plugin.get('tracked') is False and plugin.get('lifecycle')=='unloaded'
            and not self.port_busy(port))

    def connect(self,path,write=False):
        # Never create a database as a side effect of opening this page.
        c=sqlite3.connect(path.as_uri()+('?mode=rw' if write else '?mode=ro'),uri=True,timeout=2)
        c.row_factory=sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        if not write:c.execute('PRAGMA query_only=ON')
        c.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
        return c

    def rows(self,c):
        present={r[0].casefold() for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'wavesbind','wavesuser'}<=present:raise ValueError('鸣潮账号表尚未就绪')
        result={}
        for table in TABLES:
            if table.casefold() not in present:result[table]=[];continue
            columns={r[1] for r in c.execute('PRAGMA table_info("'+table+'")')}
            required={'id','user_id','bot_id','uid'}
            if not required<=columns or (table=='WavesUser' and not {'cookie','status','game_id'}<=columns):
                raise ValueError('鸣潮账号结构不支持，未修改数据')
            if table=='WavesBind' and 'pgr_uid' not in columns:
                raise ValueError('鸣潮绑定结构不支持，未修改数据')
            sql='SELECT * FROM "'+table+'"'+(' WHERE game_id=?' if table=='WavesUser' else '')+' ORDER BY id'
            result[table]=[dict(r) for r in c.execute(sql,(GAME,) if table=='WavesUser' else ())]
        return result

    def roster(self,rows):
        roster={}
        for table in TABLES:
            for r in rows[table]:
                owner=(str(r['user_id']),str(r['bot_id']))
                if not owner[0] or not owner[1]:raise ValueError('账号归属字段不完整')
                for uid in (uids(r['uid']) if table=='WavesBind' else [str(r['uid'] or '')]):
                    if re.fullmatch(r'[0-9]{9}',uid) is None:continue
                    key=(*owner,uid)
                    entry=roster.setdefault(key,dict(userId=owner[0],platform=owner[1],uid=uid,bound=False,
                        loginSaved=False,loginState='未保存登录',_rows={t:[] for t in TABLES}))
                    entry['_rows'][table].append(r)
                    if table=='WavesBind':entry['bound']=True
                    if table=='WavesUser' and r.get('cookie'):
                        entry['loginSaved']=True
                        entry['loginState']='登录信息已标记失效' if r.get('status') else '已保存登录（未在线验证）'
                    if table=='WavesGachaCloud' and r.get('login_info'):
                        entry['loginSaved']=True
                        if entry['loginState']=='未保存登录':entry['loginState']='已保存抽卡登录（未在线验证）'
        output=[]
        for key,item in sorted(roster.items()):
            if not item['bound'] and not item['loginSaved']:continue
            item['id']=digest([self.bot,*key])
            item['revision']=digest([self.bot,*key,item.pop('_rows')])
            output.append(item)
        return output

    def snapshot(self):
        path,port=self.settings();plugin=self.plugin()
        if not path.exists():accounts=[]
        else:
            c=self.connect(path)
            try:accounts=self.roster(self.rows(c))
            finally:c.close()
        return dict(bot=self.bot,plugin=dict(installed=plugin.get('installed',True),enabled=plugin.get('enabled') is True,
            loaded=plugin.get('loaded') is True),accounts=accounts,
            people=len({(r['platform'],r['userId']) for r in accounts}),accountCount=len(accounts),
            boundPeople=len({(r['platform'],r['userId']) for r in accounts if r['bound']}),
            loginPeople=len({(r['platform'],r['userId']) for r in accounts if r['loginSaved']}),
            deleteAllowed=self.stopped(plugin,port))

    def delete(self,body):
        if type(body) is not dict or set(body)!={'active','id','revision','confirm'} or body.get('active')!=self.bot or body.get('confirm') is not True:
            raise ValueError('删除需当前 Bot、所选记录和明确确认')
        if any(not isinstance(body.get(k),str) or re.fullmatch(r'[0-9a-f]{64}',body[k]) is None for k in ('id','revision')):
            raise ValueError('账号记录或版本无效')
        path,port=self.settings();p=self.plugin()
        if not self.stopped(p,port):
            raise ValueError('请先停用鸣潮插件，确认服务退出后再删除')
        c=self.connect(path,write=True)
        try:
            rows=self.rows(c);accounts=self.roster(rows)
            found=[r for r in accounts if r['id']==body['id'] and r['revision']==body['revision']]
            if len(found)!=1:raise ValueError('账号记录已变化，请刷新后重新确认')
            item=found[0];owner=(item['userId'],item['platform']);uid=item['uid']
            selected={t:[r for r in rows[t] if (str(r['user_id']),str(r['bot_id']))==owner
                and (uid in uids(r['uid']) if t=='WavesBind' else str(r['uid'])==uid)] for t in TABLES}
            backup=safe(self.scope/'account-backups');backup.mkdir(exist_ok=True)
            with self.open_private_new(backup/(uuid.uuid4().hex+'.json')) as f:
                f.write(json.dumps(dict(bot=self.bot,selected=selected),ensure_ascii=False).encode());f.flush();os.fsync(f.fileno())
            for r in selected['WavesBind']:
                remaining=[u for u in uids(r['uid']) if u!=uid]
                if not remaining and not r.get('pgr_uid'):
                    c.execute('DELETE FROM WavesBind WHERE id=?',(r['id'],))
                else:c.execute('UPDATE WavesBind SET uid=? WHERE id=?',('_'.join(remaining),r['id']))
            for t in TABLES[1:]:
                for r in selected[t]:c.execute('DELETE FROM "'+t+'" WHERE id=?',(r['id'],))
            c.commit()
        except BaseException:
            c.rollback();raise
        finally:c.close()
        # No automatic retry: even a failed refresh cannot repeat a deletion.
        return dict(ok=True,bot=self.bot,deletedId=body['id'],message='已删除此 Bot 保存的该账号绑定及登录信息；本机回滚备份已保留。')


def handle(context, action, payload):
    """Operator-only host call, never registered as an agent tool."""
    admin=WavesAccounts(context)
    if action=='accounts':
        if payload!={}:raise ValueError('名单读取不接受额外参数')
        return admin.snapshot()
    if action=='delete_account':return admin.delete(payload)
    raise ValueError('未知鸣潮页面操作')
