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
Model tests for auth
"""
from nose.tools import with_setup, assert_equal
from pylons import tmpl_context as c, app_globals as g
from webob import Request

from pymongo.errors import DuplicateKeyError
from ming.orm.ormsession import ThreadLocalORMSession

from allura import model as M
from allura.lib import plugin
from allura.tests import decorators as td
from alluratest.controller import setup_basic_test, setup_global_objects


def setUp():
    setup_basic_test()
    ThreadLocalORMSession.close_all()
    setup_global_objects()

@with_setup(setUp)
def test_password_encoder():
    # Verify salt
    ep = plugin.LocalAuthenticationProvider(Request.blank('/'))._encode_password
    assert ep('test_pass') != ep('test_pass')
    assert ep('test_pass', '0000') == ep('test_pass', '0000')

@with_setup(setUp)
def test_email_address():
    addr = M.EmailAddress(_id='test_admin@sf.net', claimed_by_user_id=c.user._id)
    ThreadLocalORMSession.flush_all()
    assert addr.claimed_by_user() == c.user
    addr2 = M.EmailAddress.upsert('test@sf.net')
    addr3 = M.EmailAddress.upsert('test_admin@sf.net')
    assert addr3 is addr
    assert addr2 is not addr
    assert addr2
    addr4 = M.EmailAddress.upsert('test@SF.NET')
    assert addr4 is addr2
    addr.send_verification_link()
    assert addr is c.user.address_object('test_admin@sf.net')
    c.user.claim_address('test@SF.NET')
    assert 'test@sf.net' in c.user.email_addresses

@with_setup(setUp)
def test_openid():
    oid = M.OpenId.upsert('http://google.com/accounts/1', 'My Google OID')
    oid.claimed_by_user_id = c.user._id
    ThreadLocalORMSession.flush_all()
    assert oid.claimed_by_user() is c.user
    assert M.OpenId.upsert('http://google.com/accounts/1', 'My Google OID') is oid
    ThreadLocalORMSession.flush_all()
    assert oid is c.user.openid_object(oid._id)
    c.user.claim_openid('http://google.com/accounts/2')
    oid2 = M.OpenId.upsert('http://google.com/accounts/2', 'My Google OID')
    assert oid2._id in c.user.open_ids
    ThreadLocalORMSession.flush_all()

@td.with_user_project('test-admin')
@with_setup(setUp)
def test_user():
    assert c.user.url() .endswith('/u/test-admin/')
    assert c.user.script_name .endswith('/u/test-admin/')
    assert_equal(set(p.shortname for p in c.user.my_projects()), set(['test', 'test2', 'u/test-admin', 'adobe-1', '--init--']))
    # delete one of the projects and make sure it won't appear in my_projects()
    p = M.Project.query.get(shortname='test2')
    p.deleted = True
    assert_equal(set(p.shortname for p in c.user.my_projects()), set(['test', 'u/test-admin', 'adobe-1', '--init--']))
    assert_equal(M.User.anonymous().project_role().name, '*anonymous')
    u = M.User.register(dict(
            username='nosetest-user'))
    ThreadLocalORMSession.flush_all()
    assert_equal(u.private_project().shortname, 'u/nosetest-user')
    roles = g.credentials.user_roles(
        u._id, project_id=u.private_project().root_project._id)
    assert len(roles) == 3, roles
    u.set_password('foo')
    provider = plugin.LocalAuthenticationProvider(Request.blank('/'))
    assert provider._validate_password(u, 'foo')
    assert not provider._validate_password(u, 'foobar')
    u.set_password('foobar')
    assert provider._validate_password(u, 'foobar')
    assert not provider._validate_password(u, 'foo')

@with_setup(setUp)
def test_user_project_creates_on_demand():
    u = M.User.register(dict(username='foobar123'), make_project=False)
    ThreadLocalORMSession.flush_all()
    assert not M.Project.query.get(shortname='u/foobar123')
    assert u.private_project()
    assert M.Project.query.get(shortname='u/foobar123')

@with_setup(setUp)
def test_user_project_already_deleted_creates_on_demand():
    u = M.User.register(dict(username='foobar123'), make_project=True)
    p = M.Project.query.get(shortname='u/foobar123')
    p.deleted = True
    ThreadLocalORMSession.flush_all()
    assert not M.Project.query.get(shortname='u/foobar123', deleted=False)
    assert u.private_project()
    ThreadLocalORMSession.flush_all()
    assert M.Project.query.get(shortname='u/foobar123', deleted=False)

@with_setup(setUp)
def test_user_project_does_not_create_on_demand_for_disabled_user():
    u = M.User.register(dict(username='foobar123', disabled=True), make_project=False)
    ThreadLocalORMSession.flush_all()
    assert not u.private_project()
    assert not M.Project.query.get(shortname='u/foobar123')

@with_setup(setUp)
def test_project_role():
    role = M.ProjectRole(project_id=c.project._id, name='test_role')
    c.user.project_role().roles.append(role._id)
    ThreadLocalORMSession.flush_all()
    roles = g.credentials.user_roles(
        c.user._id, project_id=c.project.root_project._id)
    roles_ids = [role['_id'] for role in roles]
    roles = M.ProjectRole.query.find({'_id': {'$in': roles_ids}})
    for pr in roles:
        assert pr.display()
        pr.special
        assert pr.user in (c.user, None, M.User.anonymous())

@with_setup(setUp)
def test_default_project_roles():
    roles = dict(
        (pr.name, pr)
        for pr in M.ProjectRole.query.find(dict(
                project_id=c.project._id)).all()
        if pr.name)
    assert 'Admin' in roles.keys(), roles.keys()
    assert 'Developer' in roles.keys(), roles.keys()
    assert 'Member' in roles.keys(), roles.keys()
    assert roles['Developer']._id in roles['Admin'].roles
    assert roles['Member']._id in roles['Developer'].roles

    # There're 1 user assigned to project, represented by
    # relational (vs named) ProjectRole's
    assert len(roles) == M.ProjectRole.query.find(dict(
        project_id=c.project._id)).count() - 1

@with_setup(setUp)
def test_dup_api_token():
    from ming.orm import session
    u = M.User.register(dict(username='nosetest-user'))
    ThreadLocalORMSession.flush_all()
    tok = M.ApiToken(user_id=u._id)
    session(tok).flush()
    tok2 = M.ApiToken(user_id=u._id)
    try:
        session(tok2).flush()
        assert False, "Entry with duplicate unique key was inserted"
    except DuplicateKeyError:
        pass
    assert len(M.ApiToken.query.find().all()) == 1, "Duplicate entries with unique key found"
