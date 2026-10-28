import base64
import copy
import unittest
import sqlite3
import tempfile
from unittest.mock import patch

from model import initial_state, reconcile
from model import Store, delete_subscription
from runtime import core_config, secrets
from subscriptions import parse_subscription


def nodes(text):
    return parse_subscription(text)[0]


class ProjectTests(unittest.TestCase):
    def test_dhcp_address_does_not_pin_pptp_listener(self):
        import runtime
        with patch.dict('os.environ', {}, clear=True), patch.object(
            runtime.subprocess, 'check_output', return_value='1.1.1.1 via 192.168.1.1 dev enp2s0 src 192.168.1.9\n'
        ):
            self.assertEqual(runtime.lan_ip(), '192.168.1.9')
        self.assertIn('bind=0.0.0.0', runtime.accel_config())

    def test_delete_subscription_removes_owned_accounts_and_reuses_numbers(self):
        state = initial_state()
        state['subscriptions'] = [{'id': 'a', 'name': 'A', 'nodes': []}, {'id': 'b', 'name': 'B', 'nodes': []}]
        state, _ = reconcile(state, 'a', nodes('hy2://p@a.example#A'))
        state, _ = reconcile(state, 'b', nodes('hy2://p@b.example#B'))
        original = copy.deepcopy(state)
        result, name = delete_subscription(state, 'a')
        self.assertEqual(state, original)
        self.assertEqual(name, 'A')
        self.assertEqual(result['next_account'], 1)
        self.assertEqual(result['accounts'], [state['accounts'][1]])
        self.assertNotIn('vpn1', secrets(result))
        self.assertIn('vpn2', secrets(result))
        result, _ = delete_subscription(result, 'b')
        self.assertEqual(core_config(result)['route']['rules'], [{'action': 'reject'}])
        self.assertEqual(result['accounts'], [])
        self.assertEqual(result['next_account'], 1)
        result['subscriptions'].append({'id': 'c', 'name': 'C', 'nodes': []})
        result, _ = reconcile(result, 'c', nodes('hy2://p@c.example#C'))
        self.assertEqual(result['accounts'][0]['username'], 'vpn1')
        self.assertEqual(len(result['accounts']), 1)
        with self.assertRaises(ValueError):
            delete_subscription(result, 'b')

    def test_database_connections_closed_without_garbage_collection(self):
        connections = []
        connect = sqlite3.connect

        class TrackedConnection(sqlite3.Connection):
            closed = False

            def close(self):
                self.closed = True
                super().close()

        def tracked(*args, **kwargs):
            connection = connect(*args, factory=TrackedConnection, **kwargs)
            connections.append(connection)
            return connection

        with tempfile.TemporaryDirectory() as folder:
            with patch('model.sqlite3.connect', tracked):
                store = Store(folder + '/test.db')
                for _ in range(100):
                    store.write(initial_state())
                    self.assertEqual(store.read()['accounts'], [])
                self.assertEqual(len(connections), 201)
                self.assertTrue(all(c.closed for c in connections))

    def test_console_restart_keeps_healthy_core(self):
        import runtime
        from pathlib import Path
        from types import SimpleNamespace
        import json
        state = initial_state()
        with tempfile.TemporaryDirectory() as folder, patch.object(runtime, 'DATA', Path(folder)), patch.object(runtime, 'LIVE', True):
            Path(folder, 'core.json').write_text(json.dumps(runtime.core_config(state)), encoding='utf-8')
            Path(folder, 'chap-secrets').write_text(runtime.secrets(state), encoding='utf-8')
            Path(folder, 'accel.conf').write_text(runtime.accel_config(), encoding='utf-8')
            with patch.object(runtime, 'run', return_value=SimpleNamespace(returncode=0)), patch.object(runtime, 'apply') as apply:
                runtime.ensure_started(state)
                apply.assert_not_called()
                Path(folder, 'chap-secrets').write_text('stale', encoding='utf-8')
                runtime.ensure_started(state)
                apply.assert_called_once_with(state)

    def setUp(self):
        self.state = initial_state()
        self.state['subscriptions'] = [{'id': 'sub', 'nodes': []}]

    def sync(self, links):
        self.state, added = reconcile(self.state, 'sub', nodes(links))
        return added

    def test_ipv6_base64_credentials(self):
        link = 'hy2://p%40ss@[2001:db8::1]:443?sni=a.example&insecure=1&obfs=salamander&obfs-password=abc#香港'
        n = nodes(base64.b64encode(link.encode()).decode())[0]
        self.assertEqual(n['server'], '2001:db8::1')
        self.assertEqual(n['password'], 'p@ss')
        self.assertTrue(n['insecure'])

    def test_reorder_change_add_remove(self):
        self.sync('hy2://a@a.example#A\nhy2://b@b.example#B')
        old = copy.deepcopy(self.state['accounts'])
        self.assertEqual(self.sync('hy2://new@b.example#B\nhy2://new@a.example#A'), 0)
        self.assertEqual(self.state['accounts'], old)
        self.assertEqual(self.sync('hy2://new@c.example#C\nhy2://new@d.example#D'), 0)
        self.assertEqual([a['username'] for a in self.state['accounts']], ['vpn1', 'vpn2'])
        self.sync('hy2://new@c.example#C')
        self.assertEqual(len(self.state['accounts']), 2)
        self.assertEqual(sum(a['node_id'] is None for a in self.state['accounts']), 1)
        self.assertEqual(self.sync('hy2://new@c.example#C\nhy2://e@e.example#E\nhy2://f@f.example#F'), 1)
        self.assertEqual(self.state['next_account'], 4)

    def test_endpoint_change_matches_name(self):
        self.sync('hy2://a@a.example#A')
        old = self.state['accounts'][0]['node_id']
        self.sync('hy2://b@b.example#A')
        self.assertEqual(old, self.state['accounts'][0]['node_id'])

    def test_manual_binding_survives(self):
        self.sync('hy2://a@a.example#A\nhy2://b@b.example#B')
        self.state['accounts'][0].update(manual=True, node_id=self.state['accounts'][1]['node_id'])
        previous = copy.deepcopy(self.state['accounts'])
        self.sync('hy2://b@b.example#B\nhy2://a@a.example#A')
        self.assertEqual(previous, self.state['accounts'])

    def test_fail_closed_and_secrets(self):
        self.sync('socks5://user:pass@localhost:1080#S')
        self.assertIn('vpn1\t*\t123456', secrets(self.state))
        self.state['accounts'][0]['enabled'] = False
        self.assertNotIn('vpn1', secrets(self.state))
        config = core_config(self.state)
        self.assertEqual(config['route']['rules'], [{'action': 'reject'}])

    def test_formats_and_bad_input(self):
        for text in ['{"outbounds":[{"type":"hysteria2","server":"a.example","server_port":443,"password":"p"}]}',
                     'proxies:\n - {name: A, type: socks5, server: a.example, port: 1080}']:
            self.assertEqual(len(nodes(text)), 1)
        for text in ['<html>Error</html>', 'proxies: []', 'vmess://abc', '!!python/object:os.system {}']:
            with self.assertRaises(ValueError):
                nodes(text)

    def test_subscription_direct_rules(self):
        text = '''proxies:
 - {name: A, type: socks5, server: a.example, port: 1080}
rules:
 - DOMAIN,direct.example,DIRECT
 - IP-CIDR,192.168.0.0/16,DIRECT,no-resolve
 - IP-CIDR6,fc00::/7,DIRECT,no-resolve
 - GEOIP,CN,DIRECT
 - MATCH,Proxy
'''
        ns, errors, direct, warnings = parse_subscription(text, include_rules=True)
        self.assertEqual(len(direct), 3)
        self.assertEqual(len(warnings), 1)
        self.assertTrue(direct[1]['no_resolve'])
        self.state, _ = reconcile(self.state, 'sub', ns)
        self.state['subscriptions'][0]['direct_rules'] = direct
        config = core_config(self.state)
        rules = config['route']['rules']
        self.assertEqual(rules[0]['action'], 'hijack-dns')
        self.assertEqual(rules[1]['outbound'], 'subscription-direct')
        self.assertEqual(rules[1]['rules'][0]['source_ip_cidr'], ['10.66.0.2/32'])
        self.assertEqual(rules[4]['outbound'], ns[0]['id'])
        self.state['accounts'][0]['enabled'] = False
        self.assertEqual(core_config(self.state)['route']['rules'], [{'action': 'reject'}])

    def test_rule_refresh_removes_old_and_accounts_stay(self):
        self.sync('hy2://a@a.example#A')
        old = copy.deepcopy(self.state['accounts'])
        self.state['subscriptions'][0]['direct_rules'] = [{'match': {'domain': ['old.example']}, 'no_resolve': True}]
        self.sync('hy2://a@a.example#A')
        self.state['subscriptions'][0]['direct_rules'] = []
        self.assertEqual(self.state['accounts'], old)
        self.assertNotIn('old.example', str(core_config(self.state)))


if __name__ == '__main__':
    unittest.main()
