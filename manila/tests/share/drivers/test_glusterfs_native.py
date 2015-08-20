# Copyright (c) 2014 Red Hat, Inc.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

""" GlusterFS native protocol (glusterfs) driver for shares.

Test cases for GlusterFS native protocol driver.
"""

import re
import shutil
import tempfile

import ddt
import mock
from oslo_config import cfg

from manila.common import constants
from manila import context
from manila import exception
from manila.share import configuration as config
from manila.share.drivers.glusterfs import common
from manila.share.drivers import glusterfs_native
from manila import test
from manila.tests import fake_utils


CONF = cfg.CONF


def new_share(**kwargs):
    share = {
        'id': 'fakeid',
        'name': 'fakename',
        'size': 1,
        'share_proto': 'glusterfs',
    }
    share.update(kwargs)
    return share


class GlusterXMLOut(object):

    template = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cliOutput>
  <opRet>%(ret)d</opRet>
  <opErrno>%(errno)d</opErrno>
  <opErrstr>fake error</opErrstr>
</cliOutput>"""

    def __init__(self, **kwargs):
        self.params = kwargs

    def __call__(self, *args, **kwargs):
        return self.template % self.params, ''


@ddt.ddt
class GlusterfsNativeShareDriverTestCase(test.TestCase):
    """Tests GlusterfsNativeShareDriver."""

    def setUp(self):
        super(GlusterfsNativeShareDriverTestCase, self).setUp()
        fake_utils.stub_out_utils_execute(self)
        self._execute = fake_utils.fake_execute
        self._context = context.get_admin_context()

        self.glusterfs_target1 = 'root@host1:/gv1'
        self.glusterfs_target2 = 'root@host2:/gv2'
        self.glusterfs_server1 = 'root@host1'
        self.glusterfs_server2 = 'root@host2'
        self.glusterfs_server1_volumes = 'manila-share-1-1G\nshare1'
        self.glusterfs_server2_volumes = 'manila-share-2-2G\nshare2'
        self.share1 = new_share(
            export_location=self.glusterfs_target1,
            status=constants.STATUS_AVAILABLE)
        self.share2 = new_share(
            export_location=self.glusterfs_target2,
            status=constants.STATUS_AVAILABLE)
        gmgr = common.GlusterManager
        self.gmgr1 = gmgr(self.glusterfs_server1, self._execute, None, None,
                          requires={'volume': False})
        self.gmgr2 = gmgr(self.glusterfs_server2, self._execute, None, None,
                          requires={'volume': False})
        self.glusterfs_volumes_dict = (
            {'root@host1:/manila-share-1-1G': {'size': 1},
             'root@host2:/manila-share-2-2G': {'size': 2}})
        self.glusterfs_used_vols = set([
            'root@host1:/manila-share-1-1G',
            'root@host2:/manila-share-2-2G'])

        CONF.set_default('glusterfs_servers',
                         [self.glusterfs_server1, self.glusterfs_server2])
        CONF.set_default('glusterfs_native_server_password',
                         'fake_password')
        CONF.set_default('glusterfs_native_path_to_private_key',
                         '/fakepath/to/privatekey')
        CONF.set_default('glusterfs_volume_pattern',
                         'manila-share-\d+-#{size}G$')
        CONF.set_default('driver_handles_share_servers', False)

        self.fake_conf = config.Configuration(None)
        self.mock_object(tempfile, 'mkdtemp',
                         mock.Mock(return_value='/tmp/tmpKGHKJ'))
        self.mock_object(common.GlusterManager, 'make_gluster_call')

        with mock.patch.object(glusterfs_native.GlusterfsNativeShareDriver,
                               '_glustermanager',
                               side_effect=[self.gmgr1, self.gmgr2]):
            self._driver = glusterfs_native.GlusterfsNativeShareDriver(
                execute=self._execute,
                configuration=self.fake_conf)
        self._driver.glusterfs_versions = {self.glusterfs_server1: ('3', '6'),
                                           self.glusterfs_server2: ('3', '7')}
        self.addCleanup(fake_utils.fake_execute_set_repliers, [])
        self.addCleanup(fake_utils.fake_execute_clear_log)

    @ddt.data({"test_kwargs": {}, "requires": {"volume": True}},
              {"test_kwargs": {'req_volume': False},
               "requires": {"volume": False}})
    @ddt.unpack
    def test_glustermanager(self, test_kwargs, requires):
        fake_obj = mock.Mock()
        self.mock_object(common, 'GlusterManager',
                         mock.Mock(return_value=fake_obj))
        ret = self._driver._glustermanager(self.glusterfs_target1,
                                           **test_kwargs)
        common.GlusterManager.assert_called_once_with(
            self.glusterfs_target1, self._execute,
            self._driver.configuration.glusterfs_native_path_to_private_key,
            self._driver.configuration.glusterfs_native_server_password,
            requires=requires)
        self.assertEqual(fake_obj, ret)

    def test_compile_volume_pattern(self):
        volume_pattern = 'manila-share-\d+-(?P<size>\d+)G$'
        ret = self._driver._compile_volume_pattern()
        self.assertEqual(re.compile(volume_pattern), ret)

    def test_fetch_gluster_volumes(self):
        test_args = ('volume', 'list')

        self.mock_object(
            self.gmgr1, 'gluster_call',
            mock.Mock(return_value=(self.glusterfs_server1_volumes, '')))
        self.mock_object(
            self.gmgr2, 'gluster_call',
            mock.Mock(return_value=(self.glusterfs_server2_volumes, '')))
        _glustermanager_calls = (
            self.gmgr1,
            common.GlusterManager('root@host1:/manila-share-1-1G'),
            self.gmgr2,
            common.GlusterManager('root@host2:/manila-share-2-2G'))
        self.mock_object(self._driver, '_glustermanager',
                         mock.Mock(side_effect=_glustermanager_calls))
        expected_output = self.glusterfs_volumes_dict
        ret = self._driver._fetch_gluster_volumes()
        self.gmgr1.gluster_call.assert_called_once_with(*test_args)
        self.gmgr2.gluster_call.assert_called_once_with(*test_args)
        self.assertEqual(expected_output, ret)

    def test_fetch_gluster_volumes_error(self):
        test_args = ('volume', 'list')

        def raise_exception(*args, **kwargs):
            if(args == test_args):
                raise exception.ProcessExecutionError()

        self._driver.configuration.glusterfs_servers = [self.glusterfs_server1]
        self.mock_object(self.gmgr1, 'gluster_call',
                         mock.Mock(side_effect=raise_exception))
        self.mock_object(self._driver, '_glustermanager',
                         mock.Mock(return_value=self.gmgr1))
        self.mock_object(glusterfs_native.LOG, 'error')
        self.assertRaises(exception.GlusterfsException,
                          self._driver._fetch_gluster_volumes)
        self.gmgr1.gluster_call.assert_called_once_with(*test_args)
        self.assertTrue(glusterfs_native.LOG.error.called)

    def test_do_setup(self):
        self._driver.configuration.glusterfs_servers = [self.glusterfs_server1]
        self.mock_object(self.gmgr1, 'get_gluster_version',
                         mock.Mock(return_value=('3', '6')))
        self.mock_object(self._driver, '_glustermanager',
                         mock.Mock(return_value=self.gmgr1))
        self.mock_object(self._driver, '_fetch_gluster_volumes',
                         mock.Mock(return_value=self.glusterfs_volumes_dict))
        self._driver.gluster_used_vols = self.glusterfs_used_vols
        self.mock_object(glusterfs_native.LOG, 'warn')

        expected_exec = ['mount.glusterfs']

        self._driver.do_setup(self._context)

        self._driver._fetch_gluster_volumes.assert_called_once_with()
        self.assertEqual(expected_exec, fake_utils.fake_execute_get_log())
        self.gmgr1.get_gluster_version.assert_called_once_with()

    def test_do_setup_unsupported_glusterfs_version(self):
        self._driver.configuration.glusterfs_servers = [self.glusterfs_server1]
        self.mock_object(self.gmgr1, 'get_gluster_version',
                         mock.Mock(return_value=('3', '5')))
        self.mock_object(self._driver, '_glustermanager',
                         mock.Mock(return_value=self.gmgr1))

        self.assertRaises(exception.GlusterfsException,
                          self._driver.do_setup, self._context)
        self.gmgr1.get_gluster_version.assert_called_once_with()

    @ddt.data(exception.GlusterfsException, RuntimeError)
    def test_do_setup_get_gluster_version_fails(self, exc):
        def raise_exception(*args, **kwargs):
            raise exc

        self._driver.configuration.glusterfs_servers = [self.glusterfs_server1]
        self.mock_object(self.gmgr1, 'get_gluster_version',
                         mock.Mock(side_effect=raise_exception))
        self.mock_object(self._driver, '_glustermanager',
                         mock.Mock(return_value=self.gmgr1))
        self.assertRaises(exc, self._driver.do_setup, self._context)
        self.gmgr1.get_gluster_version.assert_called_once_with()

    def test_do_setup_glusterfs_no_volumes_provided_by_backend(self):
        self._driver.configuration.glusterfs_servers = [self.glusterfs_server1]
        self.mock_object(self.gmgr1, 'get_gluster_version',
                         mock.Mock(return_value=('3', '6')))
        self.mock_object(self._driver, '_glustermanager',
                         mock.Mock(return_value=self.gmgr1))
        self.mock_object(self._driver, '_fetch_gluster_volumes',
                         mock.Mock(return_value={}))

        self.assertRaises(exception.GlusterfsException,
                          self._driver.do_setup, self._context)
        self._driver._fetch_gluster_volumes.assert_called_once_with()

    def test_ensure_share(self):
        share = self.share1

        self._driver.ensure_share(self._context, share)

        self.assertIn(share['export_location'], self._driver.gluster_used_vols)

    def test_setup_gluster_vol(self):
        test_args = [
            ('volume', 'set', 'gv1', 'nfs.export-volumes', 'off'),
            ('volume', 'set', 'gv1', 'client.ssl', 'on'),
            ('volume', 'set', 'gv1', 'server.ssl', 'on')]
        common._restart_gluster_vol = mock.Mock()

        gmgr = common.GlusterManager
        gmgr1 = gmgr(self.glusterfs_target1, self._execute, None, None)
        self._driver._glustermanager = mock.Mock(return_value=gmgr1)
        self.mock_object(gmgr1, 'get_gluster_vol_option',
                         mock.Mock(return_value='some.common.name'))

        ret = self._driver._setup_gluster_vol(gmgr1.volume)
        gmgr1.get_gluster_vol_option.assert_called_once_with('auth.ssl-allow')
        gmgr1.gluster_call.has_calls(
            mock.call(*test_args[0]),
            mock.call(*test_args[1]),
            mock.call(*test_args[2]))
        self.assertIsNone(ret)
        self.assertTrue(common._restart_gluster_vol.called)

    @ddt.data(0, 1, 2)
    def test_setup_gluster_vols_excp(self, idx):
        test_args = [
            ('volume', 'set', 'gv1', 'nfs.export-volumes', 'off'),
            ('volume', 'set', 'gv1', 'client.ssl', 'on'),
            ('volume', 'set', 'gv1', 'server.ssl', 'on')]

        def raise_exception(*args, **kwargs):
            if(args == test_args[idx]):
                raise exception.ProcessExecutionError()

        common._restart_gluster_vol = mock.Mock()

        gmgr = common.GlusterManager
        gmgr1 = gmgr(self.glusterfs_target1, self._execute, None, None)
        self._driver._glustermanager = mock.Mock(return_value=gmgr1)
        self.mock_object(gmgr1, 'get_gluster_vol_option',
                         mock.Mock(return_value='some.common.name'))
        self.mock_object(gmgr1, 'gluster_call',
                         mock.Mock(side_effect=raise_exception))

        self.assertRaises(exception.GlusterfsException,
                          self._driver._setup_gluster_vol, gmgr1.volume)
        gmgr1.get_gluster_vol_option.assert_called_once_with('auth.ssl-allow')
        self.assertIn(mock.call(*test_args[idx]),
                      gmgr1.gluster_call.call_args_list)
        self.assertFalse(common._restart_gluster_vol.called)

    def test_setup_gluster_vol_no_ssl_allow(self):
        common._restart_gluster_vol = mock.Mock()

        gmgr = common.GlusterManager
        gmgr1 = gmgr(self.glusterfs_target1, self._execute, None, None)
        self._driver._glustermanager = mock.Mock(return_value=gmgr1)
        self.mock_object(gmgr1, 'get_gluster_vol_option',
                         mock.Mock(return_value=None))

        self.assertRaises(exception.GlusterfsException,
                          self._driver._setup_gluster_vol, gmgr1.volume)
        gmgr1.get_gluster_vol_option.assert_called_once_with('auth.ssl-allow')
        self.assertFalse(gmgr1.gluster_call.called)
        self.assertFalse(common._restart_gluster_vol.called)

    @ddt.data({"voldict": {"host:/share2G": {"size": 2}}, "used_vols": set(),
               "size": 1, "expected": "host:/share2G"},
              {"voldict": {"host:/share2G": {"size": 2}}, "used_vols": set(),
               "size": 2, "expected": "host:/share2G"},
              {"voldict": {"host:/share2G": {"size": 2}}, "used_vols": set(),
               "size": None, "expected": "host:/share2G"},
              {"voldict": {"host:/share2G": {"size": 2},
                           "host:/share": {"size": None}},
               "used_vols": set(["host:/share2G"]), "size": 1,
               "expected": "host:/share"},
              {"voldict": {"host:/share2G": {"size": 2},
                           "host:/share": {"size": None}},
               "used_vols": set(["host:/share2G"]), "size": 2,
               "expected": "host:/share"},
              {"voldict": {"host:/share2G": {"size": 2},
               "host:/share": {"size": None}},
               "used_vols": set(["host:/share2G"]), "size": 3,
               "expected": "host:/share"},
              {"voldict": {"host:/share2G": {"size": 2},
                           "host:/share": {"size": None}},
               "used_vols": set(["host:/share2G"]), "size": None,
               "expected": "host:/share"},
              {"voldict": {"host:/share": {}}, "used_vols": set(), "size": 1,
               "expected": "host:/share"},
              {"voldict": {"host:/share": {}}, "used_vols": set(),
               "size": None, "expected": "host:/share"})
    @ddt.unpack
    def test_pop_gluster_vol(self, voldict, used_vols, size, expected):
        gmgr = common.GlusterManager
        gmgr1 = gmgr(expected, self._execute, None, None)
        self._driver._fetch_gluster_volumes = mock.Mock(return_value=voldict)
        self._driver.gluster_used_vols = used_vols
        self._driver._setup_gluster_vol = mock.Mock(return_value=gmgr1)
        self._driver.volume_pattern_keys = list(voldict.values())[0].keys()

        result = self._driver._pop_gluster_vol(size=size)

        self.assertEqual(expected, result)
        self.assertIn(result, used_vols)
        self._driver._setup_gluster_vol.assert_called_once_with(result)

    @ddt.data({"voldict": {"share2G": {"size": 2}},
               "used_vols": set(), "size": 3},
              {"voldict": {"share2G": {"size": 2}},
               "used_vols": set(["share2G"]), "size": None})
    @ddt.unpack
    def test_pop_gluster_vol_excp(self, voldict, used_vols, size):
        self._driver._fetch_gluster_volumes = mock.Mock(return_value=voldict)
        self._driver.gluster_used_vols = used_vols
        self._driver.volume_pattern_keys = list(voldict.values())[0].keys()
        self._driver._setup_gluster_vol = mock.Mock()

        self.assertRaises(exception.GlusterfsException,
                          self._driver._pop_gluster_vol, size=size)
        self.assertFalse(self._driver._setup_gluster_vol.called)

    def test_push_gluster_vol(self):
        self._driver.gluster_used_vols = set([
            self.glusterfs_target1, self.glusterfs_target2])

        self._driver._push_gluster_vol(self.glusterfs_target2)

        self.assertEqual(1, len(self._driver.gluster_used_vols))
        self.assertFalse(
            self.glusterfs_target2 in self._driver.gluster_used_vols)

    def test_push_gluster_vol_excp(self):
        self._driver.gluster_used_vols = set([self.glusterfs_target1])
        self._driver.gluster_unused_vols_dict = {}

        self.assertRaises(exception.GlusterfsException,
                          self._driver._push_gluster_vol,
                          self.glusterfs_target2)

    @ddt.data({'vers_minor': '6',
               'cmd': 'find /tmp/tmpKGHKJ -mindepth 1 -delete'},
              {'vers_minor': '7',
               'cmd': 'find /tmp/tmpKGHKJ -mindepth 1 ! -path '
                      '/tmp/tmpKGHKJ/.trashcan ! -path '
                      '/tmp/tmpKGHKJ/.trashcan/internal_op -delete'})
    @ddt.unpack
    def test_wipe_gluster_vol(self, vers_minor, cmd):
        common._restart_gluster_vol = mock.Mock()
        common._mount_gluster_vol = mock.Mock()
        common._umount_gluster_vol = mock.Mock()
        shutil.rmtree = mock.Mock()
        test_args = [
            ('volume', 'set', 'gv1', 'client.ssl', 'off'),
            ('volume', 'set', 'gv1', 'server.ssl', 'off'),
            ('volume', 'set', 'gv1', 'client.ssl', 'on'),
            ('volume', 'set', 'gv1', 'server.ssl', 'on')]

        gmgr = common.GlusterManager
        gmgr1 = gmgr(self.glusterfs_target1, self._execute, None, None)
        self._driver.glusterfs_versions = {
            self.glusterfs_server1: ('3', vers_minor)}
        expected_exec = [cmd]

        self._driver._wipe_gluster_vol(gmgr1)

        self.assertEqual(2, common._restart_gluster_vol.call_count)
        self.assertEqual(
            [mock.call(*test_args[0]), mock.call(*test_args[1]),
             mock.call(*test_args[2]), mock.call(*test_args[3])],
            gmgr1.gluster_call.call_args_list)
        self.assertEqual(expected_exec, fake_utils.fake_execute_get_log())
        self.assertTrue(tempfile.mkdtemp.called)
        self.assertTrue(common._mount_gluster_vol.called)
        self.assertTrue(common._umount_gluster_vol.called)
        self.assertTrue(shutil.rmtree.called)

    def test_wipe_gluster_vol_excp1(self):
        common._restart_gluster_vol = mock.Mock()
        common._mount_gluster_vol = mock.Mock()
        common._umount_gluster_vol = mock.Mock()
        shutil.rmtree = mock.Mock()
        test_args = ('volume', 'set', 'gv1', 'client.ssl', 'off')

        def raise_exception(*args, **kwargs):
            if(args == test_args):
                raise exception.ProcessExecutionError()

        gmgr = common.GlusterManager
        gmgr1 = gmgr(self.glusterfs_target1, self._execute, None, None)
        self.mock_object(gmgr1, 'gluster_call',
                         mock.Mock(side_effect=raise_exception))

        self.assertRaises(exception.GlusterfsException,
                          self._driver._wipe_gluster_vol, gmgr1)
        self.assertEqual(
            [mock.call(*test_args)], gmgr1.gluster_call.call_args_list)
        self.assertFalse(common._restart_gluster_vol.called)
        self.assertFalse(tempfile.mkdtemp.called)
        self.assertFalse(common._mount_gluster_vol.called)
        self.assertFalse(common._umount_gluster_vol.called)
        self.assertFalse(shutil.rmtree.called)

    def test_wipe_gluster_vol_excp2(self):
        common._restart_gluster_vol = mock.Mock()
        common._mount_gluster_vol = mock.Mock()
        common._umount_gluster_vol = mock.Mock()
        shutil.rmtree = mock.Mock()
        test_args = [
            ('volume', 'set', 'gv1', 'client.ssl', 'off'),
            ('volume', 'set', 'gv1', 'server.ssl', 'off')]

        def raise_exception(*args, **kwargs):
            if(args == test_args[1]):
                raise exception.ProcessExecutionError()

        gmgr = common.GlusterManager
        gmgr1 = gmgr(self.glusterfs_target1, self._execute, None, None)
        self.mock_object(gmgr1, 'gluster_call',
                         mock.Mock(side_effect=raise_exception))

        self.assertRaises(exception.GlusterfsException,
                          self._driver._wipe_gluster_vol, gmgr1)
        self.assertEqual(
            [mock.call(*test_args[0]), mock.call(*test_args[1])],
            gmgr1.gluster_call.call_args_list)
        self.assertFalse(common._restart_gluster_vol.called)
        self.assertFalse(tempfile.mkdtemp.called)
        self.assertFalse(common._mount_gluster_vol.called)
        self.assertFalse(common._umount_gluster_vol.called)
        self.assertFalse(shutil.rmtree.called)

    def test_wipe_gluster_vol_excp3(self):
        common._restart_gluster_vol = mock.Mock()
        common._mount_gluster_vol = mock.Mock()
        common._umount_gluster_vol = mock.Mock()
        shutil.rmtree = mock.Mock()
        test_args = [
            ('volume', 'set', 'gv1', 'client.ssl', 'off'),
            ('volume', 'set', 'gv1', 'server.ssl', 'off'),
            ('volume', 'set', 'gv1', 'client.ssl', 'on')]

        def raise_exception(*args, **kwargs):
            if(args == test_args[2]):
                raise exception.ProcessExecutionError()

        gmgr = common.GlusterManager
        gmgr1 = gmgr(self.glusterfs_target1, self._execute, None, None)
        self.mock_object(gmgr1, 'gluster_call',
                         mock.Mock(side_effect=raise_exception))

        expected_exec = ['find /tmp/tmpKGHKJ -mindepth 1 -delete']

        self.assertRaises(exception.GlusterfsException,
                          self._driver._wipe_gluster_vol, gmgr1)
        self.assertEqual(
            [mock.call(*test_args[0]), mock.call(*test_args[1]),
             mock.call(*test_args[2])],
            gmgr1.gluster_call.call_args_list)
        self.assertEqual(expected_exec, fake_utils.fake_execute_get_log())
        self.assertTrue(common._restart_gluster_vol.called)
        self.assertTrue(tempfile.mkdtemp.called)
        self.assertTrue(common._mount_gluster_vol.called)
        self.assertTrue(common._umount_gluster_vol.called)
        self.assertTrue(shutil.rmtree.called)

    def test_wipe_gluster_vol_excp4(self):
        common._restart_gluster_vol = mock.Mock()
        common._mount_gluster_vol = mock.Mock()
        common._umount_gluster_vol = mock.Mock()
        shutil.rmtree = mock.Mock()
        test_args = [
            ('volume', 'set', 'gv1', 'client.ssl', 'off'),
            ('volume', 'set', 'gv1', 'server.ssl', 'off'),
            ('volume', 'set', 'gv1', 'client.ssl', 'on'),
            ('volume', 'set', 'gv1', 'server.ssl', 'on')]

        def raise_exception(*args, **kwargs):
            if(args == test_args[3]):
                raise exception.ProcessExecutionError()

        gmgr = common.GlusterManager
        gmgr1 = gmgr(self.glusterfs_target1, self._execute, None, None)
        self.mock_object(gmgr1, 'gluster_call',
                         mock.Mock(side_effect=raise_exception))

        expected_exec = ['find /tmp/tmpKGHKJ -mindepth 1 -delete']

        self.assertRaises(exception.GlusterfsException,
                          self._driver._wipe_gluster_vol, gmgr1)
        self.assertEqual(
            [mock.call(*test_args[0]), mock.call(*test_args[1]),
             mock.call(*test_args[2]), mock.call(*test_args[3])],
            gmgr1.gluster_call.call_args_list)
        self.assertEqual(expected_exec, fake_utils.fake_execute_get_log())
        self.assertTrue(common._restart_gluster_vol.called)
        self.assertTrue(tempfile.mkdtemp.called)
        self.assertTrue(common._mount_gluster_vol.called)
        self.assertTrue(common._umount_gluster_vol.called)
        self.assertTrue(shutil.rmtree.called)

    def test_wipe_gluster_vol_excp5(self):
        common._restart_gluster_vol = mock.Mock()
        common._mount_gluster_vol = mock.Mock()
        common._umount_gluster_vol = mock.Mock()
        shutil.rmtree = mock.Mock()

        gmgr = common.GlusterManager
        gmgr1 = gmgr(self.glusterfs_target1, self._execute, None, None)

        test_args = [
            ('volume', 'set', 'gv1', 'client.ssl', 'off'),
            ('volume', 'set', 'gv1', 'server.ssl', 'off')]

        def exec_runner(*ignore_args, **ignore_kwargs):
            raise exception.ProcessExecutionError

        expected_exec = [
            'find /tmp/tmpKGHKJ -mindepth 1 -delete']

        fake_utils.fake_execute_set_repliers([(expected_exec[0], exec_runner)])

        self.assertRaises(exception.GlusterfsException,
                          self._driver._wipe_gluster_vol, gmgr1)
        self.assertEqual(
            [mock.call(*test_args[0]), mock.call(*test_args[1])],
            gmgr1.gluster_call.call_args_list)
        self.assertTrue(common._restart_gluster_vol.called)
        self.assertTrue(tempfile.mkdtemp.called)
        self.assertTrue(common._mount_gluster_vol.called)
        self.assertTrue(common._umount_gluster_vol.called)
        self.assertTrue(shutil.rmtree.called)

    def test_wipe_gluster_vol_mount_fail(self):
        common._restart_gluster_vol = mock.Mock()
        common._mount_gluster_vol = mock.Mock()
        common._mount_gluster_vol.side_effect = exception.GlusterfsException
        common._umount_gluster_vol = mock.Mock()
        shutil.rmtree = mock.Mock()

        gmgr = common.GlusterManager
        gmgr1 = gmgr(self.glusterfs_target1, self._execute, None, None)

        test_args = [
            ('volume', 'set', 'gv1', 'client.ssl', 'off'),
            ('volume', 'set', 'gv1', 'server.ssl', 'off')]

        self.assertRaises(exception.GlusterfsException,
                          self._driver._wipe_gluster_vol, gmgr1)
        self.assertEqual(
            [mock.call(*test_args[0]), mock.call(*test_args[1])],
            gmgr1.gluster_call.call_args_list)
        self.assertTrue(common._restart_gluster_vol.called)
        self.assertTrue(tempfile.mkdtemp.called)
        self.assertTrue(common._mount_gluster_vol.called)
        self.assertFalse(common._umount_gluster_vol.called)
        self.assertTrue(shutil.rmtree.called)

    def test_wipe_gluster_vol_umount_fail(self):
        common._restart_gluster_vol = mock.Mock()
        common._mount_gluster_vol = mock.Mock()
        common._umount_gluster_vol = mock.Mock()
        common._umount_gluster_vol.side_effect = exception.GlusterfsException
        shutil.rmtree = mock.Mock()

        gmgr = common.GlusterManager
        gmgr1 = gmgr(self.glusterfs_target1, self._execute, None, None)

        test_args = [
            ('volume', 'set', 'gv1', 'client.ssl', 'off'),
            ('volume', 'set', 'gv1', 'server.ssl', 'off')]

        expected_exec = ['find /tmp/tmpKGHKJ -mindepth 1 -delete']

        self.assertRaises(exception.GlusterfsException,
                          self._driver._wipe_gluster_vol, gmgr1)
        self.assertEqual(
            [mock.call(*test_args[0]), mock.call(*test_args[1])],
            gmgr1.gluster_call.call_args_list)
        self.assertEqual(expected_exec, fake_utils.fake_execute_get_log())
        self.assertTrue(common._restart_gluster_vol.called)
        self.assertTrue(tempfile.mkdtemp.called)
        self.assertTrue(common._mount_gluster_vol.called)
        self.assertTrue(common._umount_gluster_vol.called)
        self.assertFalse(shutil.rmtree.called)

    def test_create_share(self):
        self._driver._pop_gluster_vol = mock.Mock(
            return_value=self.glusterfs_target1)

        share = new_share()

        exp_locn = self._driver.create_share(self._context, share)

        self.assertEqual(exp_locn, self.glusterfs_target1)
        self._driver._pop_gluster_vol.assert_called_once_with(share['size'])

    def test_create_share_excp(self):
        self._driver._pop_gluster_vol = mock.Mock(
            side_effect=exception.GlusterfsException)

        share = new_share()

        self.assertRaises(exception.GlusterfsException,
                          self._driver.create_share, self._context, share)
        self._driver._pop_gluster_vol.assert_called_once_with(
            share['size'])

    def test_delete_share(self):
        self._driver._push_gluster_vol = mock.Mock()
        self._driver._wipe_gluster_vol = mock.Mock()
        gmgr = common.GlusterManager
        gmgr1 = gmgr(self.glusterfs_target1, self._execute, None, None)
        self.mock_object(self._driver, '_glustermanager',
                         mock.Mock(return_value=gmgr1))
        self._driver.gluster_used_vols = set([self.glusterfs_target1])
        self._driver.delete_share(self._context, self.share1)
        self._driver._wipe_gluster_vol.assert_called_once_with(gmgr1)
        self._driver._push_gluster_vol.assert_called_once_with(
            self.glusterfs_target1)

    def test_delete_share_excp1(self):
        self._driver._wipe_gluster_vol = mock.Mock()
        self._driver._wipe_gluster_vol.side_effect = (
            exception.GlusterfsException)
        self._driver._push_gluster_vol = mock.Mock()
        gmgr = common.GlusterManager
        gmgr1 = gmgr(self.glusterfs_target1, self._execute, None, None)
        self.mock_object(self._driver, '_glustermanager',
                         mock.Mock(return_value=gmgr1))
        self._driver.gluster_used_vols = set([self.glusterfs_target1])
        self.assertRaises(exception.GlusterfsException,
                          self._driver.delete_share, self._context,
                          self.share1)
        self._driver._wipe_gluster_vol.assert_called_once_with(gmgr1)
        self.assertFalse(self._driver._push_gluster_vol.called)

    def test_create_snapshot(self):
        self._driver.gluster_nosnap_vols_dict = {}
        self._driver.glusterfs_versions = {self.glusterfs_server1: ('3', '6')}

        gmgr = common.GlusterManager
        gmgr1 = gmgr(self.glusterfs_target1, self._execute, None, None)

        self._driver.gluster_used_vols = set([self.glusterfs_target1])
        snapshot = {
            'id': 'fake_snap_id',
            'share_id': self.share1['id'],
            'share': self.share1
        }

        args = ('--xml', 'snapshot', 'create', 'manila-fake_snap_id',
                gmgr1.volume)
        self.mock_object(gmgr1, 'gluster_call',
                         mock.Mock(side_effect=GlusterXMLOut(ret=0, errno=0)))
        self.mock_object(self._driver, '_glustermanager',
                         mock.Mock(return_value=gmgr1))
        ret = self._driver.create_snapshot(self._context, snapshot)
        self.assertIsNone(ret)
        gmgr1.gluster_call.assert_called_once_with(*args)

    def test_create_snapshot_error(self):
        self._driver.gluster_nosnap_vols_dict = {}
        self._driver.glusterfs_versions = {self.glusterfs_server1: ('3', '6')}

        gmgr = common.GlusterManager
        gmgr1 = gmgr(self.glusterfs_target1, self._execute, None, None)

        self._driver.gluster_used_vols = set([self.glusterfs_target1])
        snapshot = {
            'id': 'fake_snap_id',
            'share_id': self.share1['id'],
            'share': self.share1
        }

        args = ('--xml', 'snapshot', 'create', 'manila-fake_snap_id',
                gmgr1.volume)
        self.mock_object(gmgr1, 'gluster_call',
                         mock.Mock(side_effect=GlusterXMLOut(ret=-1, errno=2)))
        self.mock_object(self._driver, '_glustermanager',
                         mock.Mock(return_value=gmgr1))
        self.assertRaises(exception.GlusterfsException,
                          self._driver.create_snapshot, self._context,
                          snapshot)
        gmgr1.gluster_call.assert_called_once_with(*args)

    @ddt.data({"vers_minor": '6', "exctype": exception.GlusterfsException},
              {"vers_minor": '7',
               "exctype": exception.ShareSnapshotNotSupported})
    @ddt.unpack
    def test_create_snapshot_no_snap(self, vers_minor, exctype):
        self._driver.gluster_nosnap_vols_dict = {}
        self._driver.glusterfs_versions = {
            self.glusterfs_server1: ('3', vers_minor)}

        gmgr = common.GlusterManager
        gmgr1 = gmgr(self.glusterfs_target1, self._execute, None, None)

        self._driver.gluster_used_vols = set([self.glusterfs_target1])
        snapshot = {
            'id': 'fake_snap_id',
            'share_id': self.share1['id'],
            'share': self.share1
        }

        args = ('--xml', 'snapshot', 'create', 'manila-fake_snap_id',
                gmgr1.volume)
        self.mock_object(gmgr1, 'gluster_call',
                         mock.Mock(side_effect=GlusterXMLOut(ret=-1, errno=0)))
        self.mock_object(self._driver, '_glustermanager',
                         mock.Mock(return_value=gmgr1))
        self.assertRaises(exctype, self._driver.create_snapshot, self._context,
                          snapshot)
        gmgr1.gluster_call.assert_called_once_with(*args)

    @ddt.data({"vers_minor": '6', "exctype": exception.GlusterfsException},
              {"vers_minor": '7',
               "exctype": exception.ShareSnapshotNotSupported})
    @ddt.unpack
    def test_create_snapshot_no_snap_cached(self, vers_minor, exctype):
        self._driver.gluster_nosnap_vols_dict = {
            self.share1['export_location']: 'fake error'}
        self._driver.glusterfs_versions = {
            self.glusterfs_server1: ('3', vers_minor)}

        self._driver.gluster_used_vols = set([self.glusterfs_target1])
        snapshot = {
            'id': 'fake_snap_id',
            'share_id': self.share1['id'],
            'share': self.share1
        }

        self.assertRaises(exctype, self._driver.create_snapshot, self._context,
                          snapshot)

    def test_find_actual_backend_snapshot_name(self):
        gmgr = common.GlusterManager
        gmgr1 = gmgr(self.share1['export_location'], self._execute, None, None)
        self.mock_object(gmgr1, 'gluster_call',
                         mock.Mock(return_value=('fake_snap_id_xyz', '')))
        args = ('snapshot', 'list', gmgr1.volume, '--mode=script')
        snapshot = {
            'id': 'fake_snap_id',
            'share_id': self.share1['id'],
            'share': self.share1
        }
        ret = self._driver._find_actual_backend_snapshot_name(gmgr1, snapshot)
        gmgr1.gluster_call.assert_called_once_with(*args)
        self.assertEqual('fake_snap_id_xyz', ret)

    @ddt.data('this is too bad', 'fake_snap_id_xyx\nfake_snap_id_pqr')
    def test_find_actual_backend_snapshot_name_bad_snap_list(self, snaplist):
        gmgr = common.GlusterManager
        gmgr1 = gmgr(self.share1['export_location'], self._execute, None, None)
        self.mock_object(gmgr1, 'gluster_call',
                         mock.Mock(return_value=(snaplist, '')))
        args = ('snapshot', 'list', gmgr1.volume, '--mode=script')
        snapshot = {
            'id': 'fake_snap_id',
            'share_id': self.share1['id'],
            'share': self.share1
        }
        self.assertRaises(exception.GlusterfsException,
                          self._driver._find_actual_backend_snapshot_name,
                          gmgr1, snapshot)
        gmgr1.gluster_call.assert_called_once_with(*args)

    @ddt.data({'glusterfs_target': 'root@host1:/gv1',
               'glusterfs_server': 'root@host1'},
              {'glusterfs_target': 'host1:/gv1',
               'glusterfs_server': 'host1'})
    @ddt.unpack
    def test_create_share_from_snapshot(self, glusterfs_target,
                                        glusterfs_server):
        share = new_share()
        snapshot = {
            'id': 'fake_snap_id',
            'share': new_share(export_location=glusterfs_target)
        }
        volume = ''.join(['manila-', share['id']])
        new_export_location = ':/'.join([glusterfs_server, volume])
        gmgr = common.GlusterManager
        old_gmgr = gmgr(glusterfs_target, self._execute, None, None)
        new_gmgr = gmgr(new_export_location, self._execute, None, None)
        self._driver.gluster_used_vols = set([glusterfs_target])
        self._driver.glusterfs_versions = {glusterfs_server: ('3', '7')}

        self.mock_object(old_gmgr, 'gluster_call',
                         mock.Mock(side_effect=[('', ''), ('', '')]))
        self.mock_object(new_gmgr, 'gluster_call',
                         mock.Mock(side_effect=[('', ''), ('', '')]))
        self.mock_object(new_gmgr, 'get_gluster_vol_option',
                         mock.Mock())
        new_gmgr.get_gluster_vol_option.return_value = (
            'glusterfs-server-1,client')
        self.mock_object(self._driver, '_find_actual_backend_snapshot_name',
                         mock.Mock(return_value='fake_snap_id_xyz'))
        self.mock_object(self._driver, '_share_manager',
                         mock.Mock(return_value=old_gmgr))
        self.mock_object(self._driver, '_glustermanager',
                         mock.Mock(return_value=new_gmgr))

        ret = self._driver.create_share_from_snapshot(
            self._context, share, snapshot, None)

        (self._driver._find_actual_backend_snapshot_name.
            assert_called_once_with(old_gmgr, snapshot))
        args = (('snapshot', 'activate', 'fake_snap_id_xyz',
                 'force', '--mode=script'),
                ('snapshot', 'clone', volume, 'fake_snap_id_xyz'))
        old_gmgr.gluster_call.assert_has_calls([mock.call(*a) for a in args])
        self._driver._share_manager.assert_called_once_with(
            snapshot['share'])
        self._driver._glustermanager.assert_called_once_with(
            gmgr.parse(new_export_location))
        new_gmgr.get_gluster_vol_option.assert_called_once_with(
            'auth.ssl-allow')
        args = (('volume', 'set', new_gmgr.volume, 'auth.ssl-allow',
                 'glusterfs-server-1'),
                ('volume', 'start', new_gmgr.volume), )
        new_gmgr.gluster_call.assert_has_calls([mock.call(*a) for a in args])
        self.assertIn(
            new_export_location,
            self._driver.gluster_used_vols)
        self.assertEqual(new_export_location, ret)

    def test_create_share_from_snapshot_error_old_gmr_gluster_calls(self):
        glusterfs_target = 'root@host1:/gv1'
        glusterfs_server = 'root@host1'
        share = new_share()
        snapshot = {
            'id': 'fake_snap_id',
            'share': new_share(export_location=glusterfs_target)
        }
        volume = ''.join(['manila-', share['id']])
        new_export_location = ':/'.join([glusterfs_server, volume])
        gmgr = common.GlusterManager
        old_gmgr = gmgr(glusterfs_target, self._execute, None, None)
        new_gmgr = gmgr(new_export_location, self._execute, None, None)
        self._driver.gluster_used_vols_dict = {glusterfs_target: old_gmgr}
        self._driver.glusterfs_versions = {glusterfs_server: ('3', '7')}

        self.mock_object(
            old_gmgr, 'gluster_call',
            mock.Mock(side_effect=[('', ''), exception.ProcessExecutionError]))
        self.mock_object(new_gmgr, 'gluster_call',
                         mock.Mock(side_effect=[('', ''), ('', '')]))
        self.mock_object(new_gmgr, 'get_gluster_vol_option',
                         mock.Mock())
        new_gmgr.get_gluster_vol_option.return_value = (
            'glusterfs-server-1,client')
        self.mock_object(self._driver, '_find_actual_backend_snapshot_name',
                         mock.Mock(return_value='fake_snap_id_xyz'))
        self.mock_object(self._driver, '_share_manager',
                         mock.Mock(return_value=old_gmgr))
        self.mock_object(self._driver, '_glustermanager',
                         mock.Mock(return_value=new_gmgr))

        self.assertRaises(exception.GlusterfsException,
                          self._driver.create_share_from_snapshot,
                          self._context, share, snapshot)

        (self._driver._find_actual_backend_snapshot_name.
            assert_called_once_with(old_gmgr, snapshot))
        args = (('snapshot', 'activate', 'fake_snap_id_xyz',
                 'force', '--mode=script'),
                ('snapshot', 'clone', volume, 'fake_snap_id_xyz'))
        old_gmgr.gluster_call.assert_has_calls([mock.call(*a) for a in args])
        self._driver._share_manager.assert_called_once_with(
            snapshot['share'])
        self.assertFalse(new_gmgr.get_gluster_vol_option.called)
        self.assertFalse(new_gmgr.gluster_call.called)
        self.assertNotIn(new_export_location,
                         self._driver.glusterfs_versions.keys())

    def test_create_share_from_snapshot_error_new_gmr_gluster_calls(self):
        glusterfs_target = 'root@host1:/gv1'
        glusterfs_server = 'root@host1'
        share = new_share()
        snapshot = {
            'id': 'fake_snap_id',
            'share': new_share(export_location=glusterfs_target)
        }
        volume = ''.join(['manila-', share['id']])
        new_export_location = ':/'.join([glusterfs_server, volume])
        gmgr = common.GlusterManager
        old_gmgr = gmgr(glusterfs_target, self._execute, None, None)
        new_gmgr = gmgr(new_export_location, self._execute, None, None)
        self._driver.gluster_used_vols_dict = {glusterfs_target: old_gmgr}
        self._driver.glusterfs_versions = {glusterfs_server: ('3', '7')}

        self.mock_object(
            old_gmgr, 'gluster_call',
            mock.Mock(side_effect=[('', ''), ('', '')]))
        self.mock_object(
            new_gmgr, 'gluster_call',
            mock.Mock(side_effect=[('', ''), exception.ProcessExecutionError]))
        self.mock_object(new_gmgr, 'get_gluster_vol_option',
                         mock.Mock())
        new_gmgr.get_gluster_vol_option.return_value = (
            'glusterfs-server-1,client')
        self.mock_object(self._driver, '_find_actual_backend_snapshot_name',
                         mock.Mock(return_value='fake_snap_id_xyz'))
        self.mock_object(self._driver, '_share_manager',
                         mock.Mock(return_value=old_gmgr))
        self.mock_object(self._driver, '_glustermanager',
                         mock.Mock(return_value=new_gmgr))

        self.assertRaises(exception.GlusterfsException,
                          self._driver.create_share_from_snapshot,
                          self._context, share, snapshot)

        (self._driver._find_actual_backend_snapshot_name.
            assert_called_once_with(old_gmgr, snapshot))
        args = (('snapshot', 'activate', 'fake_snap_id_xyz',
                 'force', '--mode=script'),
                ('snapshot', 'clone', volume, 'fake_snap_id_xyz'))
        old_gmgr.gluster_call.assert_has_calls([mock.call(*a) for a in args])
        self._driver._share_manager.assert_called_once_with(
            snapshot['share'])
        self._driver._glustermanager.assert_called_once_with(
            gmgr.parse(new_export_location))
        new_gmgr.get_gluster_vol_option.assert_called_once_with(
            'auth.ssl-allow')
        args = (('volume', 'set', new_gmgr.volume, 'auth.ssl-allow',
                 'glusterfs-server-1'),
                ('volume', 'start', new_gmgr.volume), )
        new_gmgr.gluster_call.assert_has_calls([mock.call(*a) for a in args])
        self.assertNotIn(new_export_location,
                         self._driver.glusterfs_versions.keys())

    def test_create_share_from_snapshot_error_unsupported_gluster_version(
            self):

        glusterfs_target = 'root@host1:/gv1'
        glusterfs_server = 'root@host1'
        share = new_share()
        snapshot = {
            'id': 'fake_snap_id',
            'share': new_share(export_location=glusterfs_target)
        }
        volume = ''.join(['manila-', share['id']])
        new_export_location = ':/'.join([glusterfs_server, volume])
        gmgr = common.GlusterManager
        old_gmgr = gmgr(glusterfs_target, self._execute, None, None)
        new_gmgr = gmgr(new_export_location, self._execute, None, None)
        self._driver.gluster_used_vols_dict = {glusterfs_target: old_gmgr}
        self._driver.glusterfs_versions = {glusterfs_server: ('3', '6')}

        self.mock_object(
            old_gmgr, 'gluster_call',
            mock.Mock(side_effect=[('', ''), ('', '')]))
        self.mock_object(
            new_gmgr, 'gluster_call',
            mock.Mock(side_effect=[('', ''), exception.ProcessExecutionError]))
        self.mock_object(new_gmgr, 'get_gluster_vol_option',
                         mock.Mock())
        new_gmgr.get_gluster_vol_option.return_value = (
            'glusterfs-server-1,client')
        self.mock_object(self._driver, '_find_actual_backend_snapshot_name',
                         mock.Mock(return_value='fake_snap_id_xyz'))
        self.mock_object(self._driver, '_share_manager',
                         mock.Mock(return_value=old_gmgr))
        self.mock_object(self._driver, '_glustermanager',
                         mock.Mock(return_value=new_gmgr))

        self.assertRaises(exception.GlusterfsException,
                          self._driver.create_share_from_snapshot,
                          self._context, share, snapshot)

        self.assertFalse(
            self._driver._find_actual_backend_snapshot_name.called)
        self.assertFalse(old_gmgr.gluster_call.called)
        self._driver._share_manager.assert_called_once_with(
            snapshot['share'])
        self.assertFalse(self._driver._glustermanager.called)
        self.assertFalse(new_gmgr.get_gluster_vol_option.called)
        self.assertFalse(new_gmgr.gluster_call.called)
        self.assertNotIn(new_export_location,
                         self._driver.glusterfs_versions.keys())

    def test_delete_snapshot(self):
        self._driver.gluster_nosnap_vols_dict = {}

        gmgr = common.GlusterManager
        gmgr1 = gmgr(self.share1['export_location'], self._execute, None, None)

        self._driver.gluster_used_vols = set([self.glusterfs_target1])
        snapshot = {
            'id': 'fake_snap_id',
            'share_id': self.share1['id'],
            'share': self.share1
        }
        self.mock_object(self._driver, '_find_actual_backend_snapshot_name',
                         mock.Mock(return_value='fake_snap_id_xyz'))
        args = ('--xml', 'snapshot', 'delete', 'fake_snap_id_xyz',
                '--mode=script')
        self.mock_object(
            gmgr1, 'gluster_call',
            mock.Mock(return_value=GlusterXMLOut(ret=0, errno=0)()))
        self.mock_object(self._driver, '_glustermanager',
                         mock.Mock(return_value=gmgr1))
        ret = self._driver.delete_snapshot(self._context, snapshot)
        self.assertEqual(None, ret)
        gmgr1.gluster_call.assert_called_once_with(*args)
        (self._driver._find_actual_backend_snapshot_name.
            assert_called_once_with(gmgr1, snapshot))

    @ddt.data(GlusterXMLOut(ret=-1, errno=2)(), ('', ''))
    def test_delete_snapshot_error(self, badxmloutput):
        self._driver.gluster_nosnap_vols_dict = {}

        gmgr = common.GlusterManager
        gmgr1 = gmgr(self.share1['export_location'], self._execute, None, None)

        self._driver.gluster_used_vols = set([self.glusterfs_target1])
        snapshot = {
            'id': 'fake_snap_id',
            'share_id': self.share1['id'],
            'share': self.share1
        }
        self.mock_object(self._driver, '_find_actual_backend_snapshot_name',
                         mock.Mock(return_value='fake_snap_id_xyz'))
        args = ('--xml', 'snapshot', 'delete', 'fake_snap_id_xyz',
                '--mode=script')
        self.mock_object(
            gmgr1, 'gluster_call',
            mock.Mock(return_value=badxmloutput))
        self.mock_object(self._driver, '_glustermanager',
                         mock.Mock(return_value=gmgr1))
        self.assertRaises(exception.GlusterfsException,
                          self._driver.delete_snapshot, self._context,
                          snapshot)
        gmgr1.gluster_call.assert_called_once_with(*args)
        (self._driver._find_actual_backend_snapshot_name.
            assert_called_once_with(gmgr1, snapshot))

    def test_allow_access(self):
        common._restart_gluster_vol = mock.Mock()
        access = {'access_type': 'cert', 'access_to': 'client.example.com'}
        gmgr = common.GlusterManager
        gmgr1 = gmgr(self.glusterfs_target1, self._execute, None, None)
        self.mock_object(gmgr1, 'get_gluster_vol_option',
                         mock.Mock(return_value='some.common.name'))
        self.mock_object(self._driver, '_glustermanager',
                         mock.Mock(return_value=gmgr1))
        test_args = ('volume', 'set', 'gv1', 'auth.ssl-allow',
                     'some.common.name,' + access['access_to'])

        self._driver.gluster_used_vols = set([self.glusterfs_target1])

        self._driver.allow_access(self._context, self.share1, access)
        gmgr1.get_gluster_vol_option.assert_called_once_with('auth.ssl-allow')
        gmgr1.gluster_call.assert_called_once_with(*test_args)
        common._restart_gluster_vol.assert_called_once_with(gmgr1)

    def test_allow_access_with_share_having_access(self):
        common._restart_gluster_vol = mock.Mock()
        access = {'access_type': 'cert', 'access_to': 'client.example.com'}
        gmgr = common.GlusterManager
        gmgr1 = gmgr(self.glusterfs_target1, self._execute, None, None)
        self.mock_object(
            gmgr1, 'get_gluster_vol_option',
            mock.Mock(return_value='some.common.name,' + access['access_to']))
        self.mock_object(self._driver, '_glustermanager',
                         mock.Mock(return_value=gmgr1))

        self._driver.gluster_used_vols = set([self.glusterfs_target1])

        self._driver.allow_access(self._context, self.share1, access)
        gmgr1.get_gluster_vol_option.assert_called_once_with('auth.ssl-allow')
        self.assertFalse(gmgr1.gluster_call.called)
        self.assertFalse(common._restart_gluster_vol.called)

    def test_allow_access_invalid_access_type(self):
        common._restart_gluster_vol = mock.Mock()
        access = {'access_type': 'invalid', 'access_to': 'client.example.com'}
        expected_exec = []

        self.assertRaises(exception.InvalidShareAccess,
                          self._driver.allow_access,
                          self._context, self.share1, access)
        self.assertFalse(common._restart_gluster_vol.called)
        self.assertEqual(expected_exec, fake_utils.fake_execute_get_log())

    def test_allow_access_excp(self):
        access = {'access_type': 'cert', 'access_to': 'client.example.com'}
        test_args = ('volume', 'set', 'gv1', 'auth.ssl-allow',
                     'some.common.name,' + access['access_to'])

        def raise_exception(*args, **kwargs):
            if (args == test_args):
                raise exception.ProcessExecutionError()

        common._restart_gluster_vol = mock.Mock()

        gmgr = common.GlusterManager
        gmgr1 = gmgr(self.glusterfs_target1, self._execute, None, None)
        self.mock_object(gmgr1, 'get_gluster_vol_option',
                         mock.Mock(return_value='some.common.name'))
        self.mock_object(self._driver, '_glustermanager',
                         mock.Mock(return_value=gmgr1))
        self._driver.gluster_used_vols = set([self.glusterfs_target1])

        self.mock_object(gmgr1, 'gluster_call',
                         mock.Mock(side_effect=raise_exception))

        self.assertRaises(exception.GlusterfsException,
                          self._driver.allow_access,
                          self._context, self.share1, access)
        gmgr1.get_gluster_vol_option.assert_called_once_with('auth.ssl-allow')
        gmgr1.gluster_call.assert_called_once_with(*test_args)
        self.assertFalse(common._restart_gluster_vol.called)

    def test_deny_access(self):
        common._restart_gluster_vol = mock.Mock()
        access = {'access_type': 'cert', 'access_to': 'client.example.com'}
        gmgr = common.GlusterManager
        gmgr1 = gmgr(self.glusterfs_target1, self._execute, None, None)
        self.mock_object(
            gmgr1, 'get_gluster_vol_option',
            mock.Mock(return_value='some.common.name,' + access['access_to']))
        self.mock_object(self._driver, '_glustermanager',
                         mock.Mock(return_value=gmgr1))
        test_args = ('volume', 'set', 'gv1', 'auth.ssl-allow',
                     'some.common.name')

        self._driver.gluster_used_vols = set([self.glusterfs_target1])

        self._driver.deny_access(self._context, self.share1, access)
        gmgr1.get_gluster_vol_option.assert_called_once_with('auth.ssl-allow')
        gmgr1.gluster_call.assert_called_once_with(*test_args)
        common._restart_gluster_vol.assert_called_once_with(gmgr1)

    def test_deny_access_with_share_having_no_access(self):
        common._restart_gluster_vol = mock.Mock()
        access = {'access_type': 'cert', 'access_to': 'client.example.com'}
        gmgr = common.GlusterManager
        gmgr1 = gmgr(self.glusterfs_target1, self._execute, None, None)
        self.mock_object(gmgr1, 'get_gluster_vol_option',
                         mock.Mock(return_value='some.common.name'))
        self.mock_object(self._driver, '_glustermanager',
                         mock.Mock(return_value=gmgr1))

        self._driver.gluster_used_vols = set([self.glusterfs_target1])

        self._driver.deny_access(self._context, self.share1, access)
        gmgr1.get_gluster_vol_option.assert_called_once_with('auth.ssl-allow')
        self.assertFalse(gmgr1.gluster_call.called)
        self.assertFalse(common._restart_gluster_vol.called)

    def test_deny_access_invalid_access_type(self):
        common._restart_gluster_vol = mock.Mock()
        access = {'access_type': 'invalid', 'access_to': 'NotApplicable'}

        self.assertRaises(exception.InvalidShareAccess,
                          self._driver.deny_access,
                          self._context, self.share1, access)
        self.assertFalse(common._restart_gluster_vol.called)

    def test_deny_access_excp(self):
        access = {'access_type': 'cert', 'access_to': 'client.example.com'}
        test_args = ('volume', 'set', 'gv1', 'auth.ssl-allow',
                     'some.common.name')

        def raise_exception(*args, **kwargs):
            if (args == test_args):
                raise exception.ProcessExecutionError()

        common._restart_gluster_vol = mock.Mock()

        gmgr = common.GlusterManager
        gmgr1 = gmgr(self.glusterfs_target1, self._execute, None, None)
        self.mock_object(
            gmgr1, 'get_gluster_vol_option',
            mock.Mock(return_value='some.common.name,' + access['access_to']))
        self.mock_object(self._driver, '_glustermanager',
                         mock.Mock(return_value=gmgr1))
        self._driver.gluster_used_vols = set([self.glusterfs_target1])

        self.mock_object(gmgr1, 'gluster_call',
                         mock.Mock(side_effect=raise_exception))

        self.assertRaises(exception.GlusterfsException,
                          self._driver.deny_access,
                          self._context, self.share1, access)
        gmgr1.get_gluster_vol_option.assert_called_once_with('auth.ssl-allow')
        gmgr1.gluster_call.assert_called_once_with(*test_args)
        self.assertFalse(common._restart_gluster_vol.called)

    def test_get_share_stats_refresh_false(self):
        self._driver._stats = mock.Mock()
        ret = self._driver.get_share_stats()
        self.assertEqual(ret, self._driver._stats)

    def test_get_share_stats_refresh_true(self):
        def foo():
            self._driver._stats = {'key': 'value'}
        self._driver._update_share_stats = mock.Mock(side_effect=foo)
        ret = self._driver.get_share_stats(refresh=True)
        self.assertEqual(ret, {'key': 'value'})

    def test_update_share_stats(self):
        test_data = {
            'share_backend_name': 'GlusterFS-Native',
            'driver_handles_share_servers': False,
            'vendor_name': 'Red Hat',
            'driver_version': '1.1',
            'storage_protocol': 'glusterfs',
            'reserved_percentage': 0,
            'QoS_support': False,
            'total_capacity_gb': 'infinite',
            'free_capacity_gb': 'infinite',
            'pools': None,
            'snapshot_support': True,
        }

        self._driver._update_share_stats()

        self.assertEqual(self._driver._stats, test_data)
