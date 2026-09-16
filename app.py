import copy
import csv
import io
import json
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import runtime
from fetcher import fetch
from model import Store, PASSWORD, reconcile
from subscriptions import parse_subscription

runtime.DATA.mkdir(parents=True, exist_ok=True)
if os.name != 'nt':
    runtime.DATA.chmod(0o700)
store = Store(runtime.DATA / 'state.db')
lock = threading.RLock()
previews = {}


def event(state, message):
    state['events'] = ([{'time': time.strftime('%Y-%m-%d %H:%M:%S'), 'message': message}] + state['events'])[:150]


def commit(state, message):
    old = store.read()
    state['revision'] = old['revision'] + 1
    event(state, message)
    runtime.apply(state)
    try:
        store.write(state)
    except Exception:
        runtime.apply(old)
        raise


def preview(payload):
    url = str(payload.get('url', '')).strip()
    text = str(payload.get('text', ''))
    if not text:
        text = fetch(url, runtime.MARK if runtime.LIVE else 0)
    nodes, errors = parse_subscription(text)
    with lock:
        state = store.read()
        sub_id = payload.get('id') or uuid.uuid4().hex
        sub = next((s for s in state['subscriptions'] if s['id'] == sub_id), None)
        if not sub:
            sub = {'id': sub_id, 'nodes': []}
            state['subscriptions'].append(sub)
        sub.update(name=str(payload.get('name') or sub.get('name') or '我的订阅')[:80], url=url,
                   interval=max(0, min(10080, int(payload.get('interval', 0)))))
        proposed, added = reconcile(state, sub_id, nodes)
        proposed_sub = next(s for s in proposed['subscriptions'] if s['id'] == sub_id)
        proposed_sub.update(last_update=time.time(), last_error='', skipped=errors)
        token = uuid.uuid4().hex
        now = time.time()
        for key in list(previews):
            if previews[key]['expires'] < now:
                del previews[key]
        if len(previews) >= 20:
            previews.pop(next(iter(previews)))
        previews[token] = {'expires': now + 600, 'state': proposed, 'revision': state['revision']}
        return {'token': token, 'nodes': [{'name': n['name'], 'type': n['type']} for n in nodes],
                'count': len(nodes), 'added_accounts': added,
                'unbound_accounts': sum(not a.get('node_id') for a in proposed['accounts']), 'skipped': errors}


def apply_preview(token):
    with lock:
        p = previews.get(token)
        if not p or p['expires'] < time.time():
            raise ValueError('预览已过期，请重新解析')
        if p['revision'] != store.read()['revision']:
            raise ValueError('配置已经变化，请重新解析订阅')
        commit(p['state'], '订阅已应用，账号信息保留')
        del previews[token]


def snapshot():
    with lock:
        state = store.read()
    for sub in state['subscriptions']:
        # URL contains a bearer token; only return it through explicit edit endpoint.
        sub['source'] = urlsplit(sub['url']).hostname if sub.get('url') else '手动导入'
        sub.pop('url', None)
        sub['nodes'] = [{k: n[k] for k in ('id', 'name', 'type', 'server', 'port')} for n in sub['nodes']]
    return {**state, 'services': runtime.service_status(), 'sessions': runtime.sessions(), 'server': runtime.LAN, 'password': PASSWORD}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send(self, code, value, mime='application/json; charset=utf-8', filename=None):
        data = value.encode() if isinstance(value, str) else json.dumps(value, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'")
        if filename:
            self.send_header('Content-Disposition', 'attachment; filename="' + filename + '"')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlsplit(self.path).path
        try:
            if path == '/api/state':
                return self.send(200, snapshot())
            if path == '/api/export':
                output = io.StringIO()
                writer = csv.writer(output)
                writer.writerow(['服务器', '账号', '密码', '拨号IP', '线路'])
                state = store.read()
                nodes = {n['id']: n for s in state['subscriptions'] for n in s['nodes']}
                for a in state['accounts']:
                    name = nodes.get(a.get('node_id'), {}).get('name', '无可用线路')
                    if name.startswith(('=', '+', '-', '@')):
                        name = "'" + name
                    writer.writerow([runtime.LAN, a['username'], PASSWORD, a['ip'], name])
                return self.send(200, '\ufeff' + output.getvalue(), 'text/csv; charset=utf-8', 'vpn-accounts.csv')
            files = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'application/javascript'), '/style.css': ('style.css', 'text/css')}
            if path in files:
                name, mime = files[path]
                return self.send(200, (runtime.ROOT / 'web' / name).read_text('utf-8'), mime + '; charset=utf-8')
            self.send(404, {'error': '未找到'})
        except Exception:
            self.send(500, {'error': '读取状态失败'})

    def do_POST(self):
        try:
            origin = self.headers.get('Origin')
            if origin and urlsplit(origin).netloc != self.headers.get('Host'):
                return self.send(403, {'error': '不允许跨站请求'})
            if self.headers.get('X-Line-Request') != '1':
                return self.send(403, {'error': '缺少请求标识'})
            length = int(self.headers.get('Content-Length', 0))
            if not 0 < length <= 5 * 1024 * 1024:
                raise ValueError('请求大小无效')
            payload = json.loads(self.rfile.read(length))
            path = urlsplit(self.path).path
            if path == '/api/preview':
                return self.send(200, preview(payload))
            if path == '/api/apply':
                apply_preview(payload['token'])
            elif path == '/api/subscription':
                with lock:
                    sub = next(s for s in store.read()['subscriptions'] if s['id'] == payload['id'])
                    return self.send(200, {k: sub.get(k) for k in ('id', 'name', 'url', 'interval')})
            elif path == '/api/refresh':
                with lock:
                    sub = next(s for s in store.read()['subscriptions'] if s['id'] == payload['id'])
                p = preview(sub)
                apply_preview(p['token'])
            elif path == '/api/account':
                with lock:
                    state = store.read()
                    a = next(a for a in state['accounts'] if a['id'] == int(payload['id']))
                    if 'node_id' in payload:
                        if payload['node_id'] not in {n['id'] for s in state['subscriptions'] for n in s['nodes']}:
                            raise ValueError('线路不存在')
                        a.update(node_id=payload['node_id'], manual=True)
                    if 'enabled' in payload:
                        a['enabled'] = bool(payload['enabled'])
                    commit(state, a['username'] + ' 已更新')
                    if not a['enabled']:
                        runtime.disconnect(a['username'])
            elif path == '/api/disconnect':
                a = next(a for a in store.read()['accounts'] if a['id'] == int(payload['id']))
                runtime.disconnect(a['username'])
            elif path == '/api/test':
                state = store.read()
                n = next(n for s in state['subscriptions'] for n in s['nodes'] if n['id'] == payload['id'])
                return self.send(200, runtime.test_node(n))
            else:
                return self.send(404, {'error': '未找到'})
            self.send(200, {'ok': True})
        except (ValueError, KeyError, StopIteration) as exc:
            message = str(exc) if isinstance(exc, ValueError) else '记录不存在或参数缺失'
            self.send(400, {'error': message[:200]})
        except Exception:
            self.send(500, {'error': '操作失败，原配置已保留；请检查服务状态'})


def scheduler():
    last_attempt = {}
    while True:
        time.sleep(30)
        for sub in store.read()['subscriptions']:
            minutes = sub.get('interval', 0)
            previous = max(sub.get('last_update', 0), last_attempt.get(sub['id'], 0))
            if not minutes or not sub.get('url') or time.time() - previous < minutes * 60:
                continue
            last_attempt[sub['id']] = time.time()
            try:
                p = preview(sub)
                apply_preview(p['token'])
            except Exception:
                with lock:
                    state = store.read()
                    current = next(s for s in state['subscriptions'] if s['id'] == sub['id'])
                    current['last_error'] = '自动更新失败，保留原配置'
                    event(state, current['name'] + '：自动更新失败')
                    store.write(state)


if __name__ == '__main__':
    if runtime.LIVE:
        runtime.apply(store.read())
    threading.Thread(target=scheduler, daemon=True).start()
    server = ThreadingHTTPServer((os.environ.get('LINE_BIND', '127.0.0.1'), int(os.environ.get('LINE_PORT', '8088'))), Handler)
    print('线路转换控制台已启动', flush=True)
    server.serve_forever()
