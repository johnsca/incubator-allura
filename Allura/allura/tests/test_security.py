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

from pylons import tmpl_context as c
from nose.tools import assert_equal

from ming.odm import ThreadLocalODMSession
from allura.tests import decorators as td
from allura.tests import TestController

from allura.lib.security import Credentials, all_allowed, has_access
from allura import model as M
from forgewiki import model as WM


def _deny(obj, role, perm):
    obj.acl.insert(0, M.ACE.deny(role._id, perm))
    ThreadLocalODMSession.flush_all()
    Credentials.get().clear()

def _add_to_group(user, role):
    user.project_role().roles.append(role._id)
    ThreadLocalODMSession.flush_all()
    Credentials.get().clear()

class TestSecurity(TestController):

    validate_skip = True

    @td.with_wiki
    def test_anon(self):
        self.app.get('/security/*anonymous/forbidden', status=302)
        self.app.get('/security/*anonymous/needs_auth', status=302)
        self.app.get('/security/*anonymous/needs_project_access_fail', status=302)
        self.app.get('/security/*anonymous/needs_artifact_access_fail', status=302)

    @td.with_wiki
    def test_auth(self):
        self.app.get('/security/test-admin/forbidden', status=403)
        self.app.get('/security/test-admin/needs_auth', status=200)
        self.app.get('/security/test-admin/needs_project_access_fail', status=403)
        self.app.get('/security/test-admin/needs_project_access_ok', status=200)
        # This should fail b/c test-user doesn't have the permission
        self.app.get('/security/test-user/needs_artifact_access_fail', extra_environ=dict(username='test-user'), status=403)
        # This should succeed b/c users with the 'admin' permission on a
        # project implicitly have all permissions to everything in the project
        self.app.get('/security/test-admin/needs_artifact_access_fail', status=200)
        self.app.get('/security/test-admin/needs_artifact_access_ok', status=200)

    @td.with_wiki
    def test_all_allowed(self):
        wiki = c.project.app_instance('wiki')
        page = WM.Page.query.get(app_config_id=wiki.config._id)
        admin_role = M.ProjectRole.by_name('Admin')
        dev_role = M.ProjectRole.by_name('Developer')
        member_role = M.ProjectRole.by_name('Member')
        auth_role = M.ProjectRole.by_name('*authenticated')
        anon_role = M.ProjectRole.by_name('*anonymous')
        test_user = M.User.by_username('test-user')

        assert_equal(all_allowed(wiki, admin_role), set(['configure', 'read', 'create', 'edit', 'unmoderated_post', 'post', 'moderate', 'admin', 'delete']))
        assert_equal(all_allowed(wiki, dev_role), set(['read', 'create', 'edit', 'unmoderated_post', 'post', 'moderate', 'delete']))
        assert_equal(all_allowed(wiki, member_role), set(['read', 'create', 'edit', 'unmoderated_post', 'post']))
        assert_equal(all_allowed(wiki, auth_role), set(['read', 'post', 'unmoderated_post']))
        assert_equal(all_allowed(wiki, anon_role), set(['read']))
        assert_equal(all_allowed(wiki, test_user), set(['read', 'post', 'unmoderated_post']))

        _add_to_group(test_user, member_role)

        assert_equal(all_allowed(wiki, test_user), set(['read', 'create', 'edit', 'unmoderated_post', 'post']))

        _deny(wiki, auth_role, 'unmoderated_post')

        assert_equal(all_allowed(wiki, member_role), set(['read', 'create', 'edit', 'post']))
        assert_equal(all_allowed(wiki, test_user), set(['read', 'create', 'edit', 'post']))

    @td.with_wiki
    def test_weird_allow_vs_deny(self):
        '''
        Test weird interaction of DENYs and ALLOWs in has_access.
        '''
        wiki = c.project.app_instance('wiki')
        page = WM.Page.query.get(app_config_id=wiki.config._id)
        auth_role = M.ProjectRole.by_name('*authenticated')
        test_user = M.User.by_username('test-user')


        # DENY for auth_role on page prevents chaining of auth_role for 'read'
        # but anon_role still chains so ALLOW read for anon_role on wiki applies
        # and authed user can still read.  'post' and 'unmoderated_post' don't
        # match DENY rule so they chain as normal.
        #
        # This behavior seems wrong and should probably be fixed at some point,
        # but this test is here to confirm that all_allowed matches has_access.
        assert has_access(page, 'read', test_user)()
        assert has_access(page, 'post', test_user)()
        assert has_access(page, 'unmoderated_post', test_user)()
        assert_equal(all_allowed(page, test_user), set(['read', 'post', 'unmoderated_post']))

        _deny(page, auth_role, 'read')

        assert has_access(page, 'read', test_user)()
        assert has_access(page, 'post', test_user)()
        assert has_access(page, 'unmoderated_post', test_user)()
        assert_equal(all_allowed(page, test_user), set(['read', 'post', 'unmoderated_post']))


        # Same thing applies to ALLOW vs DENY on the same ACL;
        # an ALLOW on any applicable role overrides a DENY on any other.
        #
        # In this case it's reasonable since you might want to DENY read for
        # *anon but ALLOW it for *auth.  *anon ALLOW overriding *auth DENY is
        # just an unfortunate side-effect of not having a true heiarchy of roles.
        assert has_access(wiki, 'read', test_user)()
        assert has_access(wiki, 'post', test_user)()
        assert has_access(wiki, 'unmoderated_post', test_user)()
        assert_equal(all_allowed(wiki, test_user), set(['read', 'post', 'unmoderated_post']))

        _deny(wiki, auth_role, 'read')

        assert has_access(wiki, 'read', test_user)()
        assert has_access(wiki, 'post', test_user)()
        assert has_access(wiki, 'unmoderated_post', test_user)()
        assert_equal(all_allowed(wiki, test_user), set(['read', 'post', 'unmoderated_post']))
