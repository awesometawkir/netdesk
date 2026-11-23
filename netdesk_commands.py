"""Offline command discovery; never probes a device or rewrites commands on submit."""
from difflib import get_close_matches
from netdesk_presets import PRESETS, SCENARIOS

CATEGORIES = {
    'Configuration': ['config', 'config_review'],
    'Interfaces & switching': ['interfaces', 'neighbors', 'vlans', 'port_health', 'ip_interfaces', 'vlan_ports', 'mac_table', 'spanning_tree', 'discovery'],
    'Routing': ['routing', 'bgp', 'ospf', 'arp_table', 'bgp_peers', 'bgp_routes', 'ospf_peers', 'ospf_interfaces'],
    'System & diagnostics': ['quick', 'full', 'system', 'logs', 'resources', 'inventory', 'event_logs', 'traps', 'time_sync'],
    'Security & VPN': ['vpn', 'sessions'],
}


def category(preset):
    return next((name for name, keys in CATEGORIES.items() if preset in keys), 'Other')


def command_catalog(platform):
    result = {}
    for key, commands in PRESETS[platform].items():
        for command in commands:
            result.setdefault(command, (category(key), SCENARIOS[key]))
    return result


def suggestions(text, platform='huawei'):
    commands = list(command_catalog(platform))
    words = text.lower().strip().split()
    matches = [c for c in commands if len(words) <= len(c.split()) and
               all(full.startswith(part) for part, full in zip(words, c.lower().split()))]
    if matches:
        return matches
    return get_close_matches(text.lower().strip(), commands, n=8, cutoff=.55)
