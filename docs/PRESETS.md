# Platform and scenario presets

NetDesk includes command starting points for 15 platform families. The GUI filters its scenario choices by platform; a mixed inventory gets only presets defined for every platform in that inventory. `custom` is always available.

These presets have been checked against the Netmiko driver catalog, available command templates, and vendor references where applicable. They have **not been validated on physical devices**. Feature availability, firmware, licensing, privilege level and the current CLI context can change which commands succeed. Inspect the preview before starting.

- Use an account that already has the privileges required for the commands. NetDesk does not prompt for a separate enable password or enter configuration mode for a preset.
- `full` is a broad collection, not a guarantee of every possible log. Huawei, IOS and Junos use potentially long support commands; other platforms use a bounded command list rather than generating support files on the device.
- `neighbors` may contain ARP information where a common discovery command is not included.
- Junos VLAN commands are for switching-capable platforms; VPN/session commands require the corresponding SRX/security features.
- RouterOS presets target **v7**. `/export` uses its default handling of sensitive fields; no `show-sensitive` option is added. Do not assume v7 routing syntax works on v6.
- PAN-OS routing commands target the traditional routing CLI; the advanced routing engine may require custom commands.
- Aruba, ProCurve, Comware and Cisco routing/switching scenarios require the relevant model features. A platform-level preset cannot determine the installed hardware capabilities.
- The EdgeOS entry uses Netmiko's **ubiquiti_edgerouter** driver. It is not the similarly named EdgeSwitch driver.
- Unknown or rejected commands are reported as failures where their CLI error format is recognized. Always inspect output for platform-specific errors.

Inspect from the CLI:

```bash
netdesk presets
netdesk presets --platform huawei
netdesk collect --targets '192.0.2.10-12' --platform cisco_ios --preset bgp --dry-run
```

## Availability

| Platform | Inventory / CLI value | Scenarios |
| --- | --- | --- |
| Huawei VRP | `huawei` | quick, config, full, interfaces, routing, neighbors, vlans, bgp, ospf, system, logs |
| Cisco IOS / IOS XE | `cisco_ios` | quick, config, full, interfaces, routing, neighbors, vlans, bgp, ospf, system, logs |
| Cisco NX-OS | `cisco_nxos` | quick, config, full, interfaces, routing, neighbors, vlans, bgp, ospf, system, logs |
| Cisco IOS XR | `cisco_xr` | quick, config, full, interfaces, routing, neighbors, bgp, ospf, system, logs |
| Arista EOS | `arista_eos` | quick, config, full, interfaces, routing, neighbors, vlans, bgp, ospf, system, logs |
| Juniper Junos | `juniper_junos` | quick, config, full, interfaces, routing, neighbors, vlans, bgp, ospf, system, logs, vpn, sessions |
| Fortinet FortiOS | `fortinet` | quick, config, full, interfaces, routing, neighbors, bgp, ospf, system, vpn, sessions |
| Palo Alto PAN-OS | `paloalto_panos` | quick, config, full, interfaces, routing, neighbors, system, vpn, sessions |
| Aruba AOS-CX | `aruba_aoscx` | quick, config, full, interfaces, routing, neighbors, vlans, bgp, ospf, system, logs |
| ArubaOS-Switch / ProCurve | `hp_procurve` | quick, config, full, interfaces, routing, neighbors, vlans, system, logs |
| HPE / H3C Comware | `hp_comware` | quick, config, full, interfaces, routing, neighbors, vlans, bgp, ospf, system, logs |
| Dell SmartFabric OS10 | `dell_os10` | quick, config, full, interfaces, routing, neighbors, vlans, bgp, ospf, system, logs |
| MikroTik RouterOS v7 | `mikrotik_routeros` | quick, config, full, interfaces, routing, neighbors, vlans, bgp, ospf, system, logs, vpn |
| VyOS | `vyos` | quick, config, full, interfaces, routing, neighbors, bgp, ospf, system |
| Ubiquiti EdgeOS | `ubiquiti_edgerouter` | quick, config, full, interfaces, routing, neighbors, bgp, ospf, system |

## Scenario meanings

- `quick`: Quick health snapshot.
- `config`: Configuration backup.
- `full`: Broad diagnostic collection.
- `interfaces`: Interface status and counters.
- `routing`: Routing tables and ARP.
- `neighbors`: Connected neighbors / discovery.
- `vlans`: VLAN and switching overview.
- `bgp`: BGP session troubleshooting.
- `ospf`: OSPF adjacency troubleshooting.
- `system`: System health and inventory.
- `logs`: Recent device logs.
- `vpn`: VPN tunnel overview.
- `sessions`: Firewall session summary.

## Exact commands

The source of truth is [`netdesk_presets.py`](../netdesk_presets.py). This catalog lists the commands shipped in this release.

### Huawei VRP

**quick**

```text
display version
display interface brief
display ip routing-table
display lldp neighbor
```

**config**

```text
display current-configuration
```

**full**

```text
display current-configuration
display diagnostic-information
display lldp neighbor
display ip routing-table
```

**interfaces**

```text
display interface brief
display interface
```

**routing**

```text
display ip routing-table
display arp
```

**neighbors**

```text
display lldp neighbor
display arp
```

**vlans**

```text
display vlan
display mac-address
display stp brief
```

**bgp**

```text
display bgp peer
display bgp routing-table
```

**ospf**

```text
display ospf peer
display ospf interface
```

**system**

```text
display version
display device
display cpu-usage
display memory-usage
```

**logs**

```text
display logbuffer
display trapbuffer
```

### Cisco IOS / IOS XE

**quick**

```text
show version
show ip interface brief
show ip route
show cdp neighbors
```

**config**

```text
show running-config
```

**full**

```text
show running-config
show tech-support
```

**interfaces**

```text
show ip interface brief
show interfaces
```

**routing**

```text
show ip route
show ip arp
```

**neighbors**

```text
show cdp neighbors detail
show lldp neighbors detail
```

**vlans**

```text
show vlan brief
show interfaces trunk
show spanning-tree summary
```

**bgp**

```text
show ip bgp summary
show ip bgp neighbors
```

**ospf**

```text
show ip ospf neighbor
show ip ospf interface brief
```

**system**

```text
show version
show inventory
show processes cpu sorted
```

**logs**

```text
show logging
```

### Cisco NX-OS

**quick**

```text
show version
show interface status
show ip route
```

**config**

```text
show running-config
```

**full**

```text
show running-config
show version
show module
show interface status
show ip route
show logging last 100
```

**interfaces**

```text
show interface status
show interface counters errors
```

**routing**

```text
show ip route
show ip arp
show vrf
```

**neighbors**

```text
show cdp neighbors detail
show lldp neighbors detail
```

**vlans**

```text
show vlan brief
show interface trunk
show spanning-tree summary
```

**bgp**

```text
show bgp ipv4 unicast summary
show bgp ipv4 unicast neighbors
```

**ospf**

```text
show ip ospf neighbors
show ip ospf interface brief
```

**system**

```text
show version
show module
show system resources
```

**logs**

```text
show logging last 100
```

### Cisco IOS XR

**quick**

```text
show version
show interfaces brief
show route summary
```

**config**

```text
show running-config
```

**full**

```text
show running-config
show version
show platform
show interfaces brief
show route summary
show logging
```

**interfaces**

```text
show interfaces brief
show interfaces
```

**routing**

```text
show route
show arp
```

**neighbors**

```text
show cdp neighbors
show lldp neighbors
```

**bgp**

```text
show bgp ipv4 unicast summary
show bgp neighbors
```

**ospf**

```text
show ospf neighbor
show ospf interface brief
```

**system**

```text
show version
show platform
show inventory
```

**logs**

```text
show logging
```

### Arista EOS

**quick**

```text
show version
show interfaces status
show ip route summary
```

**config**

```text
show running-config
```

**full**

```text
show running-config
show version
show interfaces status
show ip route
show logging
```

**interfaces**

```text
show interfaces status
show interfaces counters errors
```

**routing**

```text
show ip route
show arp
```

**neighbors**

```text
show lldp neighbors detail
```

**vlans**

```text
show vlan
show interfaces trunk
show spanning-tree summary
```

**bgp**

```text
show ip bgp summary
show ip bgp neighbors
```

**ospf**

```text
show ip ospf neighbor
show ip ospf interface brief
```

**system**

```text
show version
show inventory
show environment all
```

**logs**

```text
show logging
```

### Juniper Junos

**quick**

```text
show version
show interfaces terse
show route summary
```

**config**

```text
show configuration | display set
```

**full**

```text
show configuration | display set
request support information
```

**interfaces**

```text
show interfaces terse
show interfaces extensive
```

**routing**

```text
show route
show arp no-resolve
```

**neighbors**

```text
show lldp neighbors
show arp no-resolve
```

**vlans**

```text
show vlans
show ethernet-switching table
```

**bgp**

```text
show bgp summary
show bgp neighbor
```

**ospf**

```text
show ospf neighbor
show ospf interface
```

**system**

```text
show version
show chassis hardware
show chassis alarms
show system alarms
```

**logs**

```text
show log messages | last 100
```

**vpn**

```text
show security ike security-associations
show security ipsec security-associations
```

**sessions**

```text
show security flow session summary
```

### Fortinet FortiOS

**quick**

```text
get system status
get router info routing-table all
```

**config**

```text
show full-configuration
```

**full**

```text
get system status
show full-configuration
get router info routing-table all
```

**interfaces**

```text
get system interface physical
```

**routing**

```text
get router info routing-table all
get system arp
```

**neighbors**

```text
get system arp
```

**bgp**

```text
get router info bgp summary
get router info bgp neighbors
```

**ospf**

```text
get router info ospf neighbor
get router info ospf interface
```

**system**

```text
get system status
get system performance status
```

**vpn**

```text
get vpn ipsec tunnel summary
diagnose vpn ike gateway list
```

**sessions**

```text
diagnose sys session stat
```

### Palo Alto PAN-OS

**quick**

```text
show system info
show interface all
show routing route
```

**config**

```text
show config running
```

**full**

```text
show system info
show config running
show interface all
show routing route
show session info
```

**interfaces**

```text
show interface all
show interface hardware
```

**routing**

```text
show routing route
show arp all
```

**neighbors**

```text
show arp all
```

**system**

```text
show system info
show system disk-space
```

**vpn**

```text
show vpn ike-sa
show vpn ipsec-sa
```

**sessions**

```text
show session info
```

### Aruba AOS-CX

**quick**

```text
show version
show interface brief
show ip route
```

**config**

```text
show running-config
```

**full**

```text
show running-config
show version
show interface brief
show ip route
show events
```

**interfaces**

```text
show interface brief
show interface
```

**routing**

```text
show ip route
show arp
```

**neighbors**

```text
show lldp neighbor-info
```

**vlans**

```text
show vlan
show mac-address-table
```

**bgp**

```text
show bgp all summary
```

**ospf**

```text
show ip ospf neighbors
show ip ospf interface
```

**system**

```text
show version
show system
show environment
```

**logs**

```text
show events
```

### ArubaOS-Switch / ProCurve

**quick**

```text
show version
show interfaces brief
show ip route
```

**config**

```text
show running-config
```

**full**

```text
show running-config
show system-information
show interfaces brief
show logging -r
```

**interfaces**

```text
show interfaces brief
show interfaces
```

**routing**

```text
show ip route
show arp
```

**neighbors**

```text
show lldp info remote-device
```

**vlans**

```text
show vlans
show trunks
show mac-address
```

**system**

```text
show version
show system-information
```

**logs**

```text
show logging -r
```

### HPE / H3C Comware

**quick**

```text
display version
display interface brief
display ip routing-table
```

**config**

```text
display current-configuration
```

**full**

```text
display current-configuration
display version
display device
display interface brief
display logbuffer
```

**interfaces**

```text
display interface brief
display interface
```

**routing**

```text
display ip routing-table
display arp
```

**neighbors**

```text
display lldp neighbor-information
```

**vlans**

```text
display vlan
display mac-address
display stp brief
```

**bgp**

```text
display bgp peer
```

**ospf**

```text
display ospf peer
display ospf interface
```

**system**

```text
display version
display device
display cpu-usage
display memory
```

**logs**

```text
display logbuffer
```

### Dell SmartFabric OS10

**quick**

```text
show version
show interface status
show ip route
```

**config**

```text
show running-configuration
```

**full**

```text
show running-configuration
show version
show interface status
show ip route
show logging
```

**interfaces**

```text
show interface status
show interface counters
```

**routing**

```text
show ip route
show ip arp
```

**neighbors**

```text
show lldp neighbors
```

**vlans**

```text
show vlan
show mac address-table
```

**bgp**

```text
show ip bgp summary
show ip bgp neighbors
```

**ospf**

```text
show ip ospf neighbor
```

**system**

```text
show version
show inventory
```

**logs**

```text
show logging
```

### MikroTik RouterOS v7

**quick**

```text
/system resource print
/interface print terse without-paging
/ip route print terse without-paging
```

**config**

```text
/export
```

**full**

```text
/export
/system resource print
/interface print terse without-paging
/ip route print terse without-paging
/log print without-paging
```

**interfaces**

```text
/interface print terse without-paging
/interface ethernet print detail without-paging
```

**routing**

```text
/ip route print terse without-paging
/ip arp print terse without-paging
```

**neighbors**

```text
/ip neighbor print detail without-paging
```

**vlans**

```text
/interface vlan print detail without-paging
/interface bridge vlan print detail without-paging
```

**bgp**

```text
/routing bgp session print detail without-paging
```

**ospf**

```text
/routing ospf neighbor print detail without-paging
```

**system**

```text
/system resource print
/system identity print
```

**logs**

```text
/log print without-paging
```

**vpn**

```text
/ip ipsec active-peers print without-paging
/ip ipsec installed-sa print without-paging
```

### VyOS

**quick**

```text
show version
show interfaces
show ip route
```

**config**

```text
show configuration commands
```

**full**

```text
show configuration commands
show version
show interfaces
show ip route
```

**interfaces**

```text
show interfaces
```

**routing**

```text
show ip route
show arp
```

**neighbors**

```text
show arp
```

**bgp**

```text
show bgp ipv4 summary
```

**ospf**

```text
show ip ospf neighbor
```

**system**

```text
show version
show system uptime
```

### Ubiquiti EdgeOS

**quick**

```text
show version
show interfaces
show ip route
```

**config**

```text
show configuration commands
```

**full**

```text
show configuration commands
show version
show interfaces
show ip route
```

**interfaces**

```text
show interfaces
```

**routing**

```text
show ip route
show arp
```

**neighbors**

```text
show arp
```

**bgp**

```text
show ip bgp summary
```

**ospf**

```text
show ip ospf neighbor
```

**system**

```text
show version
show system uptime
```

## Reference sources

The installed Netmiko driver implementations and NTC templates are useful cross-checks for platform identities and common operational commands:

- [Netmiko supported platforms](https://github.com/ktbyers/netmiko/blob/develop/PLATFORMS.md)
- [NTC command templates](https://github.com/networktocode/ntc-templates/tree/master/ntc_templates/templates)
- [Cisco IOS XR BGP command examples](https://www.cisco.com/c/en/us/td/docs/iosxr/cisco8000/bgp/72x/b-bgp-cg-8k-72x/implementing-bgp.html)
- [Junos operational verification examples](https://www.juniper.net/documentation/us/en/software/platform/acx7000-configuration-essentials/topics/topic-map/acx7000-enterprise-wan-edge-backbone-use-case.html)
- [Aruba AOS-CX command availability by platform](https://arubanetworking.hpe.com/techdocs/AOS-CX/10.15/HTML/rest_v10-0x/Content/Chp_AnyCLI/cmds-avail-plat.htm)
- [Aruba AOS-CX LLDP syntax](https://arubanetworking.hpe.com/techdocs/AOS-CX/10.17.1000/HTML/CLI_6300-6400/GUID-08A48FD5-C15B-4D6A-88E7-070D351941B1.html)
- [MikroTik configuration management](https://help.mikrotik.com/docs/spaces/ROS/pages/328155/Configuration+Management)
- [MikroTik v6-to-v7 routing changes](https://help.mikrotik.com/docs/spaces/ROS/pages/30474256/Moving+from+ROSv6+to+v7+with+examples)
- [PAN-OS networking CLI reference](https://docs.paloaltonetworks.com/ngfw/pan-os-cli-quick-start/cli-cheat-sheet-networking)
- [VyOS BGP operational commands](https://docs.vyos.io/en/rolling/configuration/protocols/bgp.html)
- [Dell OS10 routing verification](https://www.dell.com/en-us/infohub/articles/dell-emc-networking-smartfabric-services-deployment-with-vxrail-7-0-1)
