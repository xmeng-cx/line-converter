#!/usr/bin/env python3
"""Idempotent private chains. Never flush unrelated firewall or routing rules."""
import subprocess
import sys


def cmd(*args, check=True):
    return subprocess.run(args, check=check, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    for module in ('pptp', 'nft_tproxy', 'nft_ct'):
        cmd('modprobe', module)
    # Marked proxy sockets must bypass existing Mihomo TUN policy routing.
    for family in ('-4', '-6'):
        existing = subprocess.check_output(['ip', family, 'rule', 'show'], text=True)
        for priority, args in [(166, ['fwmark', '16602', 'lookup', 'main'])]:
            if f'{priority}:' not in existing:
                cmd('ip', family, 'rule', 'add', 'priority', str(priority), *args)
    existing = subprocess.check_output(['ip', '-4', 'rule', 'show'], text=True)
    if '167:' not in existing:
        cmd('ip', 'rule', 'add', 'priority', '167', 'fwmark', '16601', 'lookup', '166')
    if '168:' not in existing:
        cmd('ip', 'rule', 'add', 'priority', '168', 'to', '10.66.0.0/16', 'lookup', 'main')
    cmd('ip', 'route', 'replace', 'local', '0.0.0.0/0', 'dev', 'lo', 'table', '166')
    # Migrate this project's old iptables rules only; never touch other chains.
    import shlex
    for table, chain in [('mangle', 'PREROUTING'), ('filter', 'INPUT'), ('filter', 'FORWARD')]:
        result = subprocess.run(['iptables', '-w', '-t', table, '-S', chain], capture_output=True, text=True)
        for line in result.stdout.splitlines():
            if 'LINE_CONVERTER' in line or '-i lc+' in line or '--dport 16600 ' in line:
                args = shlex.split(line)
                if args and args[0] == '-A':
                    cmd('iptables', '-w', '-t', table, '-D', *args[1:], check=False)
    cmd('iptables', '-w', '-t', 'mangle', '-F', 'LINE_CONVERTER', check=False)
    cmd('iptables', '-w', '-t', 'mangle', '-X', 'LINE_CONVERTER', check=False)
    # Exclude our PPP traffic from conntrack NAT: other TUN tools may redirect
    # every TCP connection after TPROXY has already selected its local socket.
    # Untracked packets do not enter their NAT rules; no foreign table is edited.
    rules = r"""
add table inet line_converter
flush table inet line_converter
add chain inet line_converter preraw { type filter hook prerouting priority -310; policy accept; }
add rule inet line_converter preraw iifname "lc*" ip saddr 10.66.0.0/16 notrack
add chain inet line_converter outraw { type filter hook output priority -310; policy accept; }
add rule inet line_converter outraw ip daddr 10.66.0.0/16 notrack
add chain inet line_converter proxy { type filter hook prerouting priority -150; policy accept; }
add rule inet line_converter proxy iifname "lc*" ip saddr 10.66.0.0/16 meta l4proto { tcp, udp } tproxy ip to :16600 meta mark set 16601 accept
add chain inet line_converter input { type filter hook input priority -10; policy accept; }
add rule inet line_converter input iifname "lc*" meta mark 16601 accept
add rule inet line_converter input iifname "lc*" drop
add rule inet line_converter input meta l4proto { tcp, udp } th dport 16600 meta mark != 16601 drop
add chain inet line_converter forward { type filter hook forward priority -10; policy accept; }
add rule inet line_converter forward iifname "lc*" drop
"""
    subprocess.run(['nft', '-f', '-'], input=rules, text=True, check=True)


if __name__ == '__main__':
    main()
