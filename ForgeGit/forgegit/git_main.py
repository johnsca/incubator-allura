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

# Non-stdlib imports
from pylons import tmpl_context as c

from ming.utils import LazyProperty
from ming.orm.ormsession import ThreadLocalORMSession

# Pyforge-specific imports
import allura.tasks.repo_tasks
from allura.lib import helpers as h
from allura import model as M
from allura.controllers.repository import RepoRootController, RefsController, CommitsController
from allura.controllers.repository import MergeRequestsController, RepoRestController
from allura.lib.repository import RepositoryApp

# Local imports
from . import model as GM
from . import version
from .controllers import BranchBrowser

log = logging.getLogger(__name__)

class ForgeGitApp(RepositoryApp):
    '''This is the Git app for PyForge'''
    __version__ = version.__version__
    tool_label='Git'
    tool_description="""
        Git is a distributed version control system designed to
        handle everything from small to very large projects with speed
        and efficiency.
    """
    ordinal=2
    forkable=True
    default_branch_name='master'

    def __init__(self, project, config):
        super(ForgeGitApp, self).__init__(project, config)
        self.root = RepoRootController()
        self.api_root = RepoRestController()
        self.root.ref = RefsController(BranchBrowser)
        self.root.ci = CommitsController()
        setattr(self.root, 'merge-requests', MergeRequestsController())

    @LazyProperty
    def repo(self):
        return GM.Repository.query.get(app_config_id=self.config._id)

    def install(self, project):
        '''Create repo object for this tool'''
        super(ForgeGitApp, self).install(project)
        repo = GM.Repository(
            name=self.config.options.mount_point + '.git',
            tool='git',
            status='initializing',
            fs_path=self.config.options.get('fs_path'))
        ThreadLocalORMSession.flush_all()
        cloned_from_project_id = self.config.options.get('cloned_from_project_id')
        cloned_from_repo_id = self.config.options.get('cloned_from_repo_id')
        init_from_url = self.config.options.get('init_from_url')
        init_from_path = self.config.options.get('init_from_path')
        if cloned_from_project_id is not None:
            cloned_from = GM.Repository.query.get(_id=cloned_from_repo_id)
            allura.tasks.repo_tasks.clone.post(
                cloned_from_path=cloned_from.full_fs_path,
                cloned_from_name=cloned_from.app.config.script_name(),
                cloned_from_url=cloned_from.full_fs_path)
        elif init_from_url or init_from_path:
            allura.tasks.repo_tasks.clone.post(
                cloned_from_path=init_from_path,
                cloned_from_name=None,
                cloned_from_url=init_from_url)
        else:
            allura.tasks.repo_tasks.init.post()
