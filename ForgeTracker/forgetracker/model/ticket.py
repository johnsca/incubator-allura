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
import urllib
import json
import difflib
from datetime import datetime, timedelta
from bson import ObjectId

import pymongo
from pymongo.errors import OperationFailure
from pylons import tmpl_context as c, app_globals as g
from pprint import pformat

from ming import schema
from ming.utils import LazyProperty
from ming.orm import Mapper, session
from ming.orm import FieldProperty, ForeignIdProperty, RelationProperty
from ming.orm.declarative import MappedClass

from allura.model import (Artifact, VersionedArtifact, Snapshot,
                          project_orm_session, BaseAttachment, VotableArtifact, AppConfig, Mailbox, User)
from allura.model import User, Feed, Thread, Notification, ProjectRole
from allura.model import ACE, ALL_PERMISSIONS, DENY_ALL
from allura.model.timeline import ActivityObject

from allura.lib import security
from allura.lib.search import search_artifact, SearchError
from allura.lib import utils
from allura.lib import helpers as h
from allura.tasks import mail_tasks

from forgetracker.plugins import ImportIdConverter

log = logging.getLogger(__name__)

CUSTOM_FIELD_SOLR_TYPES = dict(boolean='_b', number='_i')
SOLR_TYPE_DEFAULTS = dict(_b=False, _i=0)


def get_default_for_solr_type(solr_type):
    return SOLR_TYPE_DEFAULTS.get(solr_type, u'')

config = utils.ConfigProxy(
    common_suffix='forgemail.domain',
    new_solr='solr.use_new_types')

class Globals(MappedClass):

    class __mongometa__:
        name = 'globals'
        session = project_orm_session
        indexes = [ 'app_config_id' ]

    type_s = 'Globals'
    _id = FieldProperty(schema.ObjectId)
    app_config_id = ForeignIdProperty('AppConfig', if_missing=lambda:c.app.config._id)
    last_ticket_num = FieldProperty(int)
    status_names = FieldProperty(str)
    open_status_names = FieldProperty(str)
    closed_status_names = FieldProperty(str)
    milestone_names = FieldProperty(str, if_missing='')
    custom_fields = FieldProperty([{str:None}])
    _bin_counts = FieldProperty(schema.Deprecated) # {str:int})
    _bin_counts_data = FieldProperty([dict(summary=str, hits=int)])
    _bin_counts_expire = FieldProperty(datetime)
    _bin_counts_invalidated = FieldProperty(datetime)
    _milestone_counts = FieldProperty(schema.Deprecated) #[dict(name=str,hits=int,closed=int)])
    _milestone_counts_expire = FieldProperty(schema.Deprecated) #datetime)
    show_in_search = FieldProperty({str: bool}, if_missing={'ticket_num': True,
                                                            'summary': True,
                                                            '_milestone': True,
                                                            'status': True,
                                                            'assigned_to': True,
                                                            'reported_by': False,
                                                            'created_date': True,
                                                            'mod_date': True,
                                                            'labels': False,
                                                            })

    @classmethod
    def next_ticket_num(cls):
        gbl = cls.query.find_and_modify(
            query=dict(app_config_id=c.app.config._id),
            update={'$inc': { 'last_ticket_num': 1}},
            new=True)
        session(cls).expunge(gbl)
        return gbl.last_ticket_num

    @property
    def all_status_names(self):
        return ' '.join([self.open_status_names, self.closed_status_names])

    @property
    def set_of_all_status_names(self):
        return set([name for name in self.all_status_names.split(' ') if name])

    @property
    def set_of_open_status_names(self):
        return set([name for name in self.open_status_names.split(' ') if name])

    @property
    def set_of_closed_status_names(self):
        return set([name for name in self.closed_status_names.split(' ') if name])

    @property
    def not_closed_query(self):
        return ' && '.join(['!status:'+name for name in self.set_of_closed_status_names])

    @property
    def not_closed_mongo_query(self):
        return dict(
            status={'$nin': list(self.set_of_closed_status_names)})

    @property
    def closed_query(self):
        return ' or '.join(['status:'+name for name in self.set_of_closed_status_names])

    @property
    def milestone_fields(self):
        return [ fld for fld in self.custom_fields if fld['type'] == 'milestone' ]

    def get_custom_field(self, name):
        for fld in self.custom_fields:
            if fld['name'] == name:
                return fld
        return None

    def get_custom_field_solr_type(self, field_name):
        """Return the Solr type for a custom field.

        :param field_name: Name of the custom field
        :type field_name: str
        :returns: The Solr type suffix (e.g. '_s', '_i', '_b') or None if
            there is no custom_field named ``field_name``.

        """
        fld = self.get_custom_field(field_name)
        if fld:
            return CUSTOM_FIELD_SOLR_TYPES.get(fld.type, '_s')
        return None

    def update_bin_counts(self):
        # Refresh bin counts
        self._bin_counts_data = []
        for b in Bin.query.find(dict(
                app_config_id=self.app_config_id)):
            if b.terms and '$USER' in b.terms:
                continue  # skip queries with $USER variable, hits will be inconsistent for them
            r = search_artifact(Ticket, b.terms, rows=0, short_timeout=False)
            hits = r is not None and r.hits or 0
            self._bin_counts_data.append(dict(summary=b.summary, hits=hits))
        self._bin_counts_expire = \
            datetime.utcnow() + timedelta(minutes=60)
        self._bin_counts_invalidated = None

    def bin_count(self, name):
        # not sure why we expire bin counts after an hour even if unchanged
        # I guess a catch-all in case invalidate_bin_counts is missed
        if self._bin_counts_expire < datetime.utcnow():
            self.invalidate_bin_counts()
        for d in self._bin_counts_data:
            if d['summary'] == name: return d
        return dict(summary=name, hits=0)

    def milestone_count(self, name):
        fld_name, m_name = name.split(':', 1)
        d = dict(name=name, hits=0, closed=0)
        if not (fld_name and m_name):
            return d
        mongo_query = {'custom_fields.%s' % fld_name: m_name}
        r = Ticket.query.find(dict(
            mongo_query, app_config_id=c.app.config._id, deleted=False))
        tickets = [t for t in r if security.has_access(t, 'read')]
        d['hits'] = len(tickets)
        d['closed'] = sum(1 for t in tickets
                          if t.status in c.app.globals.set_of_closed_status_names)
        return d

    def invalidate_bin_counts(self):
        '''Force expiry of bin counts and queue them to be updated.'''
        # To prevent multiple calls to this method from piling on redundant
        # tasks, we set _bin_counts_invalidated when we post the task, and
        # the task clears it when it's done.  However, in the off chance
        # that the task fails or is interrupted, we ignore the flag if it's
        # older than 5 minutes.
        invalidation_expiry = datetime.utcnow() - timedelta(minutes=5)
        if self._bin_counts_invalidated is not None and \
           self._bin_counts_invalidated > invalidation_expiry:
            return
        self._bin_counts_invalidated = datetime.utcnow()
        from forgetracker import tasks  # prevent circular import
        tasks.update_bin_counts.post(self.app_config_id, delay=5)

    def sortable_custom_fields_shown_in_search(self):
        def solr_type(field_name):
            # Pre solr-4.2.1 code indexed all custom fields as strings, so
            # they must be searched as such.
            if not config.get_bool('new_solr'):
                return '_s'
            return self.get_custom_field_solr_type(field_name) or '_s'

        return [dict(
            sortable_name='{0}{1}'.format(field['name'],
                solr_type(field['name'])),
            name=field['name'],
            label=field['label'])
            for field in self.custom_fields
            if field.get('show_in_search')]

    def has_deleted_tickets(self):
        return Ticket.query.find(dict(
            app_config_id=c.app.config._id, deleted=True)).count() > 0

    def move_tickets(self, ticket_ids, destination_tracker_id):
        tracker = AppConfig.query.get(_id=destination_tracker_id)
        tickets = Ticket.query.find(dict(
            _id={'$in': [ObjectId(id) for id in ticket_ids]},
            app_config_id=c.app.config._id)).sort('ticket_num').all()
        filtered = self.filtered_by_subscription({t._id: t for t in tickets})
        original_ticket_nums = {t._id: t.ticket_num for t in tickets}
        users = User.query.find({'_id': {'$in': filtered.keys()}}).all()
        moved_tickets = {}
        for ticket in tickets:
            moved = ticket.move(tracker, notify=False)
            moved_tickets[moved._id] = moved
        mail = dict(
            fromaddr = str(c.user.email_address_header()),
            reply_to = str(c.user.email_address_header()),
            subject = '[%s:%s] Mass ticket moving by %s' % (c.project.shortname,
                                                          c.app.config.options.mount_point,
                                                          c.user.display_name))
        tmpl = g.jinja2_env.get_template('forgetracker:data/mass_move_report.html')

        tmpl_context = {
            'original_tracker': '%s:%s' % (c.project.shortname,
                                           c.app.config.options.mount_point),
            'destination_tracker': '%s:%s' % (tracker.project.shortname,
                                              tracker.options.mount_point),
            'tickets': [],
        }
        for user in users:
            tmpl_context['tickets'] = ({
                    'original_num': original_ticket_nums[_id],
                    'destination_num': moved_tickets[_id].ticket_num,
                    'summary': moved_tickets[_id].summary
                } for _id in filtered.get(user._id, []))
            mail.update(dict(
                message_id = h.gen_message_id(),
                text = tmpl.render(tmpl_context),
                destinations = [str(user._id)]))
            mail_tasks.sendmail.post(**mail)

        if c.app.config.options.get('TicketMonitoringType') in (
                'AllTicketChanges', 'AllPublicTicketChanges'):
            monitoring_email = c.app.config.options.get('TicketMonitoringEmail')
            tmpl_context['tickets'] = [{
                    'original_num': original_ticket_nums[_id],
                    'destination_num': moved_tickets[_id].ticket_num,
                    'summary': moved_tickets[_id].summary
                } for _id, t in moved_tickets.iteritems()
                  if (not t.private or
                      c.app.config.options.get('TicketMonitoringType') ==
                      'AllTicketChanges')]
            if len(tmpl_context['tickets']) > 0:
                mail.update(dict(
                    message_id = h.gen_message_id(),
                    text = tmpl.render(tmpl_context),
                    destinations = [monitoring_email]))
                mail_tasks.sendmail.post(**mail)

        moved_from = '%s/%s' % (c.project.shortname, c.app.config.options.mount_point)
        moved_to = '%s/%s' % (tracker.project.shortname, tracker.options.mount_point)
        text = 'Tickets moved from %s to %s' % (moved_from, moved_to)
        Notification.post_user(c.user, None, 'flash', text=text)

    def filtered_by_subscription(self, tickets, project_id=None, app_config_id=None):
        p_id = project_id if project_id else c.project._id
        ac_id = app_config_id if app_config_id else c.app.config._id
        ticket_ids = tickets.keys()
        users = Mailbox.query.find(dict(project_id=p_id, app_config_id=ac_id))
        users = [u.user_id for u in users]
        filtered = {}
        for uid in users:
            params = dict(
                user_id=uid,
                project_id=p_id,
                app_config_id=ac_id)
            if Mailbox.subscribed(**params):
                filtered[uid] = set(ticket_ids)  # subscribed to entire tool, will see all changes
                continue
            for t_id, ticket in tickets.iteritems():
                params.update({'artifact': ticket})
                if Mailbox.subscribed(**params):
                    if filtered.get(uid) is None:
                        filtered[uid] = set()
                    filtered[uid].add(t_id)
        return filtered


class TicketHistory(Snapshot):

    class __mongometa__:
        name = 'ticket_history'

    def original(self):
        return Ticket.query.get(_id=self.artifact_id)

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

    @property
    def assigned_to(self):
        if self.data.assigned_to_id is None:
            return None
        return User.query.get(_id=self.data.assigned_to_id)

    def index(self):
        orig = self.original()
        if not orig:
            return None
        result = Snapshot.index(self)
        result.update(
            title='Version %d of %s' % (
                self.version, orig.summary),
            type_s='Ticket Snapshot',
            text=self.data.summary)
        # Tracker uses search with default solr parser. It would match only on
        # `text`, so we're appending all other field values into `text`, to match on it too.
        result['text'] += pformat(result.values())
        return result

class Bin(Artifact, ActivityObject):
    class __mongometa__:
        name = 'bin'

    type_s = 'Bin'
    _id = FieldProperty(schema.ObjectId)
    summary = FieldProperty(str, required=True, allow_none=False)
    terms = FieldProperty(str, if_missing='')
    sort = FieldProperty(str, if_missing='')

    @property
    def activity_name(self):
        return 'search bin %s' % self.summary

    def url(self):
        base = self.app_config.url() + 'search/?'
        params = dict(q=(h.really_unicode(self.terms).encode('utf-8') or ''))
        if self.sort:
            params['sort'] = self.sort
        return base + urllib.urlencode(params)

    def shorthand_id(self):
        return self.summary

    def index(self):
        result = Artifact.index(self)
        result.update(
            type_s=self.type_s,
            summary_t=self.summary,
            terms_s=self.terms)
        return result

class Ticket(VersionedArtifact, ActivityObject, VotableArtifact):
    class __mongometa__:
        name = 'ticket'
        history_class = TicketHistory
        indexes = [
            'ticket_num',
            'app_config_id',
            ('app_config_id', 'custom_fields._milestone'),
            'import_id',
            ]
        unique_indexes = [
            ('app_config_id', 'ticket_num'),
            ]

    type_s = 'Ticket'
    _id = FieldProperty(schema.ObjectId)
    created_date = FieldProperty(datetime, if_missing=datetime.utcnow)

    ticket_num = FieldProperty(int, required=True, allow_none=False)
    summary = FieldProperty(str)
    description = FieldProperty(str, if_missing='')
    reported_by_id = ForeignIdProperty(User, if_missing=lambda:c.user._id)
    assigned_to_id = ForeignIdProperty(User, if_missing=None)
    milestone = FieldProperty(str, if_missing='')
    status = FieldProperty(str, if_missing='')
    custom_fields = FieldProperty({str:None})

    reported_by = RelationProperty(User, via='reported_by_id')

    @property
    def activity_name(self):
        return 'ticket #%s' % self.ticket_num

    @classmethod
    def new(cls):
        '''Create a new ticket, safely (ensuring a unique ticket_num'''
        while True:
            ticket_num = c.app.globals.next_ticket_num()
            ticket = cls(
                app_config_id=c.app.config._id,
                custom_fields=dict(),
                ticket_num=ticket_num)
            try:
                session(ticket).flush(ticket)
                h.log_action(log, 'opened').info('')
                return ticket
            except OperationFailure, err:
                if 'duplicate' in err.args[0]:
                    log.warning('Try to create duplicate ticket %s', ticket.url())
                    session(ticket).expunge(ticket)
                    continue
                raise

    def index(self):
        result = VersionedArtifact.index(self)
        result.update(
            title='Ticket %s' % self.ticket_num,
            version_i=self.version,
            type_s=self.type_s,
            created_date_dt=self.created_date,
            ticket_num_i=self.ticket_num,
            summary_t=self.summary,
            milestone_s=self.milestone,
            status_s=self.status,
            text=self.description,
            snippet_s=self.summary,
            private_b=self.private,
            votes_up_i=self.votes_up,
            votes_down_i=self.votes_down,
            votes_total_i=(self.votes_up - self.votes_down),
            import_id_s=ImportIdConverter.get().simplify(self.import_id)
            )
        for k, v in self.custom_fields.iteritems():
            # Pre solr-4.2.1 code expects all custom fields to be indexed
            # as strings.
            if not config.get_bool('new_solr'):
                result[k + '_s'] = unicode(v)

            # Now let's also index with proper Solr types.
            solr_type = self.app.globals.get_custom_field_solr_type(k)
            if solr_type:
                result[k + solr_type] = (v or
                        get_default_for_solr_type(solr_type))

        if self.reported_by:
            result['reported_by_s'] = self.reported_by.username
        if self.assigned_to:
            result['assigned_to_s'] = self.assigned_to.username
        # Tracker uses search with default solr parser. It would match only on
        # `text`, so we're appending all other field values into `text`, to
        # match on it too.
        result['text'] += pformat(result.values())
        return result

    @classmethod
    def attachment_class(cls):
        return TicketAttachment

    @classmethod
    def translate_query(cls, q, fields):
        q = super(Ticket, cls).translate_query(q, fields)
        cf = [f.name for f in c.app.globals.custom_fields]
        solr_field = '{0}{1}'
        solr_type = '_s'
        for f in cf:
            # Solr 4.2.1 index contains properly typed custom fields, so we
            # can search on those instead of the old string-type solr fields.
            if config.get_bool('new_solr'):
                solr_type = (c.app.globals.get_custom_field_solr_type(f)
                        or solr_type)
            actual = solr_field.format(f, solr_type)
            q = q.replace(f + ':', actual + ':')
        return q

    @property
    def _milestone(self):
        milestone = None
        for fld in self.globals.milestone_fields:
            if fld.name == '_milestone':
                return self.custom_fields.get('_milestone', '')
        return milestone

    @property
    def assigned_to(self):
        if self.assigned_to_id is None: return None
        return User.query.get(_id=self.assigned_to_id)

    @property
    def reported_by_username(self):
        if self.reported_by:
            return self.reported_by.username
        return 'nobody'

    @property
    def assigned_to_username(self):
        if self.assigned_to:
            return self.assigned_to.username
        return 'nobody'

    @property
    def email_address(self):
        domain = '.'.join(reversed(self.app.url[1:-1].split('/'))).replace('_', '-')
        return '%s@%s%s' % (self.ticket_num, domain, config.common_suffix)

    @property
    def email_subject(self):
        return '#%s %s' % (self.ticket_num, self.summary)

    @LazyProperty
    def globals(self):
        return Globals.query.get(app_config_id=self.app_config_id)

    @property
    def open_or_closed(self):
        return 'closed' if self.status in c.app.globals.set_of_closed_status_names else 'open'

    @property
    def monitoring_email(self):
        return c.app.config.options.get('TicketMonitoringEmail')

    @property
    def notify_post(self):
        monitoring_type = c.app.config.options.get('TicketMonitoringType')
        return monitoring_type == 'AllTicketChanges' or (
                monitoring_type == 'AllPublicTicketChanges' and
                not self.private)

    def get_custom_user(self, custom_user_field_name):
        fld = None
        for f in c.app.globals.custom_fields:
            if f.name == custom_user_field_name:
                fld = f
                break
        if not fld:
            raise KeyError, 'Custom field "%s" does not exist.' % custom_user_field_name
        if fld.type != 'user':
            raise TypeError, 'Custom field "%s" is of type "%s"; expected ' \
                             'type "user".' % (custom_user_field_name, fld.type)
        username = self.custom_fields.get(custom_user_field_name)
        if not username:
            return None
        user = self.app_config.project.user_in_project(username)
        if user == User.anonymous():
            return None
        return user

    def _get_private(self):
        return bool(self.acl)

    def _set_private(self, bool_flag):
        if bool_flag:
            role_developer = ProjectRole.by_name('Developer')
            role_creator = self.reported_by.project_role()
            _allow_all = lambda role, perms: [ACE.allow(role._id, perm) for perm in perms]
            # maintain existing access for developers and the ticket creator,
            # but revoke all access for everyone else
            self.acl = _allow_all(role_developer, security.all_allowed(self, role_developer)) \
                     + _allow_all(role_creator, security.all_allowed(self, role_creator)) \
                     + [DENY_ALL]
        else:
            self.acl = []
    private = property(_get_private, _set_private)

    def commit(self):
        VersionedArtifact.commit(self)
        monitoring_email = self.app.config.options.get('TicketMonitoringEmail')
        if self.version > 1:
            hist = TicketHistory.query.get(artifact_id=self._id, version=self.version-1)
            old = hist.data
            changes = ['Ticket %s has been modified: %s' % (
                    self.ticket_num, self.summary),
                       'Edited By: %s (%s)' % (c.user.get_pref('display_name'), c.user.username)]
            fields = [
                ('Summary', old.summary, self.summary),
                ('Status', old.status, self.status) ]
            if old.status != self.status and self.status in c.app.globals.set_of_closed_status_names:
                h.log_action(log, 'closed').info('')
                g.statsUpdater.ticketEvent("closed", self, self.project, self.assigned_to)
            for key in self.custom_fields:
                fields.append((key, old.custom_fields.get(key, ''), self.custom_fields[key]))
            for title, o, n in fields:
                if o != n:
                    changes.append('%s updated: %r => %r' % (
                            title, o, n))
            o = hist.assigned_to
            n = self.assigned_to
            if o != n:
                changes.append('Owner updated: %r => %r' % (
                        o and o.username, n and n.username))
                self.subscribe(user=n)
                g.statsUpdater.ticketEvent("assigned", self, self.project, n)
                if o:
                    g.statsUpdater.ticketEvent("revoked", self, self.project, o)
            if old.description != self.description:
                changes.append('Description updated:')
                changes.append('\n'.join(
                        difflib.unified_diff(
                            a=old.description.split('\n'),
                            b=self.description.split('\n'),
                            fromfile='description-old',
                            tofile='description-new')))
            description = '\n'.join(changes)
        else:
            self.subscribe()
            if self.assigned_to_id:
                user = User.query.get(_id=self.assigned_to_id)
                g.statsUpdater.ticketEvent("assigned", self, self.project, user)
                self.subscribe(user=user)
            description = ''
            subject = self.email_subject
            Thread.new(discussion_id=self.app_config.discussion_id,
                   ref_id=self.index_id())
            n = Notification.post(artifact=self, topic='metadata', text=description, subject=subject)
            if monitoring_email and n and (not self.private or
                    self.app.config.options.get('TicketMonitoringType') in (
                        'NewTicketsOnly', 'AllTicketChanges')):
                n.send_simple(monitoring_email)
        Feed.post(
            self,
            title=self.summary,
            description=description if description else self.description,
            author=self.reported_by,
            pubdate=self.created_date)

    def url(self):
        return self.app_config.url() + str(self.ticket_num) + '/'

    def shorthand_id(self):
        return '#' + str(self.ticket_num)

    def assigned_to_name(self):
        who = self.assigned_to
        if who in (None, User.anonymous()): return 'nobody'
        return who.get_pref('display_name')

    @property
    def attachments(self):
        return TicketAttachment.query.find(dict(
            app_config_id=self.app_config_id, artifact_id=self._id, type='attachment'))

    def update(self, ticket_form):
        # update is not allowed to change the ticket_num
        ticket_form.pop('ticket_num', None)
        self.labels = ticket_form.pop('labels', [])
        custom_users = set()
        other_custom_fields = set()
        for cf in self.globals.custom_fields or []:
            (custom_users if cf['type'] == 'user' else
             other_custom_fields).add(cf['name'])
            if cf['type'] == 'boolean' and 'custom_fields.' + cf['name'] not in ticket_form:
                self.custom_fields[cf['name']] = 'False'
        # this has to happen because the milestone custom field has special layout treatment
        if '_milestone' in ticket_form:
            other_custom_fields.add('_milestone')
            milestone = ticket_form.pop('_milestone', None)
            if 'custom_fields' not in ticket_form:
                ticket_form['custom_fields'] = dict()
            ticket_form['custom_fields']['_milestone'] = milestone
        attachment = None
        if 'attachment' in ticket_form:
            attachment = ticket_form.pop('attachment')
        for k, v in ticket_form.iteritems():
            if k == 'assigned_to':
                if v:
                    user = c.project.user_in_project(v)
                    if user:
                        self.assigned_to_id = user._id
            else:
                setattr(self, k, v)
        if 'custom_fields' in ticket_form:
            for k,v in ticket_form['custom_fields'].iteritems():
                if k in custom_users:
                    # restrict custom user field values to project members
                    user = self.app_config.project.user_in_project(v)
                    self.custom_fields[k] = user.username \
                        if user and user != User.anonymous() else ''
                elif k in other_custom_fields:
                    # strings are good enough for any other custom fields
                    self.custom_fields[k] = v
        self.commit()
        if attachment is not None:
            self.attach(
                attachment.filename, attachment.file,
                content_type=attachment.type)

    def _move_attach(self, attachments, attach_metadata, app_config):
        for attach in attachments:
            attach.app_config_id = app_config._id
            if attach.attachment_type == 'DiscussionAttachment':
                attach.discussion_id = app_config.discussion_id
            attach_thumb = BaseAttachment.query.get(filename=attach.filename, **attach_metadata)
            if attach_thumb:
                if attach_thumb.attachment_type == 'DiscussionAttachment':
                    attach_thumb.discussion_id = app_config.discussion_id
                attach_thumb.app_config_id = app_config._id

    def move(self, app_config, notify=True):
        '''Move ticket from current tickets app to tickets app with given app_config'''
        app = app_config.project.app_instance(app_config)
        prior_url = self.url()
        prior_app = self.app
        attachments = self.attachments
        attach_metadata = BaseAttachment.metadata_for(self)
        prior_cfs = [
            (cf['name'], cf['type'], cf['label'])
            for cf in prior_app.globals.custom_fields or []]
        new_cfs = [
            (cf['name'], cf['type'], cf['label'])
            for cf in app.globals.custom_fields or []]
        skipped_fields = []
        user_fields = []
        for cf in prior_cfs:
            if cf not in new_cfs:  # can't convert
                skipped_fields.append(cf)
            elif cf[1] == 'user':  # can convert and field type == user
                user_fields.append(cf)
        messages = []
        for cf in skipped_fields:
            name = cf[0]
            messages.append('- **%s**: %s' % (name, self.custom_fields.get(name, '')))
        for cf in user_fields:
            name = cf[0]
            username = self.custom_fields.get(name, None)
            user = app_config.project.user_in_project(username)
            if not user or user == User.anonymous():
                messages.append('- **%s**: %s (user not in project)' % (name, username))
                self.custom_fields[name] = ''
        # special case: not custom user field (assigned_to_id)
        user = self.assigned_to
        if user and not app_config.project.user_in_project(user.username):
            messages.append('- **assigned_to**: %s (user not in project)' % user.username)
            self.assigned_to_id = None

        custom_fields = {}
        for cf in new_cfs:
            fn, ft, fl = cf
            old_val = self.custom_fields.get(fn, None)
            if old_val is None:
                custom_fields[fn] = None if ft == 'user' else ''
            custom_fields[fn] = old_val
        self.custom_fields = custom_fields

        # move ticket. ensure unique ticket_num
        while True:
            with h.push_context(app_config.project_id, app_config_id=app_config._id):
                ticket_num = app.globals.next_ticket_num()
            self.ticket_num = ticket_num
            self.app_config_id = app_config._id
            new_url = app_config.url() + str(self.ticket_num) + '/'
            try:
                session(self).flush(self)
                h.log_action(log, 'moved').info('Ticket %s moved to %s' % (prior_url, new_url))
                break
            except OperationFailure, err:
                if 'duplicate' in err.args[0]:
                    log.warning('Try to create duplicate ticket %s when moving from %s' % (new_url, prior_url))
                    session(self).expunge(self)
                    continue

        attach_metadata['type'] = 'thumbnail'
        self._move_attach(attachments, attach_metadata, app_config)

        # move ticket's discussion thread, thus all new commnets will go to a new ticket's feed
        self.discussion_thread.app_config_id = app_config._id
        self.discussion_thread.discussion_id = app_config.discussion_id
        for post in self.discussion_thread.posts:
            attach_metadata = BaseAttachment.metadata_for(post)
            attach_metadata['type'] = 'thumbnail'
            self._move_attach(post.attachments, attach_metadata, app_config)
            post.app_config_id = app_config._id
            post.app_id = app_config._id
            post.discussion_id = app_config.discussion_id

        session(self.discussion_thread).flush(self.discussion_thread)
        # need this to reset app_config RelationProperty on ticket to a new one
        session(self.discussion_thread).expunge(self.discussion_thread)
        session(self).expunge(self)
        ticket = Ticket.query.find(dict(
            app_config_id=app_config._id, ticket_num=self.ticket_num)).first()

        message = 'Ticket moved from %s' % prior_url
        if messages:
            message += '\n\nCan\'t be converted:\n\n'
        message += '\n'.join(messages)
        with h.push_context(ticket.project_id, app_config_id=app_config._id):
            ticket.discussion_thread.add_post(text=message, notify=notify)
        return ticket

    def __json__(self):
        return dict(super(Ticket,self).__json__(),
            created_date=self.created_date,
            ticket_num=self.ticket_num,
            summary=self.summary,
            description=self.description,
            reported_by=self.reported_by_username,
            assigned_to=self.assigned_to_username,
            reported_by_id=self.reported_by_id and str(self.reported_by_id) or None,
            assigned_to_id=self.assigned_to_id and str(self.assigned_to_id) or None,
            status=self.status,
            private=self.private,
            attachments=[dict(bytes=attach.length,
                              url=h.absurl(attach.url())) for attach in self.attachments],
            custom_fields=self.custom_fields)

    @classmethod
    def paged_query(cls, app_config, user, query, limit=None, page=0, sort=None, deleted=False, **kw):
        """
        Query tickets, filtering for 'read' permission, sorting and paginating the result.

        See also paged_search which does a solr search
        """
        limit, page, start = g.handle_paging(limit, page, default=25)
        q = cls.query.find(dict(query, app_config_id=app_config._id, deleted=deleted))
        q = q.sort('ticket_num', pymongo.DESCENDING)
        if sort:
            field, direction = sort.split()
            if field.startswith('_'):
                field = 'custom_fields.' + field
            direction = dict(
                asc=pymongo.ASCENDING,
                desc=pymongo.DESCENDING)[direction]
            q = q.sort(field, direction)
        q = q.skip(start)
        q = q.limit(limit)
        tickets = []
        count = q.count()
        for t in q:
            if security.has_access(t, 'read', user, app_config.project.root_project):
                tickets.append(t)
            else:
                count = count -1

        return dict(
            tickets=tickets,
            count=count, q=json.dumps(query), limit=limit, page=page, sort=sort,
            **kw)

    @classmethod
    def paged_search(cls, app_config, user, q, limit=None, page=0, sort=None, show_deleted=False, **kw):
        """Query tickets from Solr, filtering for 'read' permission, sorting and paginating the result.

        See also paged_query which does a mongo search.

        We do the sorting and skipping right in SOLR, before we ever ask
        Mongo for the actual tickets.  Other keywords for
        search_artifact (e.g., history) or for SOLR are accepted through
        kw.  The output is intended to be used directly in templates,
        e.g., exposed controller methods can just:

            return paged_query(q, ...)

        If you want all the results at once instead of paged you have
        these options:
          - don't call this routine, search directly in mongo
          - call this routine with a very high limit and TEST that
            count<=limit in the result
        limit=-1 is NOT recognized as 'all'.  500 is a reasonable limit.
        """

        limit, page, start = g.handle_paging(limit, page, default=25)
        count = 0
        tickets = []
        refined_sort = sort if sort else 'ticket_num_i desc'
        if  'ticket_num_i' not in refined_sort:
            refined_sort += ',ticket_num_i asc'
        try:
            if q:
                matches = search_artifact(
                    cls, q, short_timeout=True,
                    rows=limit, sort=refined_sort, start=start, fl='ticket_num_i', **kw)
            else:
                matches = None
            solr_error = None
        except SearchError as e:
            solr_error = e
            matches = []
        if matches:
            count = matches.hits
            # ticket_numbers is in sorted order
            ticket_numbers = [match['ticket_num_i'] for match in matches.docs]
            # but query, unfortunately, returns results in arbitrary order
            query = cls.query.find(dict(app_config_id=app_config._id, ticket_num={'$in':ticket_numbers}))
            # so stick all the results in a dictionary...
            ticket_for_num = {}
            for t in query:
                ticket_for_num[t.ticket_num] = t
            # and pull them out in the order given by ticket_numbers
            tickets = []
            for tn in ticket_numbers:
                if tn in ticket_for_num:
                    show_deleted = show_deleted and security.has_access(ticket_for_num[tn], 'delete', user, app_config.project.root_project)
                    if (security.has_access(ticket_for_num[tn], 'read', user, app_config.project.root_project) and
                        (show_deleted or ticket_for_num[tn].deleted==False)):
                        tickets.append(ticket_for_num[tn])
                    else:
                        count = count -1
        return dict(tickets=tickets,
                    count=count, q=q, limit=limit, page=page, sort=sort,
                    solr_error=solr_error, **kw)

class TicketAttachment(BaseAttachment):
    thumbnail_size = (100, 100)
    ArtifactType=Ticket
    class __mongometa__:
        polymorphic_identity='TicketAttachment'
    attachment_type=FieldProperty(str, if_missing='TicketAttachment')

Mapper.compile_all()
