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

import logging
import shutil
from urllib import quote

from pylons import tmpl_context as c, app_globals as g
from tg import expose, redirect, url
from tg.decorators import with_trailing_slash, without_trailing_slash
from bson import ObjectId

from ming.utils import LazyProperty

import allura.tasks
from allura import version
from allura.lib import helpers as h
from allura import model as M
from allura.lib import security
from allura.lib.decorators import require_post
from allura.lib.security import has_access
from allura.app import Application, SitemapEntry, DefaultAdminController, ConfigOption

log = logging.getLogger(__name__)


class RepositoryApp(Application):
    END_OF_REF_ESCAPE='~'
    __version__ = version.__version__
    permissions = [
        'read', 'write', 'create',
        'unmoderated_post', 'post', 'moderate', 'admin',
        'configure']
    config_options = Application.config_options + [
        ConfigOption('cloned_from_project_id', ObjectId, None),
        ConfigOption('cloned_from_repo_id', ObjectId, None),
        ConfigOption('init_from_url', str, None)
        ]
    tool_label='Repository'
    default_mount_label='Code'
    default_mount_point='code'
    relaxed_mount_points=True
    ordinal=2
    forkable=False
    default_branch_name=None # master or default or some such
    repo=None # override with a property in child class
    icons={
        24:'images/code_24.png',
        32:'images/code_32.png',
        48:'images/code_48.png'
    }

    def __init__(self, project, config):
        Application.__init__(self, project, config)
        self.admin = RepoAdminController(self)

    def main_menu(self):
        '''Apps should provide their entries to be added to the main nav
        :return: a list of :class:`SitemapEntries <allura.app.SitemapEntry>`
        '''
        return [ SitemapEntry(
                self.config.options.mount_label,
                '.')]

    @property
    @h.exceptionless([], log)
    def sitemap(self):
        menu_id = self.config.options.mount_label
        with h.push_config(c, app=self):
            return [
                SitemapEntry(menu_id, '.')[self.sidebar_menu()] ]

    def admin_menu(self):
        admin_url = c.project.url()+'admin/'+self.config.options.mount_point+'/'
        links = [SitemapEntry('Viewable Files', admin_url + 'extensions', className='admin_modal')]
        links.append(SitemapEntry('Refresh Repository',
                                  c.project.url() + self.config.options.mount_point + '/refresh',
                                  ))
        links += super(RepositoryApp, self).admin_menu()
        [links.remove(l) for l in links[:] if l.label == 'Options']
        return links

    @h.exceptionless([], log)
    def sidebar_menu(self):
        if not self.repo or self.repo.status != 'ready':
            return []
        links = [SitemapEntry('Browse Commits', c.app.url + 'commit_browser', ui_icon=g.icons['folder'])]
        if self.forkable and self.repo.status == 'ready':
            links.append(SitemapEntry('Fork', c.app.url + 'fork', ui_icon=g.icons['fork']))
        merge_request_count = self.repo.merge_requests_by_statuses('open').count()
        if merge_request_count:
            links += [
                SitemapEntry(
                    'Merge Requests', c.app.url + 'merge-requests/',
                    small=merge_request_count) ]
        if self.repo.forks:
            links += [
                SitemapEntry('Forks', c.app.url + 'forks/', small=len(self.repo.forks))
            ]
        if self.repo.upstream_repo.name:
            repo_path_parts = self.repo.upstream_repo.name.strip('/').split('/')
            links += [
                SitemapEntry('Clone of'),
                SitemapEntry('%s / %s' %
                    (repo_path_parts[1], repo_path_parts[-1]),
                    self.repo.upstream_repo.name)
                ]
            if len(c.app.repo.branches) and has_access(c.app.repo, 'admin'):
                links.append(SitemapEntry('Request Merge', c.app.url + 'request_merge',
                             ui_icon=g.icons['merge'],
                             ))
            pending_upstream_merges = self.repo.pending_upstream_merges()
            if pending_upstream_merges:
                links.append(SitemapEntry(
                        'Pending Merges',
                        self.repo.upstream_repo.name + 'merge-requests/',
                        small=pending_upstream_merges))
        ref_url = self.repo.url_for_commit(self.default_branch_name, url_type='ref')
        if self.repo.branches:
            links.append(SitemapEntry('Branches'))
            max_branches = 10
            for b in self.repo.branches[:max_branches]:
                links.append(SitemapEntry(
                        b.name,
                        quote(self.repo.url_for_commit(b.name) + 'tree/'),
                        small=b.count))
            if len(self.repo.branches) > max_branches:
                links.append(
                    SitemapEntry(
                        'More Branches',
                        ref_url + 'branches/',
                        ))
        if self.repo.repo_tags:
            links.append(SitemapEntry('Tags'))
            max_tags = 10
            for b in self.repo.repo_tags[:max_tags]:
                links.append(SitemapEntry(
                        b.name,
                        quote(self.repo.url_for_commit(b.name) + 'tree/'),
                        small=b.count))
            if len(self.repo.repo_tags) > max_tags:
                links.append(
                    SitemapEntry(
                        'More Tags',
                        ref_url + 'tags/',
                        ))
        return links

    def install(self, project):
        self.config.options['project_name'] = project.name
        super(RepositoryApp, self).install(project)
        role_admin = M.ProjectRole.by_name('Admin')._id
        role_developer = M.ProjectRole.by_name('Developer')._id
        role_auth = M.ProjectRole.authenticated()._id
        role_anon = M.ProjectRole.anonymous()._id
        self.config.acl = [
            M.ACE.allow(role_anon, 'read'),
            M.ACE.allow(role_auth, 'post'),
            M.ACE.allow(role_auth, 'unmoderated_post'),
            M.ACE.allow(role_developer, 'create'),
            M.ACE.allow(role_developer, 'write'),
            M.ACE.allow(role_developer, 'moderate'),
            M.ACE.allow(role_admin, 'configure'),
            M.ACE.allow(role_admin, 'admin'),
            ]

    def uninstall(self, project):
        allura.tasks.repo_tasks.uninstall.post()

class RepoAdminController(DefaultAdminController):

    def __init__(self, app):
        self.app = app

    @LazyProperty
    def repo(self):
        return self.app.repo

    def _check_security(self):
        security.require_access(self.app, 'configure')

    @with_trailing_slash
    @expose()
    def index(self, **kw):
        redirect('extensions')

    @without_trailing_slash
    @expose('jinja:allura:templates/repo/admin_extensions.html')
    def extensions(self, **kw):
        return dict(app=self.app,
                    allow_config=True,
                    additional_viewable_extensions=getattr(self.repo, 'additional_viewable_extensions', ''))

    @without_trailing_slash
    @expose()
    @require_post()
    def set_extensions(self, **post_data):
        self.repo.additional_viewable_extensions = post_data['additional_viewable_extensions']
