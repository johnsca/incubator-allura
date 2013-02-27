import pkg_resources
import unittest
from datetime import datetime, timedelta

from pylons import tmpl_context as c

from alluratest.controller import setup_basic_test, setup_global_objects
from allura.tests import decorators as td
from allura.model import User, Project, TroveCategory
from allura import model as M

from forgegit.tests import with_git

class TestUserStats(unittest.TestCase):

    def setUp(self):
        from allura.model import User

        setup_basic_test()
        setup_global_objects()
        self.user = User.register(dict(username='test-new-user',
            display_name='Test Stats'),
            make_project=False)

    def test_init_values(self):
        artifacts = self.user.stats.getArtifacts()
        tickets = self.user.stats.getTickets()
        commits = self.user.stats.getCommits()
        assert self.user.stats.tot_logins_count == 0
        assert artifacts['created'] == 0
        assert artifacts['modified'] == 0
        assert tickets['assigned'] == 0
        assert tickets['solved'] == 0
        assert tickets['revoked'] == 0
        assert tickets['averagesolvingtime'] is None
        assert commits['number'] == 0
        assert commits['lines'] == 0

        lmartifacts = self.user.stats.getLastMonthArtifacts()
        lmtickets = self.user.stats.getLastMonthTickets()
        lmcommits = self.user.stats.getLastMonthCommits()
        assert self.user.stats.getLastMonthLogins() == 0
        assert lmartifacts['created'] == 0
        assert lmartifacts['modified'] == 0
        assert lmtickets['assigned'] == 0
        assert lmtickets['solved'] == 0
        assert lmtickets['revoked'] == 0
        assert lmtickets['averagesolvingtime'] is None
        assert lmcommits['number'] == 0
        assert lmcommits['lines'] == 0

    @td.with_user_project('test-new-user')
    def test_create_artifact_stats(self):
        p = Project.query.get(shortname='u/test-new-user')
        topic = TroveCategory.query.get(shortname='scientific')

        init_lm_art = self.user.stats.getLastMonthArtifacts()
        init_art = self.user.stats.getArtifacts()
        init_art_wiki = self.user.stats.getArtifacts(art_type='Wiki')
        init_art_by_type = self.user.stats.getArtifactsByType()
        init_lm_art_by_type = self.user.stats.getLastMonthArtifactsByType()
        init_art_sci = self.user.stats.getArtifacts(category=topic._id)

        self.user.stats.addNewArtifact('Wiki', datetime.utcnow(), p)
        lm_art = self.user.stats.getLastMonthArtifacts()
        artifacts = self.user.stats.getArtifacts()
        art_wiki = self.user.stats.getArtifacts(art_type='Wiki')
        art_by_type = self.user.stats.getArtifactsByType()
        lm_art_by_type = self.user.stats.getLastMonthArtifactsByType()

        assert lm_art['created'] == init_lm_art['created'] + 1
        assert lm_art['modified'] == init_lm_art['modified']
        assert artifacts['created'] == init_art['created'] + 1
        assert artifacts['modified'] == init_art['modified']
        assert art_wiki['created'] == init_art_wiki['created'] + 1
        assert art_wiki['modified'] == init_art_wiki['modified']
        assert art_by_type['Wiki']['created'] == init_art_by_type['Wiki']['created'] + 1
        assert art_by_type['Wiki']['modified'] == init_art_by_type['Wiki']['modified']
        assert lm_art_by_type['Wiki']['created'] == init_lm_art_by_type['Wiki']['created'] + 1
        assert lm_art_by_type['Wiki']['modified'] == init_lm_art_by_type['Wiki']['modified']
        
        #In that case, last month stats should not be changed
        new_date = datetime.utcnow() + timedelta(-32)
        self.user.stats.addNewArtifact('Wiki', new_date, p)
        lm_art = self.user.stats.getLastMonthArtifacts()
        artifacts = self.user.stats.getArtifacts()
        art_wiki = self.user.stats.getArtifacts(art_type='Wiki')
        art_by_type = self.user.stats.getArtifactsByType()
        lm_art_by_type = self.user.stats.getLastMonthArtifactsByType()

        assert lm_art['created'] == init_lm_art['created'] + 1
        assert lm_art['modified'] == init_lm_art['modified']
        assert artifacts['created'] == init_art['created'] + 2
        assert artifacts['modified'] == init_art['modified']
        assert art_wiki['created'] == init_art_wiki['created'] + 2
        assert art_wiki['modified'] == init_art_wiki['modified']
        assert art_by_type['Wiki']['created'] == init_art_by_type['Wiki']['created'] + 2
        assert art_by_type['Wiki']['modified'] == init_art_by_type['Wiki']['modified']
        assert lm_art_by_type['Wiki']['created'] == init_lm_art_by_type['Wiki']['created'] + 1
        assert lm_art_by_type['Wiki']['modified'] == init_lm_art_by_type['Wiki']['modified']
        
        p.trove_topic = [topic._id]

        self.user.stats.addNewArtifact('Wiki', datetime.utcnow(), p)
        lm_art = self.user.stats.getLastMonthArtifacts()
        artifacts = self.user.stats.getArtifacts()
        art_wiki = self.user.stats.getArtifacts(art_type='Wiki')
        art_by_type = self.user.stats.getArtifactsByType()
        lm_art_by_type = self.user.stats.getLastMonthArtifactsByType()
        art_sci = self.user.stats.getArtifacts(category=topic._id)
        art_by_cat = self.user.stats.getArtifactsByCategory(detailed=True)

        assert lm_art['created'] == init_lm_art['created'] + 2
        assert lm_art['modified'] == init_lm_art['modified']
        assert artifacts['created'] == init_art['created'] + 3
        assert artifacts['modified'] == init_art['modified']
        assert art_wiki['created'] == init_art_wiki['created'] + 3
        assert art_wiki['modified'] == init_art_wiki['modified']
        assert art_by_type['Wiki']['created'] == init_art_by_type['Wiki']['created'] + 3
        assert art_by_type['Wiki']['modified'] == init_art_by_type['Wiki']['modified']
        assert lm_art_by_type['Wiki']['created'] == init_lm_art_by_type['Wiki']['created'] + 2
        assert lm_art_by_type['Wiki']['modified'] == init_lm_art_by_type['Wiki']['modified']
        assert art_sci['created'] == init_art_sci['created'] + 1
        assert art_sci['modified'] == init_art_sci['modified']
        assert dict(messagetype='Wiki', created= 1, modified = 0) in art_by_cat[topic]
        art_by_cat = self.user.stats.getArtifactsByCategory(detailed=False)
        assert art_by_cat[topic]['created'] == 1 and art_by_cat[topic]['modified'] == 0

    @td.with_user_project('test-new-user')
    def test_modify_artifact_stats(self):
        p = Project.query.get(shortname='u/test-new-user')
        topic = TroveCategory.query.get(shortname='scientific')

        init_lm_art = self.user.stats.getLastMonthArtifacts()
        init_art = self.user.stats.getArtifacts()
        init_art_wiki = self.user.stats.getArtifacts(art_type='Wiki')
        init_art_by_type = self.user.stats.getArtifactsByType()
        init_lm_art_by_type = self.user.stats.getLastMonthArtifactsByType()
        init_art_sci = self.user.stats.getArtifacts(category=topic._id)

        self.user.stats.addModifiedArtifact('Wiki', datetime.utcnow(), p)
        lm_art = self.user.stats.getLastMonthArtifacts()
        artifacts = self.user.stats.getArtifacts()
        art_wiki = self.user.stats.getArtifacts(art_type='Wiki')
        art_by_type = self.user.stats.getArtifactsByType()
        lm_art_by_type = self.user.stats.getLastMonthArtifactsByType()

        assert lm_art['created'] == init_lm_art['created']
        assert lm_art['modified'] == init_lm_art['modified'] + 1
        assert artifacts['created'] == init_art['created']
        assert artifacts['modified'] == init_art['modified'] + 1
        assert art_wiki['created'] == init_art_wiki['created']
        assert art_wiki['modified'] == init_art_wiki['modified'] + 1
        assert art_by_type['Wiki']['created'] == init_art_by_type['Wiki']['created']
        assert art_by_type['Wiki']['modified'] == init_art_by_type['Wiki']['modified'] + 1
        assert lm_art_by_type['Wiki']['created'] == init_lm_art_by_type['Wiki']['created']
        assert lm_art_by_type['Wiki']['modified'] == init_lm_art_by_type['Wiki']['modified'] + 1
        
        #In that case, last month stats should not be changed
        new_date = datetime.utcnow() + timedelta(-32)
        self.user.stats.addModifiedArtifact('Wiki', new_date, p)
        lm_art = self.user.stats.getLastMonthArtifacts()
        artifacts = self.user.stats.getArtifacts()
        art_wiki = self.user.stats.getArtifacts(art_type='Wiki')
        art_by_type = self.user.stats.getArtifactsByType()
        lm_art_by_type = self.user.stats.getLastMonthArtifactsByType()

        assert lm_art['created'] == init_lm_art['created'] 
        assert lm_art['modified'] == init_lm_art['modified'] + 1
        assert artifacts['created'] == init_art['created'] 
        assert artifacts['modified'] == init_art['modified'] + 2
        assert art_wiki['created'] == init_art_wiki['created']
        assert art_wiki['modified'] == init_art_wiki['modified'] + 2
        assert art_by_type['Wiki']['created'] == init_art_by_type['Wiki']['created']
        assert art_by_type['Wiki']['modified'] == init_art_by_type['Wiki']['modified'] + 2
        assert lm_art_by_type['Wiki']['created'] == init_lm_art_by_type['Wiki']['created']
        assert lm_art_by_type['Wiki']['modified'] == init_lm_art_by_type['Wiki']['modified'] + 1
        
        p.trove_topic = [topic._id]

        self.user.stats.addModifiedArtifact('Wiki', datetime.utcnow(), p)
        lm_art = self.user.stats.getLastMonthArtifacts()
        artifacts = self.user.stats.getArtifacts()
        art_wiki = self.user.stats.getArtifacts(art_type='Wiki')
        art_by_type = self.user.stats.getArtifactsByType()
        lm_art_by_type = self.user.stats.getLastMonthArtifactsByType()
        art_sci = self.user.stats.getArtifacts(category=topic._id)
        art_by_cat = self.user.stats.getArtifactsByCategory(detailed=True)

        assert lm_art['created'] == init_lm_art['created'] 
        assert lm_art['modified'] == init_lm_art['modified'] + 2
        assert artifacts['created'] == init_art['created']
        assert artifacts['modified'] == init_art['modified'] + 3
        assert art_wiki['created'] == init_art_wiki['created']
        assert art_wiki['modified'] == init_art_wiki['modified'] + 3
        assert art_by_type['Wiki']['created'] == init_art_by_type['Wiki']['created'] 
        assert art_by_type['Wiki']['modified'] == init_art_by_type['Wiki']['modified'] + 3
        assert lm_art_by_type['Wiki']['created'] == init_lm_art_by_type['Wiki']['created']
        assert lm_art_by_type['Wiki']['modified'] == init_lm_art_by_type['Wiki']['modified'] +2
        assert art_sci['created'] == init_art_sci['created']
        assert art_sci['modified'] == init_art_sci['modified'] + 1
        assert dict(messagetype='Wiki', created=0, modified=1) in art_by_cat[topic]
        art_by_cat = self.user.stats.getArtifactsByCategory(detailed=False)
        assert art_by_cat[topic]['created'] == 0 and art_by_cat[topic]['modified'] == 1

    @td.with_user_project('test-new-user')
    def test_ticket_stats(self):
        p = Project.query.get(shortname='u/test-new-user')
        topic = TroveCategory.query.get(shortname='scientific')
        create_time = datetime.utcnow() + timedelta(-5)

        init_lm_tickets_art = self.user.stats.getLastMonthArtifacts(art_type='Ticket')
        init_tickets_art = self.user.stats.getArtifacts(art_type='Ticket')
        init_tickets_sci_art = self.user.stats.getArtifacts(category=topic._id)
        init_tickets = self.user.stats.getTickets()
        init_lm_tickets = self.user.stats.getLastMonthTickets()

        self.user.stats.addNewArtifact('Ticket', create_time, p)
        lm_tickets_art = self.user.stats.getLastMonthArtifacts(art_type='Ticket')
        tickets_art = self.user.stats.getArtifacts(art_type='Ticket')
        tickets_sci_art = self.user.stats.getArtifacts(category=topic._id)

        assert lm_tickets_art['created'] == init_lm_tickets_art['created'] + 1
        assert lm_tickets_art['modified'] == init_lm_tickets_art['modified']
        assert tickets_art['created'] == init_tickets_art['created'] + 1
        assert tickets_art['modified'] == init_tickets_art['modified']
        assert tickets_sci_art['created'] == tickets_sci_art['created']
        assert tickets_sci_art['modified'] == tickets_sci_art['modified']
        
        p.trove_topic = [topic._id]

        self.user.stats.addAssignedTicket(create_time, p)
        tickets = self.user.stats.getTickets()
        lm_tickets = self.user.stats.getLastMonthTickets()

        assert tickets['assigned'] == init_tickets['assigned'] + 1 
        assert tickets['revoked'] == init_tickets['revoked']
        assert tickets['solved'] == init_tickets['solved'] 
        assert tickets['averagesolvingtime'] is None 
        assert lm_tickets['assigned'] == init_lm_tickets['assigned'] + 1 
        assert lm_tickets['revoked'] == init_lm_tickets['revoked']
        assert lm_tickets['solved'] == init_lm_tickets['solved'] 
        assert lm_tickets['averagesolvingtime'] is None 

        self.user.stats.addRevokedTicket(create_time + timedelta(-32), p)
        tickets = self.user.stats.getTickets()

        assert tickets['assigned'] == init_tickets['assigned'] + 1 
        assert tickets['revoked'] == init_tickets['revoked'] + 1
        assert tickets['solved'] == init_tickets['solved'] 
        assert tickets['averagesolvingtime'] is None 
        assert lm_tickets['assigned'] == init_lm_tickets['assigned'] + 1 
        assert lm_tickets['revoked'] == init_lm_tickets['revoked']
        assert lm_tickets['solved'] == init_lm_tickets['solved'] 
        assert lm_tickets['averagesolvingtime'] is None 

        self.user.stats.addClosedTicket(create_time, create_time + timedelta(1), p)
        tickets = self.user.stats.getTickets()
        lm_tickets = self.user.stats.getLastMonthTickets()

        assert tickets['assigned'] == init_tickets['assigned'] + 1 
        assert tickets['revoked'] == init_tickets['revoked'] + 1
        assert tickets['solved'] == init_tickets['solved'] + 1

        solving_time = dict(seconds=0,minutes=0,days=1,hours=0)
        assert tickets['averagesolvingtime'] == solving_time
        assert lm_tickets['assigned'] == init_lm_tickets['assigned'] + 1
        assert lm_tickets['revoked'] == init_lm_tickets['revoked']
        assert lm_tickets['solved'] == init_lm_tickets['solved'] + 1
        assert lm_tickets['averagesolvingtime'] == solving_time

        p.trove_topic = []
        self.user.stats.addClosedTicket(create_time, create_time + timedelta(3), p)
        tickets = self.user.stats.getTickets()
        lm_tickets = self.user.stats.getLastMonthTickets()

        solving_time = dict(seconds=0,minutes=0,days=2,hours=0)

        assert tickets['assigned'] == init_tickets['assigned'] + 1 
        assert tickets['revoked'] == init_tickets['revoked'] + 1
        assert tickets['solved'] == init_tickets['solved'] + 2
        assert tickets['averagesolvingtime'] == solving_time
        assert lm_tickets['assigned'] == init_lm_tickets['assigned'] + 1
        assert lm_tickets['revoked'] == init_lm_tickets['revoked']
        assert lm_tickets['solved'] == init_lm_tickets['solved'] + 2
        assert lm_tickets['averagesolvingtime'] == solving_time

        by_cat = self.user.stats.getTicketsByCategory()
        lm_by_cat = self.user.stats.getLastMonthTicketsByCategory()
        solving_time=dict(days=1,hours=0,minutes=0,seconds=0)

        assert by_cat[topic]['assigned'] == 1 
        assert by_cat[topic]['revoked'] == 1
        assert by_cat[topic]['solved'] == 1
        assert by_cat[topic]['averagesolvingtime'] == solving_time
        assert lm_by_cat[topic]['assigned'] == 1
        assert lm_by_cat[topic]['revoked'] == 0
        assert lm_by_cat[topic]['solved'] == 1
        assert lm_by_cat[topic]['averagesolvingtime'] == solving_time

    @with_git
    @td.with_user_project('test-new-user')
    def test_commit_stats(self):
        p = Project.query.get(shortname='u/test-new-user')
        topic = TroveCategory.query.get(shortname='scientific')
        commit_time = datetime.utcnow() + timedelta(-1)

        self.user.set_password('testpassword')
        addr = M.EmailAddress.upsert('rcopeland@geek.net')
        self.user.claim_address('rcopeland@geek.net')
        
        repo_dir = pkg_resources.resource_filename(
            'forgeuserstats', 'tests/data')

        c.app.repo.fs_path = repo_dir
        c.app.repo.name = 'testgit.git'
        repo = c.app.repo
        repo.refresh()
        commit = M.repo.Commit.query.get(_id=repo.heads[0]['object_id'])
        commit.repo = repo

        init_commits = self.user.stats.getCommits()
        assert init_commits['number'] == 4
        init_lmcommits = self.user.stats.getLastMonthCommits()
        assert init_lmcommits['number'] == 4
 
        p.trove_topic = [topic._id]
        self.user.stats.addCommit(commit, datetime.utcnow(), p)
        commits = self.user.stats.getCommits()
        assert commits['number'] == init_commits['number'] + 1
        assert commits['lines'] == init_commits['lines'] + 1
        lmcommits = self.user.stats.getLastMonthCommits()
        assert lmcommits['number'] == init_lmcommits['number'] + 1
        assert lmcommits['lines'] == init_lmcommits['lines'] + 1
        by_cat = self.user.stats.getCommitsByCategory()
        assert by_cat[topic]['number'] == 1
        assert by_cat[topic]['lines'] == 1
        lm_by_cat = self.user.stats.getLastMonthCommitsByCategory()
        assert lm_by_cat[topic]['number'] == 1
        assert lm_by_cat[topic]['lines'] == 1

        self.user.stats.addCommit(commit, datetime.utcnow() + timedelta(-40), p)
        commits = self.user.stats.getCommits()
        assert commits['number'] == init_commits['number'] + 2
        assert commits['lines'] == init_commits['lines'] + 2
        lmcommits = self.user.stats.getLastMonthCommits()
        assert lmcommits['number'] == init_lmcommits['number'] + 1
        assert lmcommits['lines'] == init_lmcommits['lines'] + 1
        by_cat = self.user.stats.getCommitsByCategory()
        assert by_cat[topic]['number'] == 2
        assert by_cat[topic]['lines'] == 2
        lm_by_cat = self.user.stats.getLastMonthCommitsByCategory()
        assert lm_by_cat[topic]['number'] == 1
        assert lm_by_cat[topic]['lines'] == 1

    @td.with_user_project('test-new-user')
    def test_login_stats(self):
        init_logins = self.user.stats.tot_logins_count
        init_lm_logins = self.user.stats.getLastMonthLogins()
        
        login_datetime = datetime.utcnow()
        self.user.stats.addLogin(login_datetime)
        logins = self.user.stats.tot_logins_count
        lm_logins = self.user.stats.getLastMonthLogins()
        assert logins == init_logins + 1 
        assert lm_logins == init_lm_logins + 1 
        assert abs(self.user.stats.last_login - login_datetime) < timedelta(seconds=1)

        self.user.stats.addLogin(datetime.utcnow() + timedelta(-32))
        logins = self.user.stats.tot_logins_count
        lm_logins = self.user.stats.getLastMonthLogins()
        assert logins == init_logins + 2
        assert lm_logins == init_lm_logins + 1 
        assert abs(self.user.stats.last_login - login_datetime) < timedelta(seconds=1)
