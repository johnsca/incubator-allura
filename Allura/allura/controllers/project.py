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

import re
import logging
from datetime import datetime, timedelta
from urllib import unquote
from itertools import chain, islice

from bson import ObjectId
from tg import expose, flash, redirect, validate, request, response, config
from tg.decorators import with_trailing_slash, without_trailing_slash
from pylons import tmpl_context as c, app_globals as g
from paste.deploy.converters import asbool
from webob import exc
import pymongo
from formencode import validators
from formencode.api import Invalid

from ming.utils import LazyProperty

from allura import model as M
from allura.app import SitemapEntry
from allura.lib.base import WsgiDispatchController
from allura.lib import helpers as h
from allura.lib import utils
from allura.lib.decorators import require_post
from allura.controllers.error import ErrorController
from allura.controllers.feed import FeedArgs, FeedController
from allura.lib.security import require_access, has_access
from allura.lib.security import RoleCache
from allura.lib.widgets import forms as ff
from allura.lib.widgets import form_fields as ffw
from allura.lib.widgets import project_list as plw
from allura.lib import plugin, exceptions
from .auth import AuthController
from .search import SearchController, ProjectBrowseController
from .static import NewForgeController

log = logging.getLogger(__name__)

class W:
    resize_editor = ffw.AutoResizeTextarea()
    project_summary = plw.ProjectSummary()
    add_project = plugin.ProjectRegistrationProvider.get().add_project_widget(antispam=True)
    page_list = ffw.PageList()
    page_size = ffw.PageSize()
    project_select = ffw.NeighborhoodProjectSelect
    neighborhood_overview_form = ff.NeighborhoodOverviewForm()

class NeighborhoodController(object):
    '''Manages a neighborhood of projects.
    '''

    def __init__(self, neighborhood):
        self.neighborhood = neighborhood
        self.neighborhood_name = self.neighborhood.name
        self.prefix = self.neighborhood.shortname_prefix
        self.browse = NeighborhoodProjectBrowseController(neighborhood=self.neighborhood)
        self._admin = NeighborhoodAdminController(self.neighborhood)
        self._moderate = NeighborhoodModerateController(self.neighborhood)

    def _check_security(self):
        require_access(self.neighborhood, 'read')

    @expose()
    def _lookup(self, pname, *remainder):
        pname = unquote(pname)
        if not h.re_project_name.match(pname):
            raise exc.HTTPNotFound, pname
        project = M.Project.query.get(shortname=self.prefix + pname, neighborhood_id=self.neighborhood._id)
        if project is None and self.prefix == 'u/':
            # create user-project if it is missing
            user = M.User.query.get(username=pname, disabled=False)
            if user:
                project = self.neighborhood.register_project(
                    plugin.AuthenticationProvider.get(request).user_project_shortname(user),
                    user=user, user_project=True)
        if project is None:
            # look for neighborhood tools matching the URL
            project = self.neighborhood.neighborhood_project
            c.project = project
            return ProjectController()._lookup(pname, *remainder)
        if project and self.prefix == 'u/':
            # make sure user-projects are associated with an enabled user
            user = project.user_project_of
            if not user or user.disabled:
                raise exc.HTTPNotFound
        if project.database_configured == False:
            if remainder == ('user_icon',):
                redirect(g.forge_static('images/user.png'))
            elif c.user.username == pname:
                log.info('Configuring %s database for access to %r',
                         pname, remainder)
                project.configure_project(is_user_project=True)
            else:
                raise exc.HTTPNotFound, pname
        c.project = project
        if project is None or (project.deleted and not has_access(c.project, 'update')()):
            raise exc.HTTPNotFound, pname
        if project.neighborhood.name != self.neighborhood_name:
            redirect(project.url())
        return ProjectController(), remainder

    @expose('jinja:allura:templates/neighborhood_project_list.html')
    @with_trailing_slash
    def index(self, sort='alpha', limit=25, page=0, **kw):
        c.project = self.neighborhood.neighborhood_project
        if self.neighborhood.redirect:
            redirect(self.neighborhood.redirect)
        if not self.neighborhood.has_home_tool:
            mount = c.project.ordered_mounts()[0]
            if mount is not None:
                if 'ac' in mount:
                    redirect(mount['ac'].options.mount_point + '/')
                elif 'sub' in mount:
                    redirect(mount['sub'].url())
            else:
                redirect(c.project.app_configs[0].options.mount_point + '/')
        c.project_summary = W.project_summary
        c.page_list = W.page_list
        limit, page, start = g.handle_paging(limit, page)
        pq = M.Project.query.find(dict(
                neighborhood_id=self.neighborhood._id,
                deleted=False,
                is_nbhd_project=False,
                ))
        if sort == 'alpha':
            pq.sort('name')
        else:
            pq.sort('last_updated', pymongo.DESCENDING)
        count = pq.count()
        nb_max_projects = self.neighborhood.get_max_projects()
        projects = pq.skip(start).limit(int(limit)).all()
        categories = M.ProjectCategory.query.find({'parent_id':None}).sort('name').all()
        c.custom_sidebar_menu = []
        if h.has_access(self.neighborhood, 'register')() and (nb_max_projects is None or count < nb_max_projects):
            c.custom_sidebar_menu += [
                SitemapEntry('Add a Project', self.neighborhood.url() + 'add_project', ui_icon=g.icons['plus']),
                SitemapEntry('')
            ]
        c.custom_sidebar_menu = c.custom_sidebar_menu + [
            SitemapEntry(cat.label, self.neighborhood.url() + 'browse/' + cat.name) for cat in categories
        ]
        return dict(neighborhood=self.neighborhood,
                    title="Welcome to " + self.neighborhood.name,
                    text=g.markdown.convert(self.neighborhood.homepage),
                    projects=projects,
                    sort=sort,
                    limit=limit, page=page, count=count)

    @expose('jinja:allura:templates/neighborhood_add_project.html')
    @without_trailing_slash
    def add_project(self, **form_data):
        c.project = self.neighborhood.neighborhood_project
        require_access(self.neighborhood, 'register')
        c.add_project = W.add_project
        form_data.setdefault('tools', [u'Wiki',u'Git',u'Tickets',u'Discussion'])
        form_data['neighborhood'] = self.neighborhood.name
        return dict(neighborhood=self.neighborhood, form_data=form_data)

    @expose('json:')
    def suggest_name(self, project_name=''):
        result = dict()
        result['suggested_name'] = re.sub("[^A-Za-z0-9]", "", project_name).lower()[:15]
        return result

    @expose('json:')
    def check_names(self, project_name='', unix_name=''):
        result = dict()
        try:
            W.add_project.fields['project_name'].validate(project_name, '')
        except Invalid as e:
            result['name_message'] = str(e)
        if not h.re_project_name.match(unix_name) or not (3 <= len(unix_name) <= 15):
            result['unixname_message'] = 'Please use only letters, numbers, and dashes 3-15 characters long.'
        else:
            result['unixname_message'] = plugin.ProjectRegistrationProvider.get().name_taken(unix_name, self.neighborhood)
        return result

    @h.vardec
    @expose()
    @validate(W.add_project, error_handler=add_project)
    @utils.AntiSpam.validate('Spambot protection engaged')
    @require_post()
    def register(self, project_unixname=None, project_description=None, project_name=None, neighborhood=None,
                 private_project=None, tools=None, **kw):
        require_access(self.neighborhood, 'register')
        if private_project:
            require_access(self.neighborhood, 'admin')
        neighborhood = M.Neighborhood.query.get(name=neighborhood)

        project_description = h.really_unicode(project_description or '').encode('utf-8')
        project_name = h.really_unicode(project_name or '').encode('utf-8')
        project_unixname = h.really_unicode(project_unixname or '').encode('utf-8').lower()
        try:
            c.project = neighborhood.register_project(project_unixname,
                    project_name=project_name, private_project=private_project)
        except exceptions.ProjectOverlimitError:
            flash("You have exceeded the maximum number of projects you are allowed to create", 'error')
            redirect('add_project')
        except exceptions.ProjectRatelimitError:
            flash("Project creation rate limit exceeded.  Please try again later.", 'error')
            redirect('add_project')
        except Exception as e:
            log.error('error registering project: %s', project_unixname, exc_info=True)
            flash('Internal Error. Please try again later.', 'error')
            redirect('add_project')

        if project_description:
            c.project.short_description = project_description
        offset = c.project.next_mount_point(include_hidden=True)
        if tools and not neighborhood.project_template:
            anchored_tools = neighborhood.get_anchored_tools()
            for i, tool in enumerate(tools):
                if (tool.lower() not in anchored_tools.keys()) and (c.project.app_instance(tool) is None):
                    c.project.install_app(tool, ordinal=i + offset)
        flash('Welcome to the SourceForge Project System! '
              'To get started, fill out some information about your project.')
        redirect(c.project.script_name + 'admin/overview')

    @expose()
    def icon(self, **kw):
        icon = self.neighborhood.icon
        if not icon:
            raise exc.HTTPNotFound
        return icon.serve()

    @expose('json:')
    def users(self):
        p = self.neighborhood.neighborhood_project
        return {
            'options': [{
                'value': u.username,
                'label': '%s (%s)' % (u.display_name, u.username)
            } for u in p.users()]
        }


class NeighborhoodProjectBrowseController(ProjectBrowseController):
    def __init__(self, neighborhood=None, category_name=None, parent_category=None):
        self.neighborhood = neighborhood
        super(NeighborhoodProjectBrowseController, self).__init__(category_name=category_name, parent_category=parent_category)
        self.nav_stub = '%sbrowse/' % self.neighborhood.url()
        self.additional_filters = {'neighborhood_id':self.neighborhood._id}

    @expose()
    def _lookup(self, category_name, *remainder):
        c.project = self.neighborhood.neighborhood_project
        category_name = unquote(category_name)
        return NeighborhoodProjectBrowseController(neighborhood=self.neighborhood, category_name=category_name, parent_category=self.category), remainder

    @expose('jinja:allura:templates/neighborhood_project_list.html')
    @without_trailing_slash
    def index(self, sort='alpha', limit=25, page=0, **kw):
        c.project_summary = W.project_summary
        c.page_list = W.page_list
        limit, page, start = g.handle_paging(limit, page)
        projects, count = self._find_projects(sort=sort, limit=limit, start=start)
        title = self._build_title()
        c.custom_sidebar_menu = self._build_nav()
        return dict(projects=projects,
                    title=title,
                    text=None,
                    neighborhood=self.neighborhood,
                    sort=sort,
                    limit=limit, page=page, count=count)

class HostNeighborhoodController(WsgiDispatchController, NeighborhoodController):
    '''Neighborhood controller with support for use as a root controller, for
    instance, when using adobe.sourceforge.net (if this is allowed).
    '''

    auth = AuthController()
    error = ErrorController()
    nf = NewForgeController()
    search = SearchController()

class ToolListController(object):
    """Renders a list of all tools of a given type in the current project."""

    @expose('jinja:allura:templates/tool_list.html')
    def _default(self, tool_name, *args, **kw):
        tool_name = tool_name.lower()
        entries = [e for e in c.project.sitemap()
                if e.tool_name and e.tool_name.lower() == tool_name]
        return dict(entries=entries, type=g.entry_points['tool'][tool_name].tool_label if entries else None)

class ProjectController(FeedController):

    def __init__(self):
        setattr(self, '_nav.json', self._nav)
        self.screenshot = ScreenshotsController()
        self._list = ToolListController()

    @expose('json:')
    def _nav(self):
        menu = []
        for s in c.project.grouped_navbar_entries():
            entry = dict(name=s.label, url=s.url, icon=s.ui_icon, tool_name=s.tool_name)
            if s.children:
                entry['children'] = [dict(name=child.label, url=child.url, icon=child.ui_icon, tool_name=child.tool_name) 
                                    for child in s.children]
            menu.append(entry)
        return dict(menu=menu)

    @expose()
    def _lookup(self, name, *remainder):
        name = unquote(name)
        subproject = M.Project.query.get(shortname=c.project.shortname + '/' + name,
                                         neighborhood_id=c.project.neighborhood_id)
        if subproject:
            c.project = subproject
            c.app = None
            return ProjectController(), remainder
        app = c.project.app_instance(name)
        if app is None:
            raise exc.HTTPNotFound, name
        c.app = app
        if not app.root:
            raise exc.HTTPNotFound, name

        return app.root, remainder

    @expose('jinja:allura:templates/members.html')
    @with_trailing_slash
    def _members(self, **kw):
        users = []
        for user in c.project.users():
            roles = M.ProjectRole.query.find({'_id': {'$in': user.project_role().roles}})
            roles = set([r.name for r in roles])
            users.append(dict(
                display_name=user.display_name,
                username=user.username,
                url=user.url(),
                roles=roles,
                ))
        return dict(users=users)

    def _check_security(self):
        require_access(c.project, 'read')

    @expose()
    @with_trailing_slash
    def index(self, **kw):
        mount = c.project.first_mount_visible(c.user)
        activity_enabled = config.get('activitystream.enabled', False)
        activity_enabled = request.cookies.get('activitystream.enabled', activity_enabled)
        activity_enabled = asbool(activity_enabled)
        if activity_enabled and c.project.app_instance('activity'):
            redirect('activity/')
        elif mount is not None:
            if 'ac' in mount:
                redirect(mount['ac'].options.mount_point + '/')
            elif 'sub' in mount:
                redirect(mount['sub'].url())
        else:
            redirect(c.project.app_configs[0].options.mount_point + '/')

    def get_feed(self, project, app, user):
        """Return a :class:`allura.controllers.feed.FeedArgs` object describing
        the xml feed for this controller.

        Overrides :meth:`allura.controllers.feed.FeedController.get_feed`.

        """
        return FeedArgs(
            dict(project_id=project._id),
            'Recent changes to Project %s' % project.name,
            project.url())

    @expose()
    def icon(self, **kw):
        icon = c.project.icon
        if not icon:
            raise exc.HTTPNotFound
        return icon.serve()

    @expose()
    def user_icon(self, **kw):
        try:
            return self.icon()
        except exc.HTTPNotFound:
            redirect(g.forge_static('images/user.png'))

    @expose('json:')
    def user_search(self, term=''):
        if len(term) < 3:
            raise exc.HTTPBadRequest('"term" param must be at least length 3')
        named_roles = RoleCache(
            g.credentials,
            g.credentials.project_roles(project_id=c.project.root_project._id).named)
        users = M.User.query.find({
                '_id': {'$in': named_roles.userids_that_reach},
                'display_name': re.compile(r'(?i)%s' % re.escape(term)),
            }).sort('username').limit(10).all()
        return dict(
            users=[
                dict(
                    label='%s (%s)' % (u.get_pref('display_name'), u.username),
                    value=u.username,
                    id=u.username)
                for u in users])

    @expose('json:')
    def users(self):
        return {
            'options': [{
                'value': u.username,
                'label': '%s (%s)' % (u.display_name, u.username)
            } for u in c.project.users()]
        }

class ScreenshotsController(object):

    @expose()
    def _lookup(self, filename, *args):
        if args:
            filename = unquote(filename)
        else:
            filename = unquote(request.path.rsplit('/', 1)[-1])
        return ScreenshotController(filename), args

class ScreenshotController(object):

    def __init__(self, filename):
        self.filename = filename

    @expose()
    def index(self, embed=True, **kw):
        return self._screenshot.serve(embed)

    @expose()
    def thumb(self, embed=True):
        return self._thumb.serve(embed)

    @LazyProperty
    def _screenshot(self):
        f = M.ProjectFile.query.get(
            project_id=c.project._id,
            category='screenshot',
            filename=self.filename)
        if not f: raise exc.HTTPNotFound
        return f

    @LazyProperty
    def _thumb(self):
        f = M.ProjectFile.query.get(
            project_id=c.project._id,
            category='screenshot_thumb',
            filename=self.filename)
        if not f: raise exc.HTTPNotFound
        return f

def set_nav(neighborhood):
    project = neighborhood.neighborhood_project
    if project:
        c.project = project
        g.set_app('admin')
    else:
        admin_url = neighborhood.url() + '_admin/'
        c.custom_sidebar_menu = [
            SitemapEntry('Overview', admin_url + 'overview'),
            SitemapEntry('Awards', admin_url + 'accolades')]

class NeighborhoodAdminController(object):
    def __init__(self, neighborhood):
        self.neighborhood = neighborhood
        self.awards = NeighborhoodAwardsController(self.neighborhood)
        self.stats = NeighborhoodStatsController(self.neighborhood)

    def _check_security(self):
        require_access(self.neighborhood, 'admin')

    @with_trailing_slash
    @expose()
    def index(self, **kw):
        utils.permanent_redirect('overview')

    @without_trailing_slash
    @expose('jinja:allura:templates/neighborhood_admin_overview.html')
    def overview(self, **kw):
        set_nav(self.neighborhood)
        c.overview_form = W.neighborhood_overview_form
        return dict(neighborhood=self.neighborhood)

    @without_trailing_slash
    @expose('jinja:allura:templates/neighborhood_admin_permissions.html')
    def permissions(self):
        set_nav(self.neighborhood)
        return dict(neighborhood=self.neighborhood)

    @expose('json:')
    def project_search(self, term=''):
        if len(term) < 3:
            raise exc.HTTPBadRequest('"term" param must be at least length 3')
        project_regex = re.compile('(?i)%s' % re.escape(term))
        projects = M.Project.query.find(dict(
            neighborhood_id=self.neighborhood._id, deleted=False,
            shortname=project_regex)).sort('shortname')
        return dict(
            projects=[
                dict(
                    label=p.shortname,
                    value=p.shortname,
                    id=p.shortname)
                for p in projects])

    @without_trailing_slash
    @expose('jinja:allura:templates/neighborhood_admin_accolades.html')
    def accolades(self):
        set_nav(self.neighborhood)
        awards = M.Award.query.find(dict(created_by_neighborhood_id=self.neighborhood._id)).all()
        awards_count = len(awards)
        grants = M.AwardGrant.query.find(dict(granted_by_neighborhood_id=self.neighborhood._id))
        grants_count = len(grants)
        c.project_select = W.project_select(self.neighborhood.url() + '_admin/project_search')
        return dict(
            awards=awards,
            awards_count=awards_count,
            grants=grants,
            grants_count=grants_count,
            neighborhood=self.neighborhood)

    @expose()
    @require_post()
    @validate(W.neighborhood_overview_form, error_handler=overview)
    def update(self, name=None, css=None, homepage=None, project_template=None, icon=None, **kw):
        nbhd = self.neighborhood
        c.project = nbhd.neighborhood_project
        h.log_if_changed(nbhd, 'name', name,
                        'change neighborhood name to %s' % name)
        nbhd_redirect = kw.pop('redirect', '')
        h.log_if_changed(nbhd, 'redirect', nbhd_redirect,
                        'change neighborhood redirect to %s' % nbhd_redirect)
        h.log_if_changed(nbhd, 'homepage', homepage,
                        'change neighborhood homepage to %s' % homepage)
        h.log_if_changed(nbhd, 'css', css,
                        'change neighborhood css to %s' % css)
        h.log_if_changed(nbhd, 'project_template', project_template,
                        'change neighborhood project template to %s'
                        % project_template)
        allow_browse = kw.get('allow_browse', False)
        h.log_if_changed(nbhd, 'allow_browse', allow_browse,
                        'change neighborhood allow browse to %s'
                        % allow_browse)
        show_title = kw.get('show_title', False)
        h.log_if_changed(nbhd, 'show_title', show_title,
                        'change neighborhood show title to %s' % show_title)
        project_list_url = kw.get('project_list_url', '')
        h.log_if_changed(nbhd, 'project_list_url', project_list_url,
                        'change neighborhood project list url to %s'
                        % project_list_url)
        tracking_id = kw.get('tracking_id', '')
        h.log_if_changed(nbhd, 'tracking_id', tracking_id,
                        'update neighborhood tracking_id')
        anchored_tools = kw.get('anchored_tools', '')
        validate_tools = dict()
        result = True
        if anchored_tools.strip() != '':
            try:
                validate_tools = dict(
                    (tool.split(':')[0].lower(), tool.split(':')[1])
                    for tool in anchored_tools.replace(' ', '').split(','))
            except Exception:
                flash('Anchored tools "%s" is invalid' % anchored_tools,'error')
                result = False


        for tool in validate_tools.keys():
            if tool not in g.entry_points['tool']:
                flash('Anchored tools "%s" is invalid' % anchored_tools,'error')
                result = False
        if result:
            h.log_if_changed(nbhd, 'anchored_tools', anchored_tools,
                             'update neighborhood anchored tools')

        if icon is not None and icon != '':
            if self.neighborhood.icon:
                self.neighborhood.icon.delete()
            M.AuditLog.log('update neighborhood icon')
            M.NeighborhoodFile.save_image(
                icon.filename, icon.file, content_type=icon.type,
                square=True, thumbnail_size=(48, 48),
                thumbnail_meta=dict(neighborhood_id=self.neighborhood._id))
        redirect('overview')

    @expose('jinja:allura:templates/neighborhood_help.html')
    @with_trailing_slash
    def help(self, **kw):
        require_access(self.neighborhood, 'admin')
        set_nav(self.neighborhood)
        return dict(
            neighborhood=self.neighborhood,
        )

class NeighborhoodStatsController(object):

    def __init__(self, neighborhood):
        self.neighborhood = neighborhood

    @with_trailing_slash
    @expose('jinja:allura:templates/neighborhood_stats.html')
    def index(self, **kw):
        delete_count = M.Project.query.find(dict(neighborhood_id=self.neighborhood._id, deleted=True)).count()
        public_count = 0
        private_count = 0
        last_updated_30 = 0
        last_updated_60 = 0
        last_updated_90 = 0
        today_date = datetime.today()
        if M.Project.query.find(dict(neighborhood_id=self.neighborhood._id, deleted=False)).count() < 20000: # arbitrary limit for efficiency
            for p in M.Project.query.find(dict(neighborhood_id=self.neighborhood._id, deleted=False)):
                if p.private:
                    private_count = private_count + 1
                else:
                    public_count = public_count + 1
                    if today_date - p.last_updated < timedelta(days=30):
                        last_updated_30 = last_updated_30 + 1
                    if today_date - p.last_updated < timedelta(days=60):
                        last_updated_60 = last_updated_60 + 1
                    if today_date - p.last_updated < timedelta(days=90):
                        last_updated_90 = last_updated_90 + 1

        set_nav(self.neighborhood)
        return dict(
            delete_count=delete_count,
            public_count=public_count,
            private_count=private_count,
            last_updated_30=last_updated_30,
            last_updated_60=last_updated_60,
            last_updated_90=last_updated_90,
            neighborhood=self.neighborhood,
        )

    @without_trailing_slash
    @expose('jinja:allura:templates/neighborhood_stats_adminlist.html')
    def adminlist(self, sort='alpha', limit=25, page=0, **kw):
        limit, page, start = g.handle_paging(limit, page)

        pq = M.Project.query.find(dict(neighborhood_id=self.neighborhood._id, deleted=False))
        if sort == 'alpha':
            pq.sort('name')
        else:
            pq.sort('last_updated', pymongo.DESCENDING)
        count = pq.count()
        projects = pq.skip(start).limit(int(limit)).all()

        entries = []
        for proj in projects:
            admin_role = M.ProjectRole.query.get(project_id=proj.root_project._id, name='Admin')
            if admin_role is None:
                continue
            user_role_list = M.ProjectRole.query.find(dict(project_id=proj.root_project._id, name=None)).all()
            for ur in user_role_list:
                if ur.user is not None and admin_role._id in ur.roles:
                    entries.append({'project': proj, 'user': ur.user})

        set_nav(self.neighborhood)
        return dict(entries=entries,
                    sort=sort,
                    limit=limit, page=page, count=count,
                    page_list=W.page_list,
                    neighborhood=self.neighborhood,
                    )

class NeighborhoodModerateController(object):

    def __init__(self, neighborhood):
        self.neighborhood = neighborhood

    def _check_security(self):
        require_access(self.neighborhood, 'admin')

    @expose('jinja:allura:templates/neighborhood_moderate.html')
    def index(self, **kw):
        c.project = self.neighborhood.neighborhood_project
        other_nbhds = list(M.Neighborhood.query.find(dict(_id={'$ne':self.neighborhood._id})).sort('name'))
        return dict(neighborhood=self.neighborhood,
                    neighborhoods=other_nbhds)

    @expose()
    @require_post()
    def invite(self, pid, neighborhood_id, invite=None, uninvite=None):
        p = M.Project.query.get(shortname=pid, deleted=False,
                                neighborhood_id=ObjectId(neighborhood_id))
        if p is None:
            flash("Can't find %s" % pid, 'error')
            redirect('.')
        if p.neighborhood == self.neighborhood:
            flash("%s is already in the neighborhood" % pid, 'error')
            redirect('.')
        if invite:
            if self.neighborhood._id in p.neighborhood_invitations:
                flash("%s is already invited" % pid, 'warning')
                redirect('.')
            p.neighborhood_invitations.append(self.neighborhood._id)
            flash('%s invited' % pid)
        elif uninvite:
            if self.neighborhood._id not in p.neighborhood_invitations:
                flash("%s is already uninvited" % pid, 'warning')
                redirect('.')
            p.neighborhood_invitations.remove(self.neighborhood._id)
            flash('%s uninvited' % pid)
        redirect('.')

    @expose()
    @require_post()
    def evict(self, pid):
        p = M.Project.query.get(shortname=pid, neighborhood_id=self.neighborhood._id, deleted=False)
        if p is None:
            flash("Cannot evict  %s; it's not in the neighborhood"
                  % pid, 'error')
            redirect('.')
        if not p.is_root:
            flash("Cannot evict %s; it's a subproject" % pid, 'error')
            redirect('.')
        n = M.Neighborhood.query.get(name='Projects')
        p.neighborhood_id = n._id
        if self.neighborhood._id in p.neighborhood_invitations:
            p.neighborhood_invitations.remove(self.neighborhood._id)
        flash('%s evicted to Projects' % pid)
        redirect('.')

class NeighborhoodAwardsController(object):

    def __init__(self, neighborhood=None):
        if neighborhood is not None:
            self.neighborhood = neighborhood

    @expose('jinja:allura:templates/awards.html')
    def index(self, **kw):
        require_access(self.neighborhood, 'admin')
        awards = M.Award.query.find(dict(created_by_neighborhood_id=self.neighborhood._id))
        count = len(awards)
        return dict(awards=awards or [], count=count)

    @expose('jinja:allura:templates/award_not_found.html')
    def not_found(self, **kw):
        return dict()

    @expose('jinja:allura:templates/grants.html')
    def grants(self, **kw):
        require_access(self.neighborhood, 'admin')
        grants = M.AwardGrant.query.find(dict(granted_by_neighborhood_id=self.neighborhood._id))
        count = len(grants)
        return dict(grants=grants or [], count=count)

    @expose()
    def _lookup(self, award_id, *remainder):
        return AwardController(self.neighborhood, award_id), remainder

    @expose()
    @require_post()
    def create(self, icon=None, short=None, full=None):
        require_access(self.neighborhood, 'admin')
        app_config_id = ObjectId()
        tool_version = {'neighborhood': '0'}
        if short:
            award = M.Award(app_config_id=app_config_id, tool_version=tool_version)
            award.short = short
            award.full = full
            award.created_by_neighborhood_id = self.neighborhood._id
            if hasattr(icon, 'filename'):
                M.AwardFile.save_image(
                    icon.filename, icon.file, content_type=icon.type,
                    square=True, thumbnail_size=(48, 48),
                    thumbnail_meta=dict(award_id=award._id))
        redirect(request.referer)

    @expose()
    @require_post()
    def grant(self, grant=None, recipient=None):
        require_access(self.neighborhood, 'admin')
        grant_q = M.Award.query.find(dict(short=grant,
            created_by_neighborhood_id=self.neighborhood._id)).first()
        recipient_q = M.Project.query.find(dict(
            neighborhood_id=self.neighborhood._id, shortname=recipient,
            deleted=False)).first()
        if grant_q and recipient_q:
            app_config_id = ObjectId()
            tool_version = {'neighborhood': '0'}
            award = M.AwardGrant(app_config_id=app_config_id,
                                 tool_version=tool_version)
            award.award_id = grant_q._id
            award.granted_to_project_id = recipient_q._id
            award.granted_by_neighborhood_id = self.neighborhood._id
            with h.push_context(recipient_q._id):
                g.post_event('project_updated')
        redirect(request.referer)

class AwardController(object):

    def __init__(self, neighborhood=None, award_id=None):
        self.neighborhood = neighborhood
        if award_id:
            self.award = M.Award.query.find(dict(_id=ObjectId(award_id),
                created_by_neighborhood_id=self.neighborhood._id)).first()

    @with_trailing_slash
    @expose('jinja:allura:templates/award.html')
    def index(self, **kw):
        require_access(self.neighborhood, 'admin')
        set_nav(self.neighborhood)
        if self.award is not None:
            return dict(award=self.award, neighborhood=self.neighborhood)
        else:
            redirect('not_found')

    @expose('jinja:allura:templates/award_not_found.html')
    def not_found(self, **kw):
        return dict()

    @expose()
    def _lookup(self, recipient, *remainder):
        recipient = unquote(recipient)
        return GrantController(self.neighborhood, self.award, recipient), remainder

    @expose()
    def icon(self, **kw):
        icon = self.award.icon
        if not icon:
            raise exc.HTTPNotFound
        return icon.serve()

    @expose()
    @require_post()
    def update(self, icon=None, short=None, full=None):
        require_access(self.neighborhood, 'admin')
        self.award.short = short
        self.award.full = full
        if hasattr(icon, 'filename'):
            if self.award.icon:
                self.award.icon.delete()
            M.AwardFile.save_image(
                icon.filename, icon.file, content_type=icon.type,
                square=True, thumbnail_size=(48, 48),
                thumbnail_meta=dict(award_id=self.award._id))
        for grant in M.AwardGrant.query.find(dict(award_id=self.award._id)):
            with h.push_context(grant.granted_to_project_id):
                g.post_event('project_updated')
        flash('Award updated.')
        redirect(self.award.longurl())

    @expose()
    @require_post()
    def delete(self):
        require_access(self.neighborhood, 'admin')
        if self.award:
            grants = M.AwardGrant.query.find(dict(award_id=self.award._id))
            for grant in grants:
                grant.delete()
                with h.push_context(grant.granted_to_project_id):
                    g.post_event('project_updated')
            M.AwardFile.query.remove(dict(award_id=self.award._id))
            self.award.delete()
        redirect(request.referer)

class GrantController(object):

    def __init__(self, neighborhood=None, award=None, recipient=None):
        self.neighborhood = neighborhood
        if recipient is not None and award is not None:
            self.recipient = recipient.replace('_', '/')
            self.award = M.Award.query.get(_id=award._id)
            self.project = M.Project.query.find(dict(shortname=self.recipient,
                neighborhood_id=self.neighborhood._id)).first()
            self.grant = M.AwardGrant.query.get(award_id=self.award._id,
                granted_to_project_id=self.project._id)

    @with_trailing_slash
    @expose('jinja:allura:templates/grant.html')
    def index(self, **kw):
        require_access(self.neighborhood, 'admin')
        if self.grant is not None:
            return dict(grant=self.grant)
        else:
            redirect('not_found')

    @expose('jinja:allura:templates/award_not_found.html')
    def not_found(self, **kw):
        return dict()

    @expose()
    def icon(self, **kw):
        icon = self.award.icon
        if not icon:
            raise exc.HTTPNotFound
        return icon.serve()

    @expose()
    @require_post()
    def revoke(self):
        require_access(self.neighborhood, 'admin')
        self.grant.delete()
        with h.push_context(self.project._id):
            g.post_event('project_updated')
        redirect(request.referer)
