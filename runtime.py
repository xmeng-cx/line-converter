"""Dedicated sing-box and accel-ppp configuration; changes only project-owned files."""
import json
import os
from pathlib import Path
import subprocess
import time

from model import PASSWORD

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('LINE_DATA', str(ROOT / 'data')))
CORE = os.environ.get('LINE_CORE', '/opt/line-converter/bin/sing-box')
LIVE = os.environ.get('LINE_LIVE', '0') == '1'
LAN = os.environ.get('LINE_LAN_IP', '192.168.1.144')
MARK = 16602


def run(args, timeout=20, check=True):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        # Core diagnostics may contain credentials; details stay in root-only staging files.
        raise RuntimeError('执行失败：' + Path(args[0]).name + '，请查看服务日志')
    return result


def atomic(path, value):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(value, encoding='utf-8')
    tmp.chmod(0o600)
    os.replace(tmp, path)


def outbound(node):
    result = {'tag': node['id'], 'server': node['server'], 'server_port': node['port'], 'routing_mark': MARK}
    if node['type'] == 'socks5':
        result.update(type='socks', version='5', username=node['username'], password=node['password'])
    else:
        result.update(type='hysteria2', password=node['password'],
                      tls={'enabled': True, 'server_name': node['sni'], 'insecure': node['insecure']})
        if node['obfs']:
            result['obfs'] = {'type': node['obfs'], 'password': node['obfs_password']}
    return result


def core_config(state):
    nodes = {n['id']: n for s in state['subscriptions'] for n in s.get('nodes', [])}
    subscriptions = {s['id']: s for s in state['subscriptions']}
    rules = []
    dns_rules = []
    dns_servers = [{'type': 'local', 'tag': 'local-dns'},
                   {'type': 'udp', 'tag': 'direct-dns', 'server': os.environ.get('LINE_DIRECT_DNS', '223.5.5.5'), 'routing_mark': MARK},
                   {'type': 'fakeip', 'tag': 'fakeip', 'inet4_range': '198.19.0.0/16', 'inet6_range': 'fc00:66::/48'}]
    for node in nodes.values():
        dns_servers.append({'type': 'https', 'tag': 'dns-' + node['id'], 'server': 'cloudflare-dns.com',
                            'detour': node['id'], 'tls': {'enabled': True, 'server_name': 'cloudflare-dns.com'}})
    for a in state['accounts']:
        if a['enabled'] and a.get('node_id') in nodes:
            rules.append({'inbound': ['pptp'], 'source_ip_cidr': [a['ip'] + '/32'], 'port': 53, 'action': 'hijack-dns'})
            for direct in subscriptions.get(a['subscription_id'], {}).get('direct_rules', []):
                scope = {'inbound': ['pptp'], 'source_ip_cidr': [a['ip'] + '/32']}
                match = direct['match']
                if 'ip_cidr' in match and not direct.get('no_resolve', False):
                    rules.append({**scope, 'action': 'resolve', 'server': 'direct-dns'})
                rules.append({'type': 'logical', 'mode': 'and', 'rules': [scope, match] if match else [scope],
                              'action': 'route', 'outbound': 'subscription-direct'})
            rules.append({'inbound': ['pptp'], 'source_ip_cidr': [a['ip'] + '/32'], 'action': 'route', 'outbound': a['node_id']})
            dns_rules.append({'source_ip_cidr': [a['ip'] + '/32'], 'query_type': ['A', 'AAAA'], 'action': 'route', 'server': 'fakeip'})
            for direct in subscriptions.get(a['subscription_id'], {}).get('direct_rules', []):
                match = direct['match']
                if not match or set(match) <= {'domain', 'domain_suffix', 'domain_keyword'}:
                    dns_rules.append({**match, 'source_ip_cidr': [a['ip'] + '/32'], 'action': 'route', 'server': 'direct-dns'})
            dns_rules.append({'source_ip_cidr': [a['ip'] + '/32'], 'action': 'route', 'server': 'dns-' + a['node_id']})
    rules.append({'action': 'reject'})
    return {'log': {'level': 'warn'},
            'dns': {'servers': dns_servers, 'rules': dns_rules, 'final': 'local-dns'},
            'experimental': {'cache_file': {'enabled': True, 'path': str(DATA / 'dns-cache.db'), 'store_fakeip': True}},
            'inbounds': [{'tag': 'pptp', 'type': 'tproxy', 'listen': '0.0.0.0', 'listen_port': 16600}],
            'outbounds': [{'type': 'direct', 'tag': 'subscription-direct', 'routing_mark': MARK, 'domain_resolver': 'direct-dns'}] + [outbound(n) for n in nodes.values()],
            'route': {'default_mark': MARK, 'default_domain_resolver': 'local-dns', 'rules': rules}}


def accel_config():
    return f'''[modules]
log_file
pptp
auth_mschap_v2
auth_chap_md5
auth_pap
chap-secrets

[core]
log-error={DATA}/accel-error.log
thread-count=2

[common]
single-session=replace

[client-ip-range]
{'.'.join(LAN.split('.')[:3])}.0/24

[ppp]
verbose=0
min-mtu=1280
mtu=1400
mru=1400
ipv4=require
ipv6=deny
mppe=allow
lcp-echo-interval=20
lcp-echo-failure=3

[pptp]
bind={LAN}
verbose=0
ifname=lc%d

[chap-secrets]
chap-secrets={DATA}/chap-secrets
gw-ip-address=10.66.0.1

[dns]
dns1=1.1.1.1
dns2=8.8.8.8

[log]
log-file={DATA}/accel.log
level=3
copy=0

[cli]
tcp=127.0.0.1:16611
'''


def secrets(state):
    return '# Managed by line-converter\n' + ''.join(
        f'{a["username"]}\t*\t{PASSWORD}\t{a["ip"]}\n'
        for a in state['accounts'] if a['enabled'] and a.get('node_id'))


def apply(state):
    DATA.mkdir(parents=True, exist_ok=True)
    config = json.dumps(core_config(state), ensure_ascii=False, indent=2)
    candidate = DATA / 'core.candidate.json'
    atomic(candidate, config)
    if LIVE:
        run([CORE, 'check', '-c', str(candidate)])
    paths = {DATA / 'core.json': config, DATA / 'chap-secrets': secrets(state), DATA / 'accel.conf': accel_config()}
    previous = {p: p.read_text('utf-8') if p.exists() else None for p in paths}
    try:
        for p, value in paths.items():
            atomic(p, value)
        if LIVE:
            run(['systemctl', 'restart', 'line-converter-core'])
            time.sleep(.4)
            run(['systemctl', 'is-active', '--quiet', 'line-converter-core'])
            # chap-secrets is reread for new authentications. No global PPTP restart.
            run(['systemctl', 'start', 'line-converter-pptp'])
            run(['systemctl', 'is-active', '--quiet', 'line-converter-pptp'])
    except Exception:
        for p, value in previous.items():
            if value is not None:
                atomic(p, value)
        if LIVE and previous[DATA / 'core.json'] is not None:
            run(['systemctl', 'restart', 'line-converter-core'], check=False)
        raise


def service_status():
    if not LIVE:
        return {'mode': '本地预览', 'core': '未部署', 'pptp': '未部署'}
    return {'mode': 'Armbian', **{key: run(['systemctl', 'is-active', unit], check=False).stdout.strip()
            for key, unit in [('core', 'line-converter-core'), ('pptp', 'line-converter-pptp')]}}


def sessions():
    if not LIVE:
        return []
    import socket
    try:
        with socket.create_connection(('127.0.0.1', 16611), 2) as s:
            s.settimeout(2)
            s.sendall(b'show sessions\nexit\n')
            chunks = []
            while True:
                part = s.recv(65536)
                if not part:
                    break
                chunks.append(part)
        text = b''.join(chunks).decode(errors='replace')
        rows = []
        for line in text.splitlines():
            cells = [x.strip() for x in line.split('|')]
            if len(cells) > 2 and cells[0].startswith('lc'):
                interface = cells[0]
                row = {'interface': interface, 'username': cells[1], 'detail': line.strip()}
                for name in ('rx_bytes', 'tx_bytes'):
                    p = Path('/sys/class/net') / interface / 'statistics' / name
                    row[name] = int(p.read_text()) if p.exists() else 0
                rows.append(row)
        return rows
    except (OSError, ValueError):
        return []


def disconnect(username):
    if not LIVE:
        return
    import socket
    with socket.create_connection(('127.0.0.1', 16611), 2) as s:
        s.sendall(('terminate username ' + username + '\nexit\n').encode())


def test_node(node):
    """Real HTTPS request through isolated SOCKS ingress, not a TCP-port ping."""
    import socket
    import tempfile
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
    cfg = {'log': {'disabled': True}, 'inbounds': [{'listen': '127.0.0.1', 'listen_port': port,
           'type': 'socks'}], 'outbounds': [outbound(node)],
           'dns': {'servers': [{'type': 'local', 'tag': 'local-dns'}]},
           'route': {'default_mark': MARK, 'default_domain_resolver': 'local-dns'}}
    with tempfile.TemporaryDirectory(dir=DATA) as d:
        p = Path(d) / 'test.json'
        atomic(p, json.dumps(cfg))
        proc = subprocess.Popen([CORE, 'run', '-c', str(p)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        start = time.monotonic()
        try:
            time.sleep(.3)
            result = run(['curl', '--silent', '--show-error', '--fail', '--max-time', '15',
                          '--noproxy', '', '--proxy', f'socks5h://127.0.0.1:{port}', 'https://api64.ipify.org'], timeout=18, check=False)
            if result.returncode or proc.poll() is not None:
                return {'ok': False, 'message': '节点连接或 HTTPS 请求失败'}
            import ipaddress
            address = str(ipaddress.ip_address(result.stdout.strip()))
            return {'ok': True, 'exit_ip': address, 'elapsed_ms': round((time.monotonic() - start) * 1000)}
        finally:
            proc.terminate()
            try:
                proc.wait(3)
            except subprocess.TimeoutExpired:
                proc.kill(); proc.wait()
