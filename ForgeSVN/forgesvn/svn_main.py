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

#-*- python -*-
import logging
from pylons import tmpl_context as c
from pylons import request

# Non-stdlib imports
from ming.utils import LazyProperty
from ming.orm.ormsession import ThreadLocalORMSession
from tg import expose, redirect, validate, flash
from tg.decorators import with_trailing_slash, without_trailing_slash

# Pyforge-specific imports
import allura.tasks.repo_tasks
from allura.controllers import BaseController
from allura.controllers.repository import RepoRootController
from allura.lib.decorators import require_post
from allura.lib.repository import RepositoryApp, RepoAdminController
from allura.app import SitemapEntry, ConfigOption
from allura.lib import helpers as h
from allura import model as M

# Local imports
from . import model as SM
from . import version
from . import widgets
from .controllers import BranchBrowser
from .model.svn import svn_path_exists

log = logging.getLogger(__name__)

class ForgeSVNApp(RepositoryApp):
    '''This is the SVN app for PyForge'''
    __version__ = version.__version__
    config_options = RepositoryApp.config_options + [
        ConfigOption('checkout_url', str, '')
        ]
    tool_label='SVN'
    tool_description="""
        Enterprise-class centralized version control for the masses.
    """
    ordinal=4
    forkable=False
    default_branch_name='HEAD'

    def __init__(self, project, config):
        super(ForgeSVNApp, self).__init__(project, config)
        self.root = BranchBrowser()
        default_root = RepoRootController()
        self.root.refresh = default_root.refresh
        self.root.commit_browser = default_root.commit_browser
        self.root.commit_browser_data = default_root.commit_browser_data
        self.root.status = default_root.status
        self.admin = SVNRepoAdminController(self)

    @LazyProperty
    def repo(self):
        return SM.Repository.query.get(app_config_id=self.config._id)

    def install(self, project):
        '''Create repo object for this tool'''
        super(ForgeSVNApp, self).install(project)
        SM.Repository(
            name=self.config.options.mount_point,
            tool='svn',
            status='initializing',
            fs_path=self.config.options.get('fs_path'))
        ThreadLocalORMSession.flush_all()
        init_from_url = self.config.options.get('init_from_url')
        init_from_path = self.config.options.get('init_from_path')
        if init_from_url or init_from_path:
            allura.tasks.repo_tasks.clone.post(
                cloned_from_path=init_from_path,
                cloned_from_name=None,
                cloned_from_url=init_from_url)
        else:
            allura.tasks.repo_tasks.init.post()

    def admin_menu(self):
        links = []
        links.append(SitemapEntry(
                'Checkout URL',
                c.project.url()+'admin/'+self.config.options.mount_point+'/' + 'checkout_url',
                className='admin_modal'))
        links.append(SitemapEntry(
                'Import Repo',
                c.project.url()+'admin/'+self.config.options.mount_point+'/' + 'importer/'))
        links += super(ForgeSVNApp, self).admin_menu()
        return links

class SVNRepoAdminController(RepoAdminController):
    def __init__(self, app):
        super(SVNRepoAdminController, self).__init__(app)
        self.importer = SVNImportController(self.app)

    @without_trailing_slash
    @expose('jinja:forgesvn:templates/svn/checkout_url.html')
    def checkout_url(self, **kw):
        return dict(app=self.app,
                    allow_config=True,
                    checkout_url=self.app.config.options.get('checkout_url'))

    @without_trailing_slash
    @expose()
    @require_post()
    def set_checkout_url(self, **post_data):
        if svn_path_exists("file://%s%s/%s" %
                          (self.app.repo.fs_path,
                           self.app.repo.name,
                           post_data['checkout_url'])):
            self.app.config.options['checkout_url'] = post_data['checkout_url']
            flash("Checkout URL successfully changed")
        else:
            flash("%s is not a valid path for this repository" % post_data['checkout_url'], "error")


class SVNImportController(BaseController):
    import_form=widgets.ImportForm()

    def __init__(self, app):
        self.app = app

    @with_trailing_slash
    @expose('jinja:forgesvn:templates/svn/import.html')
    def index(self, **kw):
        c.form = self.import_form
        return dict()

    @without_trailing_slash
    @expose()
    @require_post()
    @validate(import_form, error_handler=index)
    def do_import(self, checkout_url=None, **kwargs):
        with h.push_context(
            self.app.config.project_id,
            app_config_id=self.app.config._id):
            allura.tasks.repo_tasks.reclone.post(
                cloned_from_path=None,
                cloned_from_name=None,
                cloned_from_url=checkout_url)
        M.Notification.post_user(
            c.user, self.app.repo, 'importing',
            text='''Repository import scheduled,
                   an email notification will be sent when complete.''')
        redirect(c.project.url() + 'admin/tools')
