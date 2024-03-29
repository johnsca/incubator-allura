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
This module provides the security predicates used in decorating various models.
"""
import logging
from collections import defaultdict

from pylons import tmpl_context as c
from pylons import request
from webob import exc
from itertools import chain
from ming.utils import LazyProperty

from allura.lib.utils import TruthyCallable

log = logging.getLogger(__name__)

class Credentials(object):
    '''
    Role graph logic & caching
    '''

    def __init__(self):
        self.clear()

    @property
    def project_role(self):
        from allura import model as M
        db = M.session.main_doc_session.db
        return db[M.ProjectRole.__mongometa__.name]

    @classmethod
    def get(cls):
        'get the global Credentials instance'
        import allura
        return allura.credentials

    def clear(self):
        'clear cache'
        self.users = {}
        self.projects = {}

    def clear_user(self, user_id, project_id=None):
        if project_id == '*':
            to_remove = [(uid, pid) for uid, pid in self.users if uid == user_id]
        else:
            to_remove = [(user_id, project_id)]
        for uid, pid in to_remove:
            self.projects.pop(pid, None)
            self.users.pop((uid, pid), None)

    def load_user_roles(self, user_id, *project_ids):
        '''Load the credentials with all user roles for a set of projects'''
        # Don't reload roles
        project_ids = [ pid for pid in project_ids if self.users.get((user_id, pid)) is None ]
        if not project_ids: return
        if user_id is None:
            q = self.project_role.find({
                'project_id': {'$in': project_ids},
                'name': '*anonymous'})
        else:
            q0 = self.project_role.find({
                'project_id': {'$in': project_ids},
                'name': {'$in': ['*anonymous', '*authenticated']}})
            q1 = self.project_role.find({
                'project_id': {'$in': project_ids},
                'user_id': user_id})
            q = chain(q0, q1)
        roles_by_project = dict((pid, []) for pid in project_ids)
        for role in q:
            roles_by_project[role['project_id']].append(role)
        for pid, roles in roles_by_project.iteritems():
            self.users[user_id, pid] = RoleCache(self, roles)

    def load_project_roles(self, *project_ids):
        '''Load the credentials with all user roles for a set of projects'''
        # Don't reload roles
        project_ids = [ pid for pid in project_ids if self.projects.get(pid) is None ]
        if not project_ids: return
        q = self.project_role.find({
            'project_id': {'$in': project_ids}})
        roles_by_project = dict((pid, []) for pid in project_ids)
        for role in q:
            roles_by_project[role['project_id']].append(role)
        for pid, roles in roles_by_project.iteritems():
            self.projects[pid] = RoleCache(self, roles)

    def project_roles(self, project_id):
        '''
        :returns: a RoleCache of ProjectRoles for project_id
        '''
        roles = self.projects.get(project_id)
        if roles is None:
            self.load_project_roles(project_id)
            roles = self.projects[project_id]
        return roles

    def user_roles(self, user_id, project_id=None):
        '''
        :returns: a RoleCache of ProjectRoles for given user_id and project_id, *anonymous and *authenticated checked as appropriate
        '''
        roles = self.users.get((user_id, project_id))
        if roles is None:
            if project_id is None:
                if user_id is None:
                    q = []
                else:
                    q = self.project_role.find({'user_id': user_id})
                roles = RoleCache(self, q)
            else:
                self.load_user_roles(user_id, project_id)
                roles = self.users.get((user_id, project_id))
            self.users[user_id, project_id] = roles
        return roles

    def user_has_any_role(self, user_id, project_id, role_ids):
        user_roles = self.user_roles(user_id=user_id, project_id=project_id)
        return bool(set(role_ids)  & user_roles.reaching_ids_set)

    def users_with_named_role(self, project_id, name):
        """ returns in sorted order """
        roles = self.project_roles(project_id)
        return sorted(RoleCache(self, roles.find(name=name)).users_that_reach, key=lambda u:u.username)

    def userids_with_named_role(self, project_id, name):
        roles = self.project_roles(project_id)
        return RoleCache(self, roles.find(name=name)).userids_that_reach

class RoleCache(object):

    def __init__(self, cred, q):
        self.cred = cred
        self.q = q

    def find(self, **kw):
        tests = kw.items()
        def _iter():
            for r in self:
                for k,v in tests:
                    val = r.get(k)
                    if callable(v):
                        if not v(val): break
                    elif v != val: break
                else:
                    yield r
        return RoleCache(self.cred, _iter())

    def get(self, **kw):
        for x in self.find(**kw): return x
        return None

    def __iter__(self):
        return self.index.itervalues()

    def __len__(self):
        return len(self.index)

    @LazyProperty
    def index(self):
        return dict((r['_id'], r) for r in self.q)

    @LazyProperty
    def named(self):
        return RoleCache(self.cred, (
            r for r in self
            if r.get('name') and not r.get('name').startswith('*')))

    @LazyProperty
    def reverse_index(self):
        rev_index = defaultdict(list)
        for r in self:
            for rr_id in r['roles']:
                rev_index[rr_id].append(r)
        return rev_index

    @LazyProperty
    def roles_that_reach(self):
        def _iter():
            visited = set()
            to_visit = list(self)
            while to_visit:
                r = to_visit.pop(0)
                if r['_id'] in visited: continue
                visited.add(r['_id'])
                yield r
                pr_rindex = self.cred.project_roles(r['project_id']).reverse_index
                to_visit += pr_rindex[r['_id']]
        return RoleCache(self.cred, _iter())

    @LazyProperty
    def users_that_reach(self):
        from allura import model as M
        uids = [uid for uid in self.userids_that_reach if uid]
        return M.User.query.find({'_id': {'$in': uids}})

    @LazyProperty
    def userids_that_reach(self):
        return [ r['user_id'] for r in self.roles_that_reach ]

    @LazyProperty
    def reaching_roles(self):
        def _iter():
            to_visit = self.index.items()
            visited = set()
            while to_visit:
                (rid, role) = to_visit.pop()
                if rid in visited: continue
                yield role
                pr_index = self.cred.project_roles(role['project_id']).index
                if rid in pr_index:
                    for i in pr_index[rid]['roles']:
                        if i in pr_index:
                            to_visit.append((i, pr_index[i]))
        return RoleCache(self.cred, _iter())

    @LazyProperty
    def reaching_ids(self):
        return [ r['_id'] for r in self.reaching_roles ]

    @LazyProperty
    def reaching_ids_set(self):
        return set(self.reaching_ids)

def has_access(obj, permission, user=None, project=None):
    '''Return whether the given user has the permission name on the given object.

    - First, all the roles for a user in the given project context are computed.

    - Next, for each role, the given object's ACL is examined linearly. If an ACE
      is found which matches the permission and user, and that ACE ALLOWs access,
      then the function returns True and access is permitted. If the ACE DENYs
      access, then that role is removed from further consideration.

    - If the obj is not a Neighborhood and the given user has the 'admin'
      permission on the current neighborhood, then the function returns True and
      access is allowed.

    - If the obj is not a Project and the given user has the 'admin'
      permission on the current project, then the function returns True and
      access is allowed.

    - If none of the ACEs on the object ALLOW access, and there are no more roles
      to be considered, then the function returns False and access is denied.

    - Processing continues using the remaining roles and the
      obj.parent_security_context(). If the parent_security_context is None, then
      the function returns False and access is denied.

    The effect of this processing is that if *any* role for the user is ALLOWed
    access via a linear traversal of the ACLs, then access is allowed. All of the
    users roles must either be explicitly DENYed or processing terminate with no
    matches to DENY access to the resource.
    '''
    from allura import model as M
    def predicate(obj=obj, user=user, project=project, roles=None):
        if obj is None:
            return False
        if roles is None:
            if user is None: user = c.user
            assert user, 'c.user should always be at least M.User.anonymous()'
            cred = Credentials.get()
            if project is None:
                if isinstance(obj, M.Neighborhood):
                    project = obj.neighborhood_project
                    if project is None:
                        log.error('Neighborhood project missing for %s', obj)
                        return False
                elif isinstance(obj, M.Project):
                    project = obj.root_project
                else:
                    project = c.project.root_project
            roles = cred.user_roles(user_id=user._id, project_id=project._id).reaching_ids
        chainable_roles = []
        for rid in roles:
            for ace in obj.acl:
                if M.ACE.match(ace, rid, permission):
                    if ace.access == M.ACE.ALLOW:
                        # access is allowed
                        # log.info('%s: True', txt)
                        return True
                    else:
                        # access is denied for this role
                        break
            else:
                # access neither allowed or denied, may chain to parent context
                chainable_roles.append(rid)
        parent = obj.parent_security_context()
        if parent and chainable_roles:
            result = has_access(parent, permission, user=user, project=project)(
                roles=tuple(chainable_roles))
        elif not isinstance(obj, M.Neighborhood):
            result = has_access(project.neighborhood, 'admin', user=user)()
            if not (result or isinstance(obj, M.Project)):
                result = has_access(project, 'admin', user=user)()
        else:
            result = False
        # log.info('%s: %s', txt, result)
        return result
    return TruthyCallable(predicate)

def all_allowed(obj, user_or_role=None, project=None):
    '''
    List all the permission names that a given user or named role
    is allowed for a given object.  This list reflects the permissions
    for which has_access() would return True for the user (or a user
    in the given named role, e.g. Developer).

    Example:

        Given a tracker with the following ACL (pseudo-code):
            [
                ACE.allow(ProjectRole.by_name('Developer'), 'create'),
                ACE.allow(ProjectRole.by_name('Member'), 'post'),
                ACE.allow(ProjectRole.by_name('*anonymous'), 'read'),
            ]

        And user1 is in the Member group, then all_allowed(tracker, user1)
        will return:

            set(['post', 'read'])

        And all_allowed(tracker, ProjectRole.by_name('Developer')) will return:

            set(['create', 'post', 'read'])
    '''
    from allura import model as M
    anon = M.ProjectRole.anonymous(project)
    auth = M.ProjectRole.authenticated(project)
    if user_or_role is None:
        user_or_role = c.user
    if user_or_role is None:
        user_or_role = anon
    if isinstance(user_or_role, M.User):
        user_or_role = M.ProjectRole.by_user(user_or_role, project)
        if user_or_role is None:
            user_or_role = auth  # user is not member of project, treat as auth
    roles = [user_or_role]
    if user_or_role == anon:
        pass  # anon inherits nothing
    elif user_or_role == auth:
        roles += [anon]  # auth inherits from anon
    else:
        roles += [auth, anon]  # named group or user inherits from auth + anon
    role_ids = RoleCache(Credentials.get(), roles).reaching_ids  # match rules applicable to us
    perms = set()
    denied = defaultdict(set)
    while obj:  # traverse parent contexts
        for role_id in role_ids:
            for ace in obj.acl:
                if ace.permission in denied[role_id]:
                    # don't consider permissions that were denied for this role
                    continue
                if M.ACE.match(ace, role_id, ace.permission):
                    if ace.access == M.ACE.ALLOW:
                        perms.add(ace.permission)
                    else:
                        # explicit DENY overrides any ALLOW for this permission
                        # for this role_id in this ACL or parent(s) (but an ALLOW
                        # for a different role could still grant this permission)
                        denied[role_id].add(ace.permission)
        obj = obj.parent_security_context()
    if M.ALL_PERMISSIONS in perms:
        return set([M.ALL_PERMISSIONS])
    return perms

def require(predicate, message=None):
    '''
    Example: require(has_access(c.app, 'read'))

    :param callable predicate: truth function to call
    :param str message: message to show upon failure
    :raises: HTTPForbidden or HTTPUnauthorized
    '''

    from allura import model as M
    if predicate(): return
    if not message:
        message = """You don't have permission to do that.
                     You must ask a project administrator for rights to perform this task.
                     Please click the back button to return to the previous page."""
    if c.user != M.User.anonymous():
        request.environ['error_message'] = message
        raise exc.HTTPForbidden(detail=message)
    else:
        raise exc.HTTPUnauthorized()

def require_access(obj, permission, **kwargs):
    if obj is not None:
        predicate = has_access(obj, permission, **kwargs)
        return require(predicate, message='%s access required' % permission.capitalize())
    else:
        raise exc.HTTPForbidden(detail="Could not verify permissions for this page.")

def require_authenticated():
    '''
    :raises: HTTPUnauthorized if current user is anonymous
    '''
    from allura import model as M
    if c.user == M.User.anonymous():
        raise exc.HTTPUnauthorized()

def simple_grant(acl, role_id, permission):
    from allura.model.types import ACE
    for ace in acl:
        if ace.role_id == role_id and ace.permission == permission: return
    acl.append(ACE.allow(role_id, permission))

def simple_revoke(acl, role_id, permission):
    remove = []
    for i, ace in enumerate(acl):
        if ace.role_id == role_id and ace.permission == permission:
            remove.append(i)
    for i in reversed(remove):
        acl.pop(i)
