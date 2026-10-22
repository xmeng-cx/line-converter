"""Stable account allocation; subscription ordering never controls account identity."""
import copy
import ipaddress
import json
import sqlite3
from contextlib import closing

PASSWORD = '123456'


def initial_state():
    return {'subscriptions': [], 'accounts': [], 'next_account': 1, 'events': [], 'revision': 0}


def reconcile(state, sub_id, nodes):
    state = copy.deepcopy(state)
    sub = next(s for s in state['subscriptions'] if s['id'] == sub_id)
    old_nodes = {n['id']: n for n in sub.get('nodes', [])}
    unmatched = set(old_nodes)
    for node in nodes:
        match = next((key for key in sorted(unmatched) if old_nodes[key]['key'] == node['key']), None)
        if match is None:
            candidates = [key for key in unmatched if old_nodes[key]['name'] == node['name'] and old_nodes[key]['type'] == node['type']]
            if len(candidates) == 1:
                match = candidates[0]
        if match:
            unmatched.remove(match)
            node['id'] = match
        else:
            import uuid
            node['id'] = uuid.uuid4().hex
    sub['nodes'] = nodes
    valid = {n['id'] for s in state['subscriptions'] for n in s.get('nodes', [])}
    owned = [a for a in state['accounts'] if a['subscription_id'] == sub_id]
    for account in state['accounts']:
        if account.get('node_id') not in valid:
            account['node_id'] = None
    # Manual selections remain intact. Default slots track every subscription entry.
    used_slots = {a.get('slot_id') for a in owned}
    current_ids = {n['id'] for n in nodes}
    available = [a for a in owned if a.get('slot_id') not in current_ids]
    added = 0
    for node in nodes:
        if node['id'] in used_slots:
            for a in owned:
                if a.get('slot_id') == node['id'] and not a.get('manual'):
                    a['node_id'] = node['id']
            continue
        if available:
            account = available.pop(0)
        else:
            number = state['next_account']
            if number > 65000:
                raise ValueError('账号地址池已满')
            account = {'id': number, 'username': 'vpn' + str(number),
                       'ip': str(ipaddress.IPv4Address('10.66.0.1') + number),
                       'subscription_id': sub_id, 'enabled': True, 'manual': False}
            state['next_account'] += 1
            state['accounts'].append(account)
            owned.append(account)
            added += 1
        account['slot_id'] = node['id']
        if not account.get('manual') or not account.get('node_id'):
            account['node_id'] = node['id']
            account['manual'] = False
    for a in owned:
        if a.get('slot_id') not in current_ids:
            a['slot_id'] = None
    return state, added


def delete_subscription(state, sub_id):
    state = copy.deepcopy(state)
    sub = next((s for s in state['subscriptions'] if s['id'] == sub_id), None)
    if sub is None:
        raise ValueError('订阅不存在或已经删除')
    removed = {n['id'] for n in sub.get('nodes', [])}
    state['subscriptions'] = [s for s in state['subscriptions'] if s['id'] != sub_id]
    for account in state['accounts']:
        if account.get('subscription_id') == sub_id:
            account['subscription_id'] = None
            account['slot_id'] = None
        if account.get('node_id') in removed:
            account['node_id'] = None
            account['manual'] = False
    return state, sub['name']


class Store:
    def __init__(self, path):
        self.path = path
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)')

    def read(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            row = db.execute('SELECT data FROM state WHERE id=1').fetchone()
        return json.loads(row[0]) if row else initial_state()

    def write(self, state):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('INSERT OR REPLACE INTO state VALUES (1, ?)', (json.dumps(state, ensure_ascii=False),))
