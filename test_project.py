import base64
import copy
import unittest

from model import initial_state, reconcile
from runtime import core_config, secrets
from subscriptions import parse_subscription


def nodes(text):
    return parse_subscription(text)[0]


class ProjectTests(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()
