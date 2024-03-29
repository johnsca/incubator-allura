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

import cgi
import random
import shlex
import logging
import traceback
from operator import attrgetter

import pymongo
from pylons import tmpl_context as c, app_globals as g
from pylons import request
from paste.deploy.converters import asint

from . import helpers as h
from . import security

log = logging.getLogger(__name__)

_macros = {}
class macro(object):

    def __init__(self, context=None):
        self._context = context

    def __call__(self, func):
        _macros[func.__name__] = (func, self._context)
        return func

class parse(object):

    def __init__(self, context):
        self._context = context

    def __call__(self, s):
        try:
            if s.startswith('quote '):
                return '[[' + s[len('quote '):] + ']]'
            try:
                parts = [ unicode(x, 'utf-8') for x in shlex.split(s.encode('utf-8')) ]
                if not parts: return '[[' + s + ']]'
                macro = self._lookup_macro(parts[0])
                if not macro: return  '[[' + s + ']]'
                for t in parts[1:]:
                    if '=' not in t:
                        return '[-%s: missing =-]' % ' '.join(parts)
                args = dict(t.split('=', 1) for t in parts[1:])
                response = macro(**h.encode_keys(args))
                return response
            except (ValueError, TypeError) as ex:
                log.warn('macro error.  Upwards stack is %s',
                         ''.join(traceback.format_stack()),
                         exc_info=True)
                msg = cgi.escape(u'[[%s]] (%s)' % (s, repr(ex)))
                return '\n<div class="error"><pre><code>%s</code></pre></div>' % msg
        except Exception, ex:
            raise
            return '[[Error parsing %s: %s]]' % (s, ex)

    def _lookup_macro(self, s):
        macro, context = _macros.get(s, (None, None))
        if context is None or context == self._context:
            return macro
        else:
            return None

@macro('neighborhood-wiki')
def neighborhood_feeds(tool_name, max_number=5, sort='pubdate'):
    from allura import model as M
    from allura.lib.widgets.macros import NeighborhoodFeeds
    feed = M.Feed.query.find(
        dict(
            tool_name=tool_name,
            neighborhood_id=c.project.neighborhood._id))
    feed = feed.sort(sort, pymongo.DESCENDING).limit(int(max_number)).all()
    output = ((dict(
                href=item.link,
                title=item.title,
                author=item.author_name,
                ago=h.ago(item.pubdate),
                description=g.markdown.convert(item.description)))
        for item in feed)
    feeds = NeighborhoodFeeds(feeds=output)
    g.resource_manager.register(feeds)
    response = feeds.display(feeds=output)
    return response

@macro('neighborhood-wiki')
def neighborhood_blog_posts(max_number=5, sort='timestamp', summary=False):
    from forgeblog import model as BM
    from allura.lib.widgets.macros import BlogPosts
    posts = BM.BlogPost.query.find(dict(
        neighborhood_id=c.project.neighborhood._id,
        state='published'))
    posts = posts.sort(sort, pymongo.DESCENDING).limit(int(max_number)).all()
    output = ((dict(
                href=post.url(),
                title=post.title,
                author=post.author().display_name,
                ago=h.ago(post.timestamp),
                description=summary and '&nbsp;' or g.markdown.convert(post.text)))
        for post in posts if post.app and
                             security.has_access(post, 'read', project=post.app.project)() and
                             security.has_access(post.app.project, 'read', project=post.app.project)())

    posts = BlogPosts(posts=output)
    g.resource_manager.register(posts)
    response = posts.display(posts=output)
    return response

@macro()
def project_blog_posts(max_number=5, sort='timestamp', summary=False, mount_point=None):
    from forgeblog import model as BM
    from allura.lib.widgets.macros import BlogPosts
    app_config_ids = []
    for conf in c.project.app_configs:
        if conf.tool_name.lower() == 'blog' and (mount_point is None or conf.options.mount_point==mount_point):
            app_config_ids.append(conf._id)
    posts = BM.BlogPost.query.find({'state':'published','app_config_id':{'$in':app_config_ids}})
    posts = posts.sort(sort, pymongo.DESCENDING).limit(int(max_number)).all()
    output = ((dict(
                href=post.url(),
                title=post.title,
                author=post.author().display_name,
                ago=h.ago(post.timestamp),
                description=summary and '&nbsp;' or g.markdown.convert(post.text)))
        for post in posts if security.has_access(post, 'read', project=post.app.project)() and
                             security.has_access(post.app.project, 'read', project=post.app.project)())
    posts = BlogPosts(posts=output)
    g.resource_manager.register(posts)
    response = posts.display(posts=output)
    return response

def get_projects_for_macro(category=None, display_mode='grid', sort='last_updated',
        show_total=False, limit=100, labels='', award='', private=False,
        columns=1, show_proj_icon=True, show_download_button=True, show_awards_banner=True,
        grid_view_tools='',
        initial_q={}):
    from allura.lib.widgets.project_list import ProjectList
    from allura.lib import utils
    from allura import model as M
    # 'trove' is internal substitution for 'category' filter in wiki macro
    trove = category
    limit = int(limit)
    q = dict(
        deleted=False,
        is_nbhd_project=False)
    q.update(initial_q)

    if labels:
        or_labels = labels.split('|')
        q['$or'] = [{'labels': {'$all': l.split(',')}} for l in or_labels]
    if trove is not None:
        trove = M.TroveCategory.query.get(fullpath=trove)
    if award:
        aw = M.Award.query.find(dict(
            created_by_neighborhood_id=c.project.neighborhood_id,
            short=award)).first()
        if aw:
            ids = [grant.granted_to_project_id for grant in
                M.AwardGrant.query.find(dict(
                    granted_by_neighborhood_id=c.project.neighborhood_id,
                    award_id=aw._id))]
            if '_id' in q:
                ids = list(set(q['_id']['$in']).intersection(ids))
            q['_id'] = {'$in': ids}

    if trove is not None:
        q['trove_' + trove.type] = trove._id
    sort_key, sort_dir = 'last_updated', pymongo.DESCENDING
    if sort == 'alpha':
        sort_key, sort_dir = 'name', pymongo.ASCENDING
    elif sort == 'random':
        sort_key, sort_dir = None, None
    elif sort == 'last_registered':
        sort_key, sort_dir = '_id', pymongo.DESCENDING
    elif sort == '_id':
        sort_key, sort_dir = '_id', pymongo.DESCENDING

    projects = []
    if private:
        # Only return private projects.
        # Can't filter these with a mongo query directly - have to iterate
        # through and check the ACL of each project.
        for chunk in utils.chunked_find(M.Project, q, sort_key=sort_key,
                sort_dir=sort_dir):
            projects.extend([p for p in chunk if p.private])
        total = len(projects)
        if sort == 'random':
            projects = random.sample(projects, min(limit, total))
        else:
            projects = projects[:limit]
    else:
        total = None
        if sort == 'random':
            # MongoDB doesn't have a random sort built in, so...
            # 1. Do a direct pymongo query (faster than ORM) to fetch just the
            #    _ids of objects that match our criteria
            # 2. Choose a random sample of those _ids
            # 3. Do an ORM query to fetch the objects with those _ids
            # 4. Shuffle the results
            from ming.orm import mapper
            m = mapper(M.Project)
            collection = M.main_doc_session.db[m.collection.m.collection_name]
            docs = list(collection.find(q, {'_id': 1}))
            if docs:
                ids = [doc['_id'] for doc in
                        random.sample(docs, min(limit, len(docs)))]
                if '_id' in q:
                    ids = list(set(q['_id']['$in']).intersection(ids))
                q['_id'] = {'$in': ids}
                projects = M.Project.query.find(q).all()
                random.shuffle(projects)
        else:
            projects = M.Project.query.find(q).limit(limit).sort(sort_key,
                sort_dir).all()

    pl = ProjectList()
    g.resource_manager.register(pl)
    response = pl.display(projects=projects, display_mode=display_mode,
                          columns=columns, show_proj_icon=show_proj_icon,
                          show_download_button=show_download_button,
                          show_awards_banner=show_awards_banner,
                          grid_view_tools=grid_view_tools)
    if show_total:
        if total is None:
            total = 0
            for p in M.Project.query.find(q):
                if h.has_access(p, 'read')():
                    total = total + 1
        response = '<p class="macro_projects_total">%s Projects</p>%s' % \
                (total, response)
    return response


@macro('neighborhood-wiki')
def projects(category=None, display_mode='grid', sort='last_updated',
        show_total=False, limit=100, labels='', award='', private=False,
        columns=1, show_proj_icon=True, show_download_button=True, show_awards_banner=True,
        grid_view_tools=''):
    initial_q = dict(neighborhood_id=c.project.neighborhood_id)
    return get_projects_for_macro(category=category, display_mode=display_mode, sort=sort,
                   show_total=show_total, limit=limit, labels=labels, award=award, private=private,
                   columns=columns, show_proj_icon=show_proj_icon, show_download_button=show_download_button,
                   show_awards_banner=show_awards_banner, grid_view_tools=grid_view_tools,
                   initial_q=initial_q)

@macro('userproject-wiki')
def my_projects(category=None, display_mode='grid', sort='last_updated',
        show_total=False, limit=100, labels='', award='', private=False,
        columns=1, show_proj_icon=True, show_download_button=True, show_awards_banner=True,
        grid_view_tools=''):

    myproj_user = c.project.user_project_of
    if myproj_user is None:
        myproj_user = c.user.anonymous()

    ids = []
    for p in myproj_user.my_projects():
        ids.append(p._id)

    initial_q = dict(_id={'$in': ids})
    return get_projects_for_macro(category=category, display_mode=display_mode, sort=sort,
                   show_total=show_total, limit=limit, labels=labels, award=award, private=private,
                   columns=columns, show_proj_icon=show_proj_icon, show_download_button=show_download_button,
                   show_awards_banner=show_awards_banner, grid_view_tools=grid_view_tools,
                   initial_q=initial_q)

@macro()
def project_screenshots():
    from allura.lib.widgets.project_list import ProjectScreenshots
    ps = ProjectScreenshots()
    g.resource_manager.register(ps)
    response = ps.display(project=c.project)
    return response


# FIXME: this is SourceForge specific - need to provide a way for macros to come from other packages
@macro()
def download_button():
    from allura.lib.widgets.macros import DownloadButton
    button = DownloadButton(project=c.project)
    try:
        res_mgr = g.resource_manager
    except TypeError:
        # e.g. "TypeError: No object (name: widget_context) has been registered for this thread"
        # this is an ugly way to check to see if we're outside of a web request and avoid errors
        return '[[download_button]]'
    else:
        res_mgr.register(button)
        response = button.display(project=c.project)
        return response


@macro()
def include(ref=None, **kw):
    from allura import model as M
    from allura.lib.widgets.macros import Include
    if ref is None:
        return '[-include-]'
    link = M.Shortlink.lookup(ref)
    if not link:
        return '[[include %s (not found)]]' % ref
    artifact = link.ref.artifact
    if artifact is None:
        return '[[include (artifact not found)]]' % ref
    included = request.environ.setdefault('allura.macro.included', set())
    if artifact in included:
        return '[[include %s (already included)]' % ref
    else:
        included.add(artifact)
    sb = Include()
    g.resource_manager.register(sb)
    response = sb.display(artifact=artifact, attrs=kw)
    return response

@macro()
def img(src=None, **kw):
    attrs = ('%s="%s"' % t for t in kw.iteritems())
    included = request.environ.setdefault('allura.macro.att_embedded', set())
    included.add(src)
    if '://' in src:
        return '<img src="%s" %s/>' % (src, ' '.join(attrs))
    else:
        return '<img src="./attachment/%s" %s/>' % (src, ' '.join(attrs))

@macro()
def project_admins():
    admins = c.project.users_with_role('Admin')
    from allura.lib.widgets.macros import ProjectAdmins
    output = ((dict(
            url=user.url(),
            name=user.display_name))
        for user in admins)
    users = ProjectAdmins(users=output)
    g.resource_manager.register(users)
    response = users.display(users=output)
    return response

@macro()
def members(limit=20):
    from allura.lib.widgets.macros import Members
    limit = asint(limit)
    admins = set(c.project.users_with_role('Admin'))
    members = sorted(c.project.users(), key=attrgetter('display_name'))
    output = [dict(
            url=user.url(),
            name=user.display_name,
            admin=' (admin)' if user in admins else '',
            )
        for user in members[:limit]]

    over_limit = len(members) > limit
    users = Members(users=output, over_limit=over_limit)
    g.resource_manager.register(users)
    response = users.display(users=output, over_limit=over_limit)
    return response

