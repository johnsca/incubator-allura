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

import difflib
import functools
from datetime import datetime
from random import randint

from pylons import tmpl_context as c, app_globals as g
from pymongo.errors import DuplicateKeyError

from ming import schema
from ming.orm import FieldProperty, ForeignIdProperty, Mapper, session, state
from ming.orm.declarative import MappedClass

from allura import model as M
from allura.model.timeline import ActivityObject
from allura.lib import helpers as h
from allura.lib import utils

config = utils.ConfigProxy(
    common_suffix='forgemail.domain')

class Globals(MappedClass):

    class __mongometa__:
        name = 'blog-globals'
        session = M.project_orm_session
        indexes = [ 'app_config_id' ]

    type_s = 'BlogGlobals'
    _id = FieldProperty(schema.ObjectId)
    app_config_id = ForeignIdProperty('AppConfig', if_missing=lambda:c.app.config._id)
    external_feeds=FieldProperty([str])


class BlogPostSnapshot(M.Snapshot):
    class __mongometa__:
        name='blog_post_snapshot'
    type_s='Blog Post Snapshot'

    def original(self):
        return BlogPost.query.get(_id=self.artifact_id)

    def shorthand_id(self):
        orig = self.original()
        if not orig:
            return None
        return '%s#%s' % (orig.shorthand_id(), self.version)

    def url(self):
        orig = self.original()
        if not orig:
            return None
        return orig.url() + '?version=%d' % self.version

    def index(self):
        orig = self.original()
        if not orig:
            return None
        result = super(BlogPostSnapshot, self).index()
        result.update(
            title='%s (version %d)' % (orig.title, self.version),
            type_s=self.type_s,
            text=self.data.text)
        return result

    @property
    def html_text(self):
        """A markdown processed version of the page text"""
        return g.markdown_wiki.convert(self.data.text)

    @property
    def attachments(self):
        orig = self.original()
        if not orig:
            return None
        return orig.attachments

    @property
    def email_address(self):
        orig = self.original()
        if not orig:
            return None
        return orig.email_address

class BlogPost(M.VersionedArtifact, ActivityObject):
    class __mongometa__:
        name='blog_post'
        history_class = BlogPostSnapshot
        unique_indexes = [ ('app_config_id', 'slug') ]
    type_s = 'Blog Post'

    title = FieldProperty(str, if_missing='Untitled')
    text = FieldProperty(str, if_missing='')
    timestamp = FieldProperty(datetime, if_missing=datetime.utcnow)
    slug = FieldProperty(str)
    state = FieldProperty(schema.OneOf('draft', 'published'), if_missing='draft')
    neighborhood_id = ForeignIdProperty('Neighborhood', if_missing=None)

    @property
    def activity_name(self):
        return 'blog post %s' % self.title

    def author(self):
        '''The author of the first snapshot of this BlogPost'''
        return M.User.query.get(_id=self.get_version(1).author.id) or M.User.anonymous()

    def _get_date(self):
        return self.timestamp.date()
    def _set_date(self, value):
        self.timestamp = datetime.combine(value, self.time)
    date = property(_get_date, _set_date)

    def _get_time(self):
        return self.timestamp.time()
    def _set_time(self, value):
        self.timestamp = datetime.combine(self.date, value)
    time = property(_get_time, _set_time)

    @property
    def html_text(self):
        return g.markdown.convert(self.text)

    @property
    def html_text_preview(self):
        """Return an html preview of the BlogPost text.

        Truncation happens at paragraph boundaries to avoid chopping markdown
        in inappropriate places.

        If the entire post is one paragraph, the full text is returned.
        If the entire text is <= 400 chars, the full text is returned.
        Else, at least 400 chars are returned, rounding up to the nearest
        whole paragraph.

        If truncation occurs, a hyperlink to the full text is appended.

        """
        # Splitting on spaces or single lines breaks isn't sufficient as some
        # markup can span spaces and single line breaks. Converting to HTML
        # first and *then* truncating doesn't work either, because the
        # ellipsis tag ends up orphaned from the main text.
        ellipsis = '... [read more](%s)' % self.url()
        paragraphs = self.text.replace('\r','').split('\n\n')
        total_length = 0
        for i, p in enumerate(paragraphs):
            total_length += len(p)
            if total_length >= 400:
                break
        text = '\n\n'.join(paragraphs[:i+1])
        return g.markdown.convert(text + (ellipsis if i + 1 < len(paragraphs)
                                                   else ''))

    @property
    def email_address(self):
        domain = '.'.join(reversed(self.app.url[1:-1].split('/'))).replace('_', '-')
        return '%s@%s%s' % (self.title.replace('/', '.'), domain, config.common_suffix)

    @staticmethod
    def make_base_slug(title, timestamp):
        slugsafe = ''.join(
            ch.lower()
            for ch in title.replace(' ', '-')
            if ch.isalnum() or ch == '-')
        return '%s/%s' % (
                timestamp.strftime('%Y/%m'),
                slugsafe)

    def make_slug(self):
        base = BlogPost.make_base_slug(self.title, self.timestamp)
        self.slug = base
        while True:
            try:
                session(self).insert_now(self, state(self))
                return self.slug
            except DuplicateKeyError:
                self.slug = base + '-%.3d' % randint(0,999)

    def url(self):
        return self.app.url + self.slug + '/'

    def shorthand_id(self):
        return self.slug

    def index(self):
        result = super(BlogPost, self).index()
        result.update(
            title=self.title,
            type_s=self.type_s,
            state_s=self.state,
            snippet_s='%s: %s' % (self.title, h.text.truncate(self.text, 200)),
            text=self.text)
        return result

    def get_version(self, version):
        HC = self.__mongometa__.history_class
        return HC.query.find({'artifact_id':self._id, 'version':int(version)}).one()

    def commit(self):
        activity = functools.partial(g.director.create_activity, c.user,
                target=c.project)
        self.subscribe()
        super(BlogPost, self).commit()
        if self.version > 1:
            v1 = self.get_version(self.version-1)
            v2 = self
            la = [ line + '\n'  for line in v1.text.splitlines() ]
            lb = [ line + '\n'  for line in v2.text.splitlines() ]
            diff = ''.join(difflib.unified_diff(
                    la, lb,
                    'v%d' % v1.version,
                    'v%d' % v2.version))
            description = diff
            if v1.state != 'published' and v2.state == 'published':
                activity('created', self)
                M.Feed.post(self, self.title, self.text, author=self.author(), pubdate=self.get_version(1).timestamp)
                description = self.text
                subject = '%s created post %s' % (
                    c.user.username, self.title)
            elif v1.title != v2.title:
                activity('renamed', self)
                subject = '%s renamed post %s to %s' % (
                    c.user.username, v2.title, v1.title)
            else:
                activity('modified', self)
                subject = '%s modified post %s' % (
                    c.user.username, self.title)
        else:
            description = self.text
            subject = '%s created post %s' % (
                c.user.username, self.title)
            if self.state == 'published':
                activity('created', self)
                M.Feed.post(self, self.title, self.text, author=self.author(), pubdate=self.timestamp)
        if self.state == 'published':
            M.Notification.post(
                artifact=self, topic='metadata', text=description, subject=subject)

class Attachment(M.BaseAttachment):
    ArtifactClass=BlogPost
    class __mongometa__:
        polymorphic_identity='BlogAttachment'
    attachment_type=FieldProperty(str, if_missing='BlogAttachment')


Mapper.compile_all()
