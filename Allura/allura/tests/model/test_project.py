# -*- coding: utf-8 -*-

#       Licensed to the Apache Software Foundation (ASF) under one
#       or more contributor license agreements.  See the NOTICE file
#       distributed with this work for additional information
#       regarding copyright ownership.  The ASF licenses this file
#       to you under the Apache License, Version 2.0 (the
#       "License"); you may not use this file except in compliance
#       with the License.  You may obtain a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#       Unless required by applicable law or agreed to in writing,
#       software distributed under the License is distributed on an
#       "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
#       KIND, either express or implied.  See the License for the
#       specific language governing permissions and limitations
#       under the License.

"""
Model tests for project
"""
from nose.tools import with_setup, assert_equal
from pylons import tmpl_context as c
from ming.orm.ormsession import ThreadLocalORMSession

from allura import model as M
from allura.lib import helpers as h
from allura.tests import decorators as td
from alluratest.controller import setup_basic_test, setup_global_objects
from allura.lib.exceptions import ToolError
from mock import MagicMock


def setUp():
    setup_basic_test()
    setup_with_tools()

@td.with_wiki
def setup_with_tools():
    setup_global_objects()

@with_setup(setUp)
def test_project():
    assert type(c.project.sidebar_menu()) == list
    assert c.project.script_name in c.project.url()
    old_proj = c.project
    h.set_context('test/sub1', neighborhood='Projects')
    assert type(c.project.sidebar_menu()) == list
    assert type(c.project.sitemap()) == list
    assert c.project.sitemap()[0].label == 'Admin'
    assert old_proj in list(c.project.parent_iter())
    h.set_context('test', 'wiki', neighborhood='Projects')
    adobe_nbhd = M.Neighborhood.query.get(name='Adobe')
    p = M.Project.query.get(shortname='adobe-1', neighborhood_id=adobe_nbhd._id)
    # assert 'http' in p.url() # We moved adobe into /adobe/, not http://adobe....
    assert p.script_name in p.url()
    assert c.project.shortname == 'test'
    assert '<p>' in c.project.description_html
    c.project.uninstall_app('hello-test-mount-point')
    ThreadLocalORMSession.flush_all()

    c.project.install_app('Wiki', 'hello-test-mount-point')
    c.project.support_page = 'hello-test-mount-point'
    assert_equal(c.project.app_config('wiki').tool_name, 'wiki')
    ThreadLocalORMSession.flush_all()
    with td.raises(ToolError):
        # already installed
        c.project.install_app('Wiki', 'hello-test-mount-point')
    ThreadLocalORMSession.flush_all()
    c.project.uninstall_app('hello-test-mount-point')
    ThreadLocalORMSession.flush_all()
    with td.raises(ToolError):
        # mount point reserved
        c.project.install_app('Wiki', 'feed')
    with td.raises(ToolError):
        # mount point too long
        c.project.install_app('Wiki', 'a' * 64)
    with td.raises(ToolError):
        # mount point must begin with letter
        c.project.install_app('Wiki', '1')
    # single letter mount points are allowed
    c.project.install_app('Wiki', 'a')
    # Make sure the project support page is reset if the tool it was pointing
    # to is uninstalled.
    assert c.project.support_page == ''
    app_config = c.project.app_config('hello')
    app_inst = c.project.app_instance(app_config)
    app_inst = c.project.app_instance('hello')
    app_inst = c.project.app_instance('hello2123')
    c.project.breadcrumbs()
    c.app.config.breadcrumbs()

def test_subproject():
    with td.raises(ToolError):
        # name exceeds 15 chars
        sp = c.project.new_subproject('test-project-nose')
    sp = c.project.new_subproject('test-proj-nose')
    spp = sp.new_subproject('spp')
    ThreadLocalORMSession.flush_all()
    sp.delete()
    ThreadLocalORMSession.flush_all()

@td.with_wiki
def test_anchored_tools():
    c.project.neighborhood.anchored_tools = 'wiki:Wiki, tickets:Ticket'
    c.project.install_app = MagicMock()
    assert c.project.sitemap()[0].label == 'Wiki'
    assert c.project.install_app.call_args[0][0] == 'tickets'
    assert c.project.ordered_mounts()[0]['ac'].tool_name == 'wiki'


def test_set_ordinal_to_admin_tool():
    assert c.project.sitemap()
    assert c.project.app_config('admin').options.ordinal == 100

def test_users_and_roles():
    p = c.project
    sub = c.project.direct_subprojects.next()
    u = M.User.by_username('test-admin')
    assert p.users_with_role('Admin') == [u]
    assert p.users_with_role('Admin') == sub.users_with_role('Admin')
    assert p.users_with_role('Admin') == p.admins()
