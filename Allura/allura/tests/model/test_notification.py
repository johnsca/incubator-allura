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

import unittest
from datetime import timedelta
import collections

from pylons import tmpl_context as c, app_globals as g
from nose.tools import assert_equal, assert_in
from ming.orm import ThreadLocalORMSession
import mock
import bson

from alluratest.controller import setup_basic_test, setup_global_objects, REGISTRY
from allura import model as M
from allura.lib import helpers as h
from allura.tests import decorators as td
from forgewiki import model as WM

class TestNotification(unittest.TestCase):

    def setUp(self):
        setup_basic_test()
        self.setup_with_tools()

    @td.with_wiki
    def setup_with_tools(self):
        setup_global_objects()
        _clear_subscriptions()
        _clear_notifications()
        ThreadLocalORMSession.flush_all()
        ThreadLocalORMSession.close_all()
        M.notification.MAILBOX_QUIESCENT=None # disable message combining

    def test_subscribe_unsubscribe(self):
        M.Mailbox.subscribe(type='direct')
        ThreadLocalORMSession.flush_all()
        ThreadLocalORMSession.close_all()
        subscriptions = M.Mailbox.query.find(dict(
            project_id=c.project._id,
            app_config_id=c.app.config._id,
            user_id=c.user._id)).all()
        assert len(subscriptions) == 1
        assert subscriptions[0].type=='direct'
        assert M.Mailbox.query.find().count() == 1
        M.Mailbox.unsubscribe()
        ThreadLocalORMSession.flush_all()
        ThreadLocalORMSession.close_all()
        subscriptions = M.Mailbox.query.find(dict(
            project_id=c.project._id,
            app_config_id=c.app.config._id,
            user_id=c.user._id)).all()
        assert len(subscriptions) == 0
        assert M.Mailbox.query.find().count() == 0

class TestPostNotifications(unittest.TestCase):

    def setUp(self):
        setup_basic_test()
        self.setup_with_tools()

    @td.with_wiki
    def setup_with_tools(self):
        setup_global_objects()
        g.set_app('wiki')
        _clear_subscriptions()
        _clear_notifications()
        ThreadLocalORMSession.flush_all()
        ThreadLocalORMSession.close_all()
        self.pg = WM.Page.query.get(app_config_id=c.app.config._id)
        M.notification.MAILBOX_QUIESCENT=None # disable message combining
        while M.MonQTask.run_ready('setup'):
            ThreadLocalORMSession.flush_all()

    def test_post_notification(self):
        self._post_notification()
        ThreadLocalORMSession.flush_all()
        M.MonQTask.list()
        t = M.MonQTask.get()
        assert t.args[1] == self.pg.index_id()

    def test_post_user_notification(self):
        u = M.User.query.get(username='test-admin')
        n = M.Notification.post_user(u, self.pg, 'metadata')
        ThreadLocalORMSession.flush_all()
        ThreadLocalORMSession.close_all()
        flash_msgs = list(h.pop_user_notifications(u))
        assert len(flash_msgs) == 1, flash_msgs
        msg = flash_msgs[0]
        assert msg['text'].startswith('Home modified by Test Admin')
        assert msg['subject'].startswith('[test:wiki]')
        flash_msgs = list(h.pop_user_notifications(u))
        assert not flash_msgs, flash_msgs

    def test_delivery(self):
        self._subscribe()
        self._post_notification()
        M.MonQTask.run_ready()
        ThreadLocalORMSession.flush_all()
        M.MonQTask.run_ready()
        ThreadLocalORMSession.flush_all()
        assert M.Mailbox.query.find().count()==1
        mbox = M.Mailbox.query.get()
        assert len(mbox.queue) == 1
        assert not mbox.queue_empty

    def test_email(self):
        self._subscribe()  # as current user: test-admin
        user2 = M.User.query.get(username='test-user-2')
        self._subscribe(user=user2)
        self._post_notification()
        ThreadLocalORMSession.flush_all()

        assert_equal(M.Notification.query.get()['from_address'], '"Test Admin" <test-admin@users.localhost>')
        assert_equal(M.Mailbox.query.find().count(), 2)

        M.MonQTask.run_ready()  # sends the notification out into "mailboxes", and from mailboxes into email tasks
        mboxes = M.Mailbox.query.find().all()
        assert_equal(len(mboxes), 2)
        assert_equal(len(mboxes[0].queue), 1)
        assert not mboxes[0].queue_empty
        assert_equal(len(mboxes[1].queue), 1)
        assert not mboxes[1].queue_empty

        email_tasks = M.MonQTask.query.find({'state': 'ready'}).all()
        assert_equal(len(email_tasks), 2)  # make sure both subscribers will get an email

        first_destinations = [e.kwargs['destinations'][0] for e in email_tasks]
        assert_in(str(c.user._id), first_destinations)
        assert_in(str(user2._id), first_destinations)
        assert_equal(email_tasks[0].kwargs['fromaddr'], '"Test Admin" <test-admin@users.localhost>')
        assert_equal(email_tasks[1].kwargs['fromaddr'], '"Test Admin" <test-admin@users.localhost>')
        assert email_tasks[0].kwargs['text'].startswith('Home modified by Test Admin')
        assert 'you indicated interest in ' in email_tasks[0].kwargs['text']

    def test_permissions(self):
        # Notification should only be delivered if user has read perms on the
        # artifact. The perm check happens just before the mail task is
        # posted.
        u = M.User.query.get(username='test-admin')
        self._subscribe(user=u)
        # Simulate a permission check failure.
        def patched_has_access(*args, **kw):
            def predicate(*args, **kw):
                return False
            return predicate
        from allura.model.notification import security
        orig = security.has_access
        security.has_access = patched_has_access
        try:
            # this will create a notification task
            self._post_notification()
            ThreadLocalORMSession.flush_all()
            # running the notification task will create a mail task if the
            # permission check passes...
            M.MonQTask.run_ready()
            ThreadLocalORMSession.flush_all()
            # ...but in this case it doesn't create a mail task since we
            # forced the perm check to fail
            assert M.MonQTask.get() == None
        finally:
            security.has_access = orig

    def _subscribe(self, **kw):
        self.pg.subscribe(type='direct', **kw)
        ThreadLocalORMSession.flush_all()
        ThreadLocalORMSession.close_all()

    def _post_notification(self):
        return M.Notification.post(self.pg, 'metadata')

class TestSubscriptionTypes(unittest.TestCase):

    def setUp(self):
        setup_basic_test()
        self.setup_with_tools()

    @td.with_wiki
    def setup_with_tools(self):
        setup_global_objects()
        g.set_app('wiki')
        _clear_subscriptions()
        _clear_notifications()
        ThreadLocalORMSession.flush_all()
        ThreadLocalORMSession.close_all()
        self.pg = WM.Page.query.get(app_config_id=c.app.config._id)
        M.notification.MAILBOX_QUIESCENT=None # disable message combining

    def test_direct_sub(self):
        self._subscribe()
        self._post_notification(text='A')
        self._post_notification(text='B')
        ThreadLocalORMSession.flush_all()
        ThreadLocalORMSession.close_all()
        M.Mailbox.fire_ready()

    def test_digest_sub(self):
        self._subscribe(type='digest')
        self._post_notification(text='x'*1024)
        self._post_notification()
        M.Mailbox.fire_ready()

    def test_summary_sub(self):
        self._subscribe(type='summary')
        self._post_notification(text='x'*1024)
        self._post_notification()
        M.Mailbox.fire_ready()

    def test_message(self):
        self._test_message()

        self.setUp()
        self._test_message()

        self.setUp()
        M.notification.MAILBOX_QUIESCENT=timedelta(minutes=1)
        # will raise "assert msg is not None" since the new message is not 1 min old:
        self.assertRaises(AssertionError, self._test_message)

    def _test_message(self):
        self._subscribe()
        thd = M.Thread.query.get(ref_id=self.pg.index_id())
        thd.post('This is a very cool message')
        M.MonQTask.run_ready()
        ThreadLocalORMSession.flush_all()
        M.Mailbox.fire_ready()
        ThreadLocalORMSession.flush_all()
        ThreadLocalORMSession.close_all()
        msg = M.MonQTask.query.get(
                task_name='allura.tasks.mail_tasks.sendmail',
                state='ready')
        assert msg is not None
        assert 'Home@wiki.test.p' in msg.kwargs['reply_to']
        u = M.User.by_username('test-admin')
        assert str(u._id) in msg.kwargs['fromaddr'], msg.kwargs['fromaddr']

    def _clear_subscriptions(self):
        M.Mailbox.query.remove({})
        ThreadLocalORMSession.flush_all()
        ThreadLocalORMSession.close_all()

    def _subscribe(self, type='direct', topic=None):
        self.pg.subscribe(type=type, topic=topic)
        ThreadLocalORMSession.flush_all()
        ThreadLocalORMSession.close_all()

    def _post_notification(self, text=None):
        return M.Notification.post(self.pg, 'metadata', text=text)

    @mock.patch('allura.model.notification.defaultdict')
    @mock.patch('allura.model.notification.Notification')
    def test_direct_accumulation(self, mocked_notification, mocked_defaultdict):
        class OrderedDefaultDict(collections.OrderedDict):
            def __init__(self, factory=list, *a, **kw):
                self._factory = factory
                super(OrderedDefaultDict, self).__init__(*a, **kw)

            def __getitem__(self, key):
                if key not in self:
                    value = self[key] = self._factory()
                else:
                    value = super(OrderedDefaultDict, self).__getitem__(key)
                return value

        notifications = mocked_notification.query.find.return_value.all.return_value = [
                mock.Mock(_id='n0', topic='metadata', subject='s1', from_address='f1', reply_to_address='rt1', author_id='a1'),
                mock.Mock(_id='n1', topic='metadata', subject='s2', from_address='f2', reply_to_address='rt2', author_id='a2'),
                mock.Mock(_id='n2', topic='metadata', subject='s2', from_address='f2', reply_to_address='rt2', author_id='a2'),
                mock.Mock(_id='n3', topic='message', subject='s3', from_address='f3', reply_to_address='rt3', author_id='a3'),
                mock.Mock(_id='n4', topic='message', subject='s3', from_address='f3', reply_to_address='rt3', author_id='a3'),
            ]
        mocked_defaultdict.side_effect = OrderedDefaultDict

        u0 = bson.ObjectId()
        mbox = M.Mailbox(type='direct', user_id=u0, queue=['n0', 'n1', 'n2', 'n3', 'n4'])
        mbox.fire('now')

        mocked_notification.query.find.assert_called_once_with({'_id': {'$in': ['n0', 'n1', 'n2', 'n3', 'n4']}})
        # first notification should be sent direct, as its key values are unique
        notifications[0].send_direct.assert_called_once_with(u0)
        # next two notifications should be sent as a digest as they have matching key values
        mocked_notification.send_digest.assert_called_once_with(u0, 'f2', 's2', [notifications[1], notifications[2]], 'rt2')
        # final two should be sent direct even though they matching keys, as they are messages
        notifications[3].send_direct.assert_called_once_with(u0)
        notifications[4].send_direct.assert_called_once_with(u0)

def _clear_subscriptions():
        M.Mailbox.query.remove({})

def _clear_notifications():
        M.Notification.query.remove({})
