"""Translate explicitly supported subscription DIRECT rules into scoped matchers."""
import ipaddress
import json


def parse_direct_rules(document):
    if not isinstance(document, dict):
        return [], []
    result, warnings = [], []
    mapping = {'DOMAIN': 'domain', 'DOMAIN-SUFFIX': 'domain_suffix',
               'DOMAIN-KEYWORD': 'domain_keyword', 'IP-CIDR': 'ip_cidr', 'IP-CIDR6': 'ip_cidr'}
    for index, raw in enumerate(document.get('rules', [])):
        if not isinstance(raw, str):
            continue
        parts = [p.strip() for p in raw.split(',')]
        if 'DIRECT' not in parts:
            continue
        try:
            if parts == ['MATCH', 'DIRECT']:
                match = {}
            else:
                if len(parts) < 3 or parts[2] != 'DIRECT' or parts[0] not in mapping:
                    raise ValueError()
                field = mapping[parts[0]]
                value = parts[1]
                if not value or any(p != 'no-resolve' for p in parts[3:]):
                    raise ValueError()
                if field == 'ip_cidr':
                    value = str(ipaddress.ip_network(value, strict=False))
                else:
                    value = value.lower().rstrip('.')
                    if any(c.isspace() for c in value):
                        raise ValueError()
                match = {field: [value]}
            result.append({'match': match, 'no_resolve': 'no-resolve' in parts, 'label': raw})
        except (ValueError, TypeError):
            warnings.append({'index': index + 1, 'reason': '直连规则暂不支持或格式无效：' + parts[0][:40]})
    direct_tags = {n.get('tag') for n in document.get('outbounds', []) if isinstance(n, dict) and n.get('type') == 'direct'}
    for index, raw in enumerate(document.get('route', {}).get('rules', [])):
        if not isinstance(raw, dict) or raw.get('outbound') not in direct_tags:
            continue
        fields = {'domain', 'domain_suffix', 'domain_keyword', 'ip_cidr', 'ip_is_private', 'port', 'network'}
        if set(raw) - fields - {'outbound', 'action'} or raw.get('action', 'route') != 'route':
            warnings.append({'index': index + 1, 'reason': '复杂 sing-box 直连规则暂不支持'})
            continue
        match = {k: v for k, v in raw.items() if k in fields}
        result.append({'match': match, 'no_resolve': True, 'label': json.dumps(match, ensure_ascii=False)})
    if len(result) > 5000:
        raise ValueError('直连规则超过 5000 条限制')
    return result, warnings
