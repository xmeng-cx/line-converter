"""Parse subscription data without evaluating YAML objects or retaining raw URLs in logs."""
import base64
import hashlib
import json
from urllib.parse import urlsplit, parse_qs, unquote

import yaml


def truth(value):
    return str(value).lower() in ('1', 'true', 'yes')


def b64(text):
    raw = ''.join(text.split())
    return base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4)).decode('utf-8-sig')


def normalize(item):
    kind = item.get('type', '')
    kind = {'hy2': 'hysteria2', 'socks': 'socks5'}.get(kind, kind)
    if kind not in ('hysteria2', 'socks5'):
        raise ValueError('不支持的协议：' + str(kind)[:30])
    tls = item.get('tls') or {}
    if not isinstance(tls, dict):
        if tls and kind == 'socks5':
            raise ValueError('暂不支持 SOCKS5 over TLS')
        tls = {}
    if kind == 'socks5' and tls.get('enabled'):
        raise ValueError('暂不支持 SOCKS5 over TLS')
    server = str(item.get('server', '')).strip().strip('[]')
    port = int(item.get('port', item.get('server_port', 0)))
    if not server or any(c.isspace() for c in server) or not 1 <= port <= 65535:
        raise ValueError('节点地址或端口无效')
    if item.get('ports') or item.get('server_ports'):
        raise ValueError('暂不支持端口跳跃，请提供固定端口')
    obfs = item.get('obfs') or ''
    obfs_password = item.get('obfs-password', '')
    if isinstance(obfs, dict):
        obfs_password = obfs.get('password', '')
        obfs = obfs.get('type', '')
    if obfs not in ('', 'salamander'):
        raise ValueError('不支持的混淆方式')
    node = dict(type=kind, name=str(item.get('name', item.get('tag', server))),
                server=server, port=port, username=str(item.get('username', '')),
                password=str(item.get('password', '')),
                sni=str(item.get('sni', tls.get('server_name', server))),
                insecure=truth(item.get('skip-cert-verify', tls.get('insecure', False))),
                obfs=obfs, obfs_password=str(obfs_password))
    node['key'] = hashlib.sha256(json.dumps([kind, server, port, node['username']], ensure_ascii=False).encode()).hexdigest()[:24]
    return node


def parse_link(line):
    p = urlsplit(line)
    q = {k: v[0] for k, v in parse_qs(p.query).items()}
    kind = p.scheme.lower()
    if kind not in ('hy2', 'hysteria2', 'socks', 'socks5'):
        raise ValueError('不支持的协议：' + kind)
    auth = p.netloc.rsplit('@', 1)[0] if '@' in p.netloc else ''
    username, password = '', ''
    if kind in ('hy2', 'hysteria2'):
        password = unquote(auth)
    elif auth:
        if ':' not in auth:
            auth = b64(auth)
        username, _, password = auth.partition(':')
        username, password = unquote(username), unquote(password)
    if q.get('mport'):
        raise ValueError('暂不支持端口跳跃')
    return normalize(dict(type=kind, name=unquote(p.fragment) or p.hostname,
                          server=p.hostname, port=p.port or (443 if kind in ('hy2', 'hysteria2') else 1080),
                          username=username, password=password,
                          sni=q.get('sni', p.hostname),
                          **{'skip-cert-verify': q.get('insecure', '0'),
                             'obfs': q.get('obfs', ''), 'obfs-password': q.get('obfs-password', '')}))


def parse_subscription(text):
    text = text.strip().lstrip('\ufeff')
    if len(text) > 4 * 1024 * 1024:
        raise ValueError('订阅超过 4 MB 限制')
    if not any(x in text for x in ('://', 'proxies:', 'outbounds', '{', '[')):
        try:
            text = b64(text)
        except Exception:
            raise ValueError('无法识别订阅格式') from None
    nodes, errors = [], []
    if text.startswith(('{', '[')) or 'proxies:' in text:
        try:
            document = yaml.safe_load(text)
            entries = document if isinstance(document, list) else document.get('proxies', document.get('outbounds', []))
            if not isinstance(entries, list):
                raise ValueError()
        except Exception:
            raise ValueError('订阅 JSON/YAML 格式无效') from None
        for i, entry in enumerate(entries):
            try:
                nodes.append(normalize(entry))
            except Exception as exc:
                errors.append({'index': i + 1, 'reason': str(exc) if isinstance(exc, ValueError) else '节点格式无效'})
    else:
        for i, line in enumerate(text.splitlines()):
            if not line.strip() or line.lstrip().startswith('#'):
                continue
            try:
                nodes.append(parse_link(line.strip()))
            except Exception as exc:
                errors.append({'index': i + 1, 'reason': str(exc) if isinstance(exc, ValueError) and '://' not in str(exc) else '节点链接无效'})
    # Preserve intentional duplicate entries; each subscription entry represents a line.
    counts = {}
    for n in nodes:
        key = n['key']
        counts[key] = counts.get(key, 0) + 1
        n['key'] = key + ':' + str(counts[key])
    if not nodes:
        raise ValueError('订阅没有可用的 HY2 / SOCKS5 节点，原配置已保留')
    if len(nodes) > 2000:
        raise ValueError('单份订阅最多支持 2000 条线路')
    return nodes, errors
