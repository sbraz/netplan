#!/usr/bin/python3
#
# Integration tests for ethernet devices and features common to all device
# types.
#
# These need to be run in a VM and do change the system
# configuration.
#
# Copyright (C) 2018-2021 Canonical, Ltd.
# Author: Mathieu Trudel-Lapierre <mathieu.trudel-lapierre@canonical.com>
# AUthor: Lukas Märdian <slyon@ubuntu.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 3.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import sys
import subprocess
import unittest

from base import IntegrationTestsBase, nm_uses_dnsmasq, resolved_in_use, test_backends


class _CommonTests():

    def test_eth_mtu(self):
        self.setup_eth(None)
        with open(self.config, 'w') as f:
            f.write('''network:
  renderer: %(r)s
  ethernets:
    enmtus:
      match: {name: %(e2c)s}
      mtu: 1492
      dhcp4: yes''' % {'r': self.backend, 'e2c': self.dev_e2_client})
        self.generate_and_settle([self.state_dhcp4(self.dev_e2_client)])
        self.assert_iface_up(self.dev_e2_client,
                             ['inet 192.168.6.[0-9]+/24', 'mtu 1492'])

    def test_eth_mac(self):
        self.setup_eth(None)
        self.addCleanup(subprocess.call, ['ip', 'link', 'set', self.dev_e2_client, 'address', self.dev_e2_client_mac])
        with open(self.config, 'w') as f:
            f.write('''network:
  renderer: %(r)s
  ethernets:
    enmac:
      match: {name: %(e2c)s}
      macaddress: 00:01:02:03:04:05
      dhcp4: yes''' % {'r': self.backend, 'e2c': self.dev_e2_client})
        self.generate_and_settle([self.state_dhcp4(self.dev_e2_client)])
        self.assert_iface_up(self.dev_e2_client,
                             ['inet 192.168.6.[0-9]+/24', 'ether 00:01:02:03:04:05'])

    # Supposed to fail if tested against NetworkManager < 1.14
    # Interface globbing was introduced as of NM 1.14+
    def test_eth_glob(self):
        self.setup_eth(None)
        with open(self.config, 'w') as f:
            f.write('''network:
  renderer: %(r)s
  ethernets:
    englob:
      match: {name: "eth?2"}
      addresses: ["172.16.42.99/18", "1234:FFFF::42/64"]
''' % {'r': self.backend})  # globbing match on "eth42", i.e. self.dev_e_client
        self.generate_and_settle([self.dev_e_client])
        self.assert_iface_up(self.dev_e_client, ['inet 172.16.42.99/18', 'inet6 1234:ffff::42/64'])

    def test_manual_addresses(self):
        self.setup_eth(None)
        with open(self.config, 'w') as f:
            f.write('''network:
  renderer: %(r)s
  ethernets:
    %(ec)s:
      addresses: ["172.16.42.99/18", "1234:FFFF::42/64"]
      dhcp4: yes
    %(e2c)s:
      addresses: ["172.16.1.2/24"]
      gateway4: "172.16.1.1"
      nameservers:
        addresses: [172.1.2.3]
        search: ["fakesuffix"]
''' % {'r': self.backend, 'ec': self.dev_e_client, 'e2c': self.dev_e2_client})
        self.generate_and_settle([self.state_dhcp4(self.dev_e_client), self.dev_e2_client])
        if self.backend == 'NetworkManager':
            self.nm_online_full(self.dev_e_client)
        self.assert_iface_up(self.dev_e_client,
                             ['inet 172.16.42.99/18',
                              'inet6 1234:ffff::42/64',
                              'inet 192.168.5.[0-9]+/24'])  # from DHCP
        self.assert_iface_up(self.dev_e2_client,
                             ['inet 172.16.1.2/24'])

        self.assertIn(b'default via 192.168.5.1',  # from DHCP
                      subprocess.check_output(['ip', 'route', 'show', 'dev', self.dev_e_client]))
        self.assertNotIn(b'default',
                         subprocess.check_output(['ip', '-6', 'route', 'show', 'dev', self.dev_e_client]))
        self.assertIn(b'default via 172.16.1.1',
                      subprocess.check_output(['ip', 'route', 'show', 'dev', self.dev_e2_client]))
        self.assertNotIn(b'default',
                         subprocess.check_output(['ip', '-6', 'route', 'show', 'dev', self.dev_e2_client]))

        # ensure that they do not get managed by NM for foreign backends
        expected_state = (self.backend == 'NetworkManager') and 'connected' or 'unmanaged'
        out = subprocess.check_output(['nmcli', 'dev'], universal_newlines=True)
        for i in [self.dev_e_client, self.dev_e2_client]:
            self.assertRegex(out, r'%s\s+(ethernet|bridge)\s+%s' % (i, expected_state))

        with open('/etc/resolv.conf') as f:
            resolv_conf = f.read()

        if self.backend == 'NetworkManager' and nm_uses_dnsmasq:
            sys.stdout.write('[NM with dnsmasq] ')
            sys.stdout.flush()
            self.assertRegex(resolv_conf, 'search.*fakesuffix')
            # not easy to peek dnsmasq's brain, so check its logging
            out = subprocess.check_output(['journalctl', '--quiet', '-tdnsmasq', '-ocat', '--since=-30s'],
                                          universal_newlines=True)
            self.assertIn('nameserver 172.1.2.3', out)
        elif resolved_in_use():
            sys.stdout.write('[resolved] ')
            sys.stdout.flush()
            out = subprocess.check_output(['resolvectl', 'status'], universal_newlines=True)
            self.assertIn('DNS Servers: 172.1.2.3', out)
            self.assertIn('fakesuffix', out)
        else:
            sys.stdout.write('[/etc/resolv.conf] ')
            sys.stdout.flush()
            self.assertRegex(resolv_conf, 'search.*fakesuffix')
            # /etc/resolve.conf often already has three nameserver entries
            if 'nameserver 172.1.2.3' not in resolv_conf:
                self.assertGreaterEqual(resolv_conf.count('nameserver'), 3)

        # change the addresses, make sure that "apply" does not leave leftovers
        with open(self.config, 'w') as f:
            f.write('''network:
  renderer: %(r)s
  ethernets:
    %(ec)s:
      addresses: ["172.16.5.3/20", "9876:BBBB::11/70"]
      gateway6: "9876:BBBB::1"
    %(e2c)s:
      addresses: ["172.16.7.2/30", "4321:AAAA::99/80"]
      dhcp4: yes
''' % {'r': self.backend, 'ec': self.dev_e_client, 'e2c': self.dev_e2_client})
        self.generate_and_settle([self.dev_e_client, self.state_dhcp4(self.dev_e2_client)])
        if self.backend == 'NetworkManager':
            self.nm_online_full(self.dev_e2_client)
        self.assert_iface_up(self.dev_e_client,
                             ['inet 172.16.5.3/20'],
                             ['inet 192.168.5',   # old DHCP
                              'inet 172.16.42',   # old static IPv4
                              'inet6 1234'])      # old static IPv6
        self.assert_iface_up(self.dev_e2_client,
                             ['inet 172.16.7.2/30',
                              'inet6 4321:aaaa::99/80',
                              'inet 192.168.6.[0-9]+/24'],  # from DHCP
                             ['inet 172.16.1'])   # old static IPv4

        self.assertNotIn(b'default',
                         subprocess.check_output(['ip', 'route', 'show', 'dev', self.dev_e_client]))
        self.assertIn(b'via 9876:bbbb::1',
                      subprocess.check_output(['ip', '-6', 'route', 'show', 'default']))
        self.assertIn(b'default via 192.168.6.1',  # from DHCP
                      subprocess.check_output(['ip', 'route', 'show', 'dev', self.dev_e2_client]))
        self.assertNotIn(b'default',
                         subprocess.check_output(['ip', '-6', 'route', 'show', 'dev', self.dev_e2_client]))

    def test_dhcp6(self):
        self.setup_eth('slaac')
        with open(self.config, 'w') as f:
            f.write('''network:
  version: 2
  renderer: %(r)s
  ethernets:
    %(ec)s:
      dhcp6: yes
      accept-ra: yes''' % {'r': self.backend, 'ec': self.dev_e_client})
        self.generate_and_settle([self.state_dhcp6(self.dev_e_client)])
        self.assert_iface_up(self.dev_e_client, ['inet6 2600:'], ['inet 192.168'])

    def test_ip6_token(self):
        self.setup_eth('slaac')
        with open(self.config, 'w') as f:
            f.write('''network:
  version: 2
  renderer: %(r)s
  ethernets:
    %(ec)s:
      dhcp6: yes
      accept-ra: yes
      ipv6-address-token: ::42''' % {'r': self.backend, 'ec': self.dev_e_client})
        self.generate_and_settle([self.state_dhcp6(self.dev_e_client)])
        self.assert_iface_up(self.dev_e_client, ['inet6 2600::42/64'])

    def test_link_local_all(self):
        self.setup_eth(None)
        with open(self.config, 'w') as f:
            f.write('''network:
  renderer: %(r)s
  ethernets:
    %(ec)s:
      link-local: [ ipv4, ipv6 ]''' % {'r': self.backend, 'ec': self.dev_e_client})
        self.generate_and_settle([self.dev_e_client])
        # Verify IPv4 and IPv6 link local addresses are there
        self.assert_iface(self.dev_e_client, ['inet6 fe80:', 'inet 169.254.'])

    def test_rename_interfaces(self):
        self.setup_eth(None)
        with open(self.config, 'w') as f:
            f.write('''network:
  renderer: %(r)s
  ethernets:
    idx:
      match:
        name: %(ec)s
      set-name: iface1
      addresses: [10.10.10.11/24]
    idy:
      match:
        macaddress: %(e2c_mac)s
      set-name: iface2
      addresses: [10.10.10.22/24]
''' % {'r': self.backend, 'ec': self.dev_e_client, 'e2c_mac': self.dev_e2_client_mac})
        self.generate_and_settle(['iface1', 'iface2'])
        self.assert_iface_up('iface1', ['inet 10.10.10.11'])
        self.assert_iface_up('iface2', ['inet 10.10.10.22'])

    def test_link_offloading(self):
        self.setup_eth(None, False)
        # check kernel defaults
        out = subprocess.check_output(['ethtool', '-k', self.dev_e_client])
        self.assertIn(b'rx-checksumming: on', out)
        self.assertIn(b'tx-checksumming: on', out)
        self.assertIn(b'tcp-segmentation-offload: on', out)
        self.assertIn(b'tx-tcp6-segmentation: on', out)
        self.assertIn(b'generic-segmentation-offload: on', out)
        # enabled for armhf on autopkgtest.u.c but 'off' elsewhere
        # self.assertIn(b'generic-receive-offload: off', out)
        # validate turning off
        with open(self.config, 'w') as f:
            f.write('''network:
  renderer: %(r)s
  ethernets:
    %(ec)s:
      addresses: [10.10.10.22/24]
      receive-checksum-offload: off
      transmit-checksum-offload: off
      tcp-segmentation-offload: off
      tcp6-segmentation-offload: off
      generic-segmentation-offload: off
      generic-receive-offload: off
      #large-receive-offload: off # not possible on veth
''' % {'r': self.backend, 'ec': self.dev_e_client})
        self.generate_and_settle([self.dev_e_client])
        self.assert_iface_up(self.dev_e_client, ['inet 10.10.10.22'])
        out = subprocess.check_output(['ethtool', '-k', self.dev_e_client])
        self.assertIn(b'rx-checksumming: off', out)
        self.assertIn(b'tx-checksumming: off', out)
        self.assertIn(b'tcp-segmentation-offload: off', out)
        self.assertIn(b'tx-tcp6-segmentation: off', out)
        self.assertIn(b'generic-segmentation-offload: off', out)
        self.assertIn(b'generic-receive-offload: off', out)
        # validate turning on
        with open(self.config, 'w') as f:
            f.write('''network:
  renderer: %(r)s
  ethernets:
    %(ec)s:
      addresses: [10.10.10.22/24]
      receive-checksum-offload: true
      transmit-checksum-offload: true
      tcp-segmentation-offload: true
      tcp6-segmentation-offload: true
      generic-segmentation-offload: true
      generic-receive-offload: true
      #large-receive-offload: true # not possible on veth
''' % {'r': self.backend, 'ec': self.dev_e_client})
        self.generate_and_settle([self.dev_e_client])
        self.assert_iface_up(self.dev_e_client, ['inet 10.10.10.22'])
        out = subprocess.check_output(['ethtool', '-k', self.dev_e_client])
        self.assertIn(b'rx-checksumming: on', out)
        self.assertIn(b'tx-checksumming: on', out)
        self.assertIn(b'tcp-segmentation-offload: on', out)
        self.assertIn(b'tx-tcp6-segmentation: on', out)
        self.assertIn(b'generic-segmentation-offload: on', out)
        self.assertIn(b'generic-receive-offload: on', out)


@unittest.skipIf("networkd" not in test_backends,
                 "skipping as networkd backend tests are disabled")
class TestNetworkd(IntegrationTestsBase, _CommonTests):
    backend = 'networkd'

    def test_eth_dhcp6_off(self):
        self.setup_eth('slaac')
        with open(self.config, 'w') as f:
            f.write('''network:
  version: 2
  renderer: %(r)s
  ethernets:
    %(ec)s:
      dhcp6: no
      accept-ra: yes
      addresses: [ '192.168.1.100/24' ]''' % {'r': self.backend, 'ec': self.dev_e_client})
        self.generate_and_settle([self.state_dhcp6(self.dev_e_client)])
        self.assert_iface_up(self.dev_e_client, ['inet6 2600:'], [])

    def test_eth_dhcp6_off_no_accept_ra(self):
        self.setup_eth('slaac')
        with open(self.config, 'w') as f:
            f.write('''network:
  version: 2
  renderer: %(r)s
  ethernets:
    %(ec)s:
      dhcp6: no
      accept-ra: no
      addresses: [ '192.168.1.100/24' ]''' % {'r': self.backend, 'ec': self.dev_e_client})
        self.generate_and_settle([self.dev_e_client])
        self.assert_iface_up(self.dev_e_client, [], ['inet6 2600:'])

    # TODO: implement link-local handling in NetworkManager backend and move this test into CommonTests()
    def test_link_local_ipv4(self):
        self.setup_eth(None)
        with open(self.config, 'w') as f:
            f.write('''network:
  renderer: %(r)s
  ethernets:
    %(ec)s:
      link-local: [ ipv4 ]''' % {'r': self.backend, 'ec': self.dev_e_client})
        self.generate_and_settle([self.dev_e_client])
        # Verify IPv4 link local address is there, while IPv6 is not
        self.assert_iface(self.dev_e_client, ['inet 169.254.'], ['inet6 fe80:'])

    # TODO: implement link-local handling in NetworkManager backend and move this test into CommonTests()
    def test_link_local_ipv6(self):
        self.setup_eth(None)
        with open(self.config, 'w') as f:
            f.write('''network:
  renderer: %(r)s
  ethernets:
    %(ec)s:
      link-local: [ ipv6 ]''' % {'r': self.backend, 'ec': self.dev_e_client})
        self.generate_and_settle([self.dev_e_client])
        # Verify IPv6 link local address is there, while IPv4 is not
        self.assert_iface(self.dev_e_client, ['inet6 fe80:'], ['inet 169.254.'])

    # TODO: implement link-local handling in NetworkManager backend and move this test into CommonTests()
    def test_link_local_disabled(self):
        self.setup_eth(None)
        with open(self.config, 'w') as f:
            f.write('''network:
  renderer: %(r)s
  ethernets:
    %(ec)s:
      addresses: ["172.16.5.3/20", "9876:BBBB::11/70"] # needed to bring up the interface at all
      link-local: []''' % {'r': self.backend, 'ec': self.dev_e_client})
        self.generate_and_settle([self.dev_e_client])
        # Verify IPv4 and IPv6 link local addresses are not there
        self.assert_iface(self.dev_e_client,
                          ['inet6 9876:bbbb::11/70', 'inet 172.16.5.3/20'],
                          ['inet6 fe80:', 'inet 169.254.'])


@unittest.skipIf("NetworkManager" not in test_backends,
                 "skipping as NetworkManager backend tests are disabled")
class TestNetworkManager(IntegrationTestsBase, _CommonTests):
    backend = 'NetworkManager'

    @unittest.skip("NetworkManager does not disable accept_ra: bug LP: #1704210")
    def test_eth_dhcp6_off(self):
        self.setup_eth('slaac')
        with open(self.config, 'w') as f:
            f.write('''network:
  version: 2
  renderer: %(r)s
  ethernets:
    %(ec)s:
      dhcp6: no
      addresses: [ '192.168.1.100/24' ]''' % {'r': self.backend, 'ec': self.dev_e_client})
        self.generate_and_settle([self.dev_e_client])
        self.assert_iface_up(self.dev_e_client, [], ['inet6 2600:'])


unittest.main(testRunner=unittest.TextTestRunner(stream=sys.stdout, verbosity=2))
