import pkg_resources
import unittest

from pylons import app_globals as g
from pylons import tmpl_context as c

from alluratest.controller import TestController, setup_basic_test, setup_global_objects
from allura.tests import decorators as td
from allura.lib import helpers as h
from allura.model import User
from allura import model as M

from forgegit.tests import with_git
from forgewiki import model as WM
from forgetracker import model as TM

class TestStats(TestController):

    def setUp(self):
        super(TestStats, self).setUp()
        p = M.Project.query.get(shortname='test')
        p.add_user(M.User.by_username('test-user'), ['Admin'])

    def test_login(self):
        user = User.by_username('test-user')
        init_logins = c.user.stats.tot_logins_count
        r = self.app.post('/auth/do_login', params=dict(
                username=user.username, password='foo'))

        assert user.stats.tot_logins_count == 1 + init_logins
        assert user.stats.getLastMonthLogins() == 1 + init_logins

    @td.with_tool('test', 'wiki', mount_point='wiki', mount_label='wiki', username='test-admin')
    def test_wiki_stats(self):
        initial_artifacts = c.user.stats.getArtifacts()
        initial_wiki = c.user.stats.getArtifacts(art_type="Wiki")

        self.app.post('/wiki/TestPage/update', 
            params=dict(title='TestPage', text='some text'),
            extra_environ=dict(username=str(c.user.username)))

        artifacts = c.user.stats.getArtifacts()
        wiki = c.user.stats.getArtifacts(art_type="Wiki")

        assert artifacts['created'] == 1 + initial_artifacts['created']
        assert artifacts['modified'] == initial_artifacts['modified']
        assert wiki['created'] == 1 + initial_wiki['created']
        assert wiki['modified'] == initial_wiki['modified']

        self.app.post('/wiki/TestPage2/update', 
            params=dict(title='TestPage2', text='some text'),
            extra_environ=dict(username=str(c.user.username)))

        artifacts = c.user.stats.getArtifacts()
        wiki = c.user.stats.getArtifacts(art_type="Wiki")

        assert artifacts['created'] == 2 + initial_artifacts['created']
        assert artifacts['modified'] == initial_artifacts['modified']
        assert wiki['created'] == 2 + initial_wiki['created']
        assert wiki['modified'] == initial_wiki['modified']

        self.app.post('/wiki/TestPage2/update', 
            params=dict(title='TestPage2', text='some modified text'),
            extra_environ=dict(username=str(c.user.username)))

        artifacts = c.user.stats.getArtifacts()
        wiki = c.user.stats.getArtifacts(art_type="Wiki")

        assert artifacts['created'] == 2 + initial_artifacts['created']
        assert artifacts['modified'] == 1 + initial_artifacts['modified']
        assert wiki['created'] == 2 + initial_wiki['created']
        assert wiki['modified'] == 1 + initial_wiki['modified']

    @td.with_tool('test', 'tickets', mount_point='tickets', mount_label='tickets', username='test-admin')
    def test_tracker_stats(self):
        initial_tickets = c.user.stats.getTickets()
        initial_tickets_artifacts = c.user.stats.getArtifacts(art_type="Ticket")

        r = self.app.post('/tickets/save_ticket', 
            params={'ticket_form.summary':'test',
                    'ticket_form.assigned_to' : str(c.user.username)},
            extra_environ=dict(username=str(c.user.username)))

        ticketnum = str(TM.Ticket.query.get(summary='test').ticket_num)

        tickets = c.user.stats.getTickets()
        tickets_artifacts = c.user.stats.getArtifacts(art_type="Ticket")

        assert tickets['assigned'] == initial_tickets['assigned'] + 1
        assert tickets['solved'] == initial_tickets['solved']
        assert tickets['revoked'] == initial_tickets['revoked']
        assert tickets_artifacts['created'] == initial_tickets_artifacts['created'] + 1
        assert tickets_artifacts['modified'] == initial_tickets_artifacts['modified']

        r = self.app.post('/tickets/%s/update_ticket_from_widget' % ticketnum, 
            params={'ticket_form.ticket_num' : ticketnum,
                    'ticket_form.summary':'footext3',
                    'ticket_form.status' : 'closed'},
            extra_environ=dict(username=str(c.user.username)))

        tickets = c.user.stats.getTickets()
        tickets_artifacts = c.user.stats.getArtifacts(art_type="Ticket")

        assert tickets['assigned'] == initial_tickets['assigned'] + 1
        assert tickets['solved'] == initial_tickets['solved'] + 1
        assert tickets['revoked'] == initial_tickets['revoked']
        assert tickets_artifacts['created'] == initial_tickets_artifacts['created'] + 1
        assert tickets_artifacts['modified'] == initial_tickets_artifacts['modified'] + 1

        r = self.app.post('/tickets/save_ticket', 
            params={'ticket_form.summary':'test2'},
            extra_environ=dict(username=str(c.user.username)))

        ticketnum = str(TM.Ticket.query.get(summary='test2').ticket_num)
        
        tickets = c.user.stats.getTickets()
        tickets_artifacts = c.user.stats.getArtifacts(art_type="Ticket")

        assert tickets['assigned'] == initial_tickets['assigned'] + 1
        assert tickets['solved'] == initial_tickets['solved'] + 1
        assert tickets['revoked'] == initial_tickets['revoked']
        assert tickets_artifacts['created'] == initial_tickets_artifacts['created'] + 2
        assert tickets_artifacts['modified'] == initial_tickets_artifacts['modified'] + 1

        r = self.app.post('/tickets/%s/update_ticket_from_widget' % ticketnum, 
            params={'ticket_form.ticket_num' : ticketnum,
                    'ticket_form.summary':'test2',
                    'ticket_form.assigned_to' : str(c.user.username)},
            extra_environ=dict(username=str(c.user.username)))

        tickets = c.user.stats.getTickets()
        tickets_artifacts = c.user.stats.getArtifacts(art_type="Ticket")
        
        assert tickets['assigned'] == initial_tickets['assigned'] + 2
        assert tickets['solved'] == initial_tickets['solved'] + 1
        assert tickets['revoked'] == initial_tickets['revoked']
        assert tickets_artifacts['created'] == initial_tickets_artifacts['created'] + 2
        assert tickets_artifacts['modified'] == initial_tickets_artifacts['modified'] + 2

        r = self.app.post('/tickets/%s/update_ticket_from_widget' % ticketnum, 
            params={'ticket_form.ticket_num' : ticketnum,
                    'ticket_form.summary':'test2',
                    'ticket_form.assigned_to' : 'test-user'},
            extra_environ=dict(username=str(c.user.username)))

        tickets = c.user.stats.getTickets()
        tickets_artifacts = c.user.stats.getArtifacts(art_type="Ticket")
        
        assert tickets['assigned'] == initial_tickets['assigned'] + 2
        assert tickets['solved'] == initial_tickets['solved'] + 1
        assert tickets['revoked'] == initial_tickets['revoked'] + 1
        assert tickets_artifacts['created'] == initial_tickets_artifacts['created'] + 2
        assert tickets_artifacts['modified'] == initial_tickets_artifacts['modified'] + 3

class TestGitCommit(unittest.TestCase, TestController):

    def setUp(self):
        setup_basic_test()

        user = User.by_username('test-admin')
        user.set_password('testpassword')
        addr = M.EmailAddress.upsert('rcopeland@geek.net')
        user.claim_address('rcopeland@geek.net')
        self.setup_with_tools()

    @with_git
    @td.with_wiki
    def setup_with_tools(self):
        setup_global_objects()
        h.set_context('test', 'src-git', neighborhood='Projects')
        repo_dir = pkg_resources.resource_filename(
            'forgeuserstats', 'tests/data')
        c.app.repo.fs_path = repo_dir
        c.app.repo.name = 'testgit.git'
        self.repo = c.app.repo
        self.repo.refresh()
        self.rev = M.repo.Commit.query.get(_id=self.repo.heads[0]['object_id'])
        self.rev.repo = self.repo

    @td.with_user_project('test-admin')
    def test_commit(self):
        commits = c.user.stats.getCommits()
        assert commits['number'] == 4
        lmcommits = c.user.stats.getLastMonthCommits()
        assert lmcommits['number'] == 4