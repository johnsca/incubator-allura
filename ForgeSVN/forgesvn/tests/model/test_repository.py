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

import os
import shutil
import unittest
import pkg_resources
from itertools import count, product
from datetime import datetime
from zipfile import ZipFile

from collections import defaultdict
from pylons import tmpl_context as c, app_globals as g
import mock
from nose.tools import assert_equal
import tg
import ming
from ming.base import Object
from ming.orm import session, ThreadLocalORMSession
from testfixtures import TempDirectory
from IPython.testing.decorators import onlyif

from alluratest.controller import setup_basic_test, setup_global_objects
from allura import model as M
from allura.model.repo_refresh import send_notifications
from allura.lib import helpers as h
from allura.tests import decorators as td
from allura.tests.model.test_repo import RepoImplTestBase

from forgesvn import model as SM
from forgesvn.model.svn import svn_path_exists
from forgesvn.tests import with_svn

class TestNewRepo(unittest.TestCase):

    def setUp(self):
        setup_basic_test()
        self.setup_with_tools()

    @with_svn
    def setup_with_tools(self):
        setup_global_objects()
        h.set_context('test', 'src', neighborhood='Projects')
        repo_dir = pkg_resources.resource_filename(
            'forgesvn', 'tests/data/')
        self.repo = SM.Repository(
            name='testsvn',
            fs_path=repo_dir,
            url_path = '/test/',
            tool = 'svn',
            status = 'creating')
        self.repo.refresh()
        self.rev = self.repo.commit('HEAD')
        ThreadLocalORMSession.flush_all()
        ThreadLocalORMSession.close_all()

    def test_last_commit_for(self):
        tree = self.rev.tree
        for row in tree.ls():
            assert row['last_commit']['author'] is not None

    def test_commit(self):
        assert self.rev.primary() is self.rev
        assert self.rev.index_id().startswith('allura/model/repo/Commit#')
        self.rev.author_url
        self.rev.committer_url
        assert self.rev.tree._id == self.rev.tree_id
        assert self.rev.summary == self.rev.message.splitlines()[0]
        assert self.rev.shorthand_id() == '[r5]'
        assert self.rev.symbolic_ids == ([], [])
        assert self.rev.url() == (
            '/p/test/src/5/')
        all_cis = self.repo.log(self.rev._id, 0, 1000)
        assert len(all_cis) == 5
        assert self.repo.log(self.rev._id, 1,1000) == all_cis[1:]
        assert self.repo.log(self.rev._id, 0,3) == all_cis[:3]
        assert self.repo.log(self.rev._id, 1,2) == all_cis[1:3]
        for ci in all_cis:
            ci.context()
        self.rev.tree.ls()
        assert self.rev.tree.readme() == (
            'README', 'This is readme\nAnother Line\n')
        assert self.rev.tree.path() == '/'
        assert self.rev.tree.url() == (
            '/p/test/src/5/tree/')
        self.rev.tree.by_name['README']
        assert self.rev.tree.is_blob('README') == True
        assert self.rev.tree['a']['b']['c'].ls() == []
        self.assertRaises(KeyError, lambda:self.rev.tree['a']['b']['d'])

class TestSVNRepo(unittest.TestCase, RepoImplTestBase):

    def setUp(self):
        setup_basic_test()
        self.setup_with_tools()

    @with_svn
    def setup_with_tools(self):
        setup_global_objects()
        h.set_context('test', 'src', neighborhood='Projects')
        repo_dir = pkg_resources.resource_filename(
            'forgesvn', 'tests/data/')
        self.repo = SM.Repository(
            name='testsvn',
            fs_path=repo_dir,
            url_path = '/test/',
            tool = 'svn',
            status = 'creating')
        self.repo.refresh()
        ThreadLocalORMSession.flush_all()
        ThreadLocalORMSession.close_all()

    def test_init(self):
        repo = SM.Repository(
            name='testsvn',
            fs_path=g.tmpdir+'/',
            url_path = '/test/',
            tool = 'svn',
            status = 'creating')
        dirname = os.path.join(repo.fs_path, repo.name)
        if os.path.exists(dirname):
            shutil.rmtree(dirname)
        repo.init()
        shutil.rmtree(dirname)

    def test_fork(self):
        repo = SM.Repository(
            name='testsvn',
            fs_path=g.tmpdir+'/',
            url_path = '/test/',
            tool = 'svn',
            status = 'creating')
        repo_path = pkg_resources.resource_filename(
            'forgesvn', 'tests/data/testsvn')
        dirname = os.path.join(repo.fs_path, repo.name)
        if os.path.exists(dirname):
            shutil.rmtree(dirname)
        repo.init()
        repo._impl.clone_from('file://' + repo_path)
        assert not os.path.exists(os.path.join(g.tmpdir, 'testsvn/hooks/pre-revprop-change'))
        assert os.path.exists(os.path.join(g.tmpdir, 'testsvn/hooks/post-commit'))
        assert os.access(os.path.join(g.tmpdir, 'testsvn/hooks/post-commit'), os.X_OK)
        with open(os.path.join(g.tmpdir, 'testsvn/hooks/post-commit')) as f:
            c = f.read()
        self.assertIn('curl -s http://localhost//auth/refresh_repo/p/test/src/\n', c)
        self.assertIn('exec $DIR/post-commit-user "$@"\n', c)

        repo.refresh(notify=False)
        assert len(repo.log())

        shutil.rmtree(dirname)

    @mock.patch('forgesvn.model.svn.tg')
    def test_can_hotcopy(self, tg):
        from forgesvn.model.svn import SVNImplementation
        func = SVNImplementation.can_hotcopy
        obj = mock.Mock(spec=SVNImplementation)
        for combo in product(
                ['file:///myfile', 'http://myfile'],
                [True, False],
                ['version 1.7', 'version 1.6', 'version 2.0.3']):
            source_url = combo[0]
            tg.config = {'scm.svn.hotcopy': combo[1]}
            stdout = combo[2]
            obj.check_call.return_value = stdout, ''
            expected = (source_url.startswith('file://') and
                    tg.config['scm.svn.hotcopy'] and
                    stdout != 'version 1.6')
            result = func(obj, source_url)
            assert result == expected

    @mock.patch('forgesvn.model.svn.g.post_event')
    def test_clone(self, post_event):
        repo = SM.Repository(
            name='testsvn',
            fs_path=g.tmpdir+'/',
            url_path = '/test/',
            tool = 'svn',
            status = 'creating')
        repo_path = pkg_resources.resource_filename(
            'forgesvn', 'tests/data/testsvn')
        dirname = os.path.join(repo.fs_path, repo.name)
        if os.path.exists(dirname):
            shutil.rmtree(dirname)
        repo.init()
        repo._impl.clone_from('file://' + repo_path)
        assert not os.path.exists(os.path.join(g.tmpdir, 'testsvn/hooks/pre-revprop-change'))
        assert os.path.exists(os.path.join(g.tmpdir, 'testsvn/hooks/post-commit'))
        assert os.access(os.path.join(g.tmpdir, 'testsvn/hooks/post-commit'), os.X_OK)
        with open(os.path.join(g.tmpdir, 'testsvn/hooks/post-commit')) as f:
            c = f.read()
        self.assertIn('curl -s http://localhost//auth/refresh_repo/p/test/src/\n', c)
        self.assertIn('exec $DIR/post-commit-user "$@"\n', c)

        repo.refresh(notify=False)
        assert len(repo.log())

        shutil.rmtree(dirname)

    def test_index(self):
        i = self.repo.index()
        assert i['type_s'] == 'SVN Repository', i

    def test_log(self):
        for entry in self.repo.log():
            assert entry.committed.name == 'rick446'
            assert entry.message
            print '=='
            print entry._id
            print entry.message
            print entry.diffs

    def test_paged_diffs(self):
        entry = self.repo.log(2, limit=1)[0]
        self.assertEqual(entry.diffs, entry.paged_diffs())
        self.assertEqual(entry.diffs, entry.paged_diffs(start=0))
        expected =  dict(
                copied=[], changed=[], removed=[],
                added=['/a/b', '/a/b/c'], total=4)
        actual = entry.paged_diffs(start=1, end=3)
        self.assertEqual(expected, actual)

        empty = M.repo.Commit().paged_diffs()
        self.assertEqual(sorted(actual.keys()), sorted(empty.keys()))

    def test_diff_create_file(self):
        entry = self.repo.log(1, limit=1)[0]
        self.assertEqual(
            entry.diffs, dict(
                copied=[], changed=[],
                removed=[], added=['/README'], total=1))

    def test_diff_create_path(self):
        entry = self.repo.log(2, limit=1)[0]
        self.assertEqual(
            entry.diffs, dict(
                copied=[], changed=[], removed=[],
                added=[
                    '/a', '/a/b', '/a/b/c',
                    '/a/b/c/hello.txt'], total=4))

    def test_diff_modify_file(self):
        entry = self.repo.log(3, limit=1)[0]
        self.assertEqual(
            entry.diffs, dict(
                copied=[], changed=['/README'],
                removed=[], added=[], total=1))

    def test_diff_delete(self):
        entry = self.repo.log(4, limit=1)[0]
        self.assertEqual(
            entry.diffs, dict(
                copied=[], changed=[],
                removed=['/a/b/c/hello.txt'], added=[], total=1))

    def test_diff_copy(self):
        # Copies are currently only detected as 'add'
        entry = self.repo.log(5, limit=1)[0]
        self.assertEqual(
            entry.diffs, dict(
                copied=[], changed=[],
                removed=[], added=['/b'], total=1))

    def test_commit(self):
        entry = self.repo.commit(1)
        assert entry.committed.name == 'rick446'
        assert entry.message

    def test_svn_path_exists(self):
        repo_path = pkg_resources.resource_filename(
            'forgesvn', 'tests/data/testsvn')
        assert svn_path_exists("file://%s/a" % repo_path)
        assert svn_path_exists("file://%s" % repo_path)
        assert not svn_path_exists("file://%s/badpath" % repo_path)

    def test_count_revisions(self):
        ci = mock.Mock(_id='deadbeef:100')
        self.assertEqual(self.repo.count_revisions(ci), 100)

    @onlyif(os.path.exists(tg.config.get('scm.repos.tarball.zip_binary', '/usr/bin/zip')), 'zip binary is missing')
    def test_tarball(self):
        tmpdir = tg.config['scm.repos.tarball.root']
        assert_equal(self.repo.tarball_path, os.path.join(tmpdir, 'svn/t/te/test/testsvn'))
        assert_equal(self.repo.tarball_url('1'), 'file:///svn/t/te/test/testsvn/test-src-1.zip')
        self.repo.tarball('1')
        assert os.path.isfile(os.path.join(tmpdir, "svn/t/te/test/testsvn/test-src-1.zip"))
        tarball_zip = ZipFile(os.path.join(tmpdir, 'svn/t/te/test/testsvn/test-src-1.zip'), 'r')
        assert_equal(tarball_zip.namelist(), ['test-src-1/', 'test-src-1/README'])

    def test_is_empty(self):
        assert not self.repo.is_empty()
        with TempDirectory() as d:
            repo2 = SM.Repository(
                name='test',
                fs_path=d.path,
                url_path = '/test/',
                tool = 'svn',
                status = 'creating')
            repo2.init()
            assert repo2.is_empty()
            repo2.refresh()
            ThreadLocalORMSession.flush_all()
            assert repo2.is_empty()

class TestSVNRev(unittest.TestCase):

    def setUp(self):
        setup_basic_test()
        self.setup_with_tools()

    @with_svn
    def setup_with_tools(self):
        setup_global_objects()
        h.set_context('test', 'src', neighborhood='Projects')
        repo_dir = pkg_resources.resource_filename(
            'forgesvn', 'tests/data/')
        self.repo = SM.Repository(
            name='testsvn',
            fs_path=repo_dir,
            url_path = '/test/',
            tool = 'svn',
            status = 'creating')
        self.repo.refresh()
        self.rev = self.repo.commit(1)
        ThreadLocalORMSession.flush_all()
        ThreadLocalORMSession.close_all()

    def test_url(self):
        assert self.rev.url().endswith('/1/')

    def test_primary(self):
        assert self.rev.primary() == self.rev

    def test_shorthand(self):
        assert self.rev.shorthand_id() == '[r1]'

    def test_diff(self):
        diffs = (self.rev.diffs.added
                 +self.rev.diffs.removed
                 +self.rev.diffs.changed
                 +self.rev.diffs.copied)
        for d in diffs:
            print d

    def _oid(self, rev_id):
        return '%s:%s' % (self.repo._id, rev_id)

    def test_commits(self):
        # path only
        commits = self.repo.commits()
        assert len(commits) == 5, 'Returned %s commits' % len(commits)
        assert self._oid(5) in commits, commits
        assert self._oid(1) in commits, commits
        commits = self.repo.commits('README')
        assert commits == [self._oid(3), self._oid(1)]
        assert self.repo.commits('does/not/exist') == []
        # with path and start rev
        commits = self.repo.commits('README', self._oid(1))
        assert commits == [self._oid(1)], commits
        # skip and limit
        commits = self.repo.commits(None, rev=None, skip=1, limit=2)
        assert commits == [self._oid(4), self._oid(3)]
        commits = self.repo.commits(None, self._oid(2), skip=1)
        assert commits == [self._oid(1)], commits
        commits = self.repo.commits('README', self._oid(1), skip=1)
        assert commits == []
        # path to dir
        commits = self.repo.commits('a/b/c/')
        assert commits == [self._oid(4), self._oid(2)]
        commits = self.repo.commits('a/b/c/', skip=1)
        assert commits == [self._oid(2)]
        commits = self.repo.commits('a/b/c/', limit=1)
        assert commits == [self._oid(4)]
        commits = self.repo.commits('not/exist/')
        assert commits == []

    def test_commits_count(self):
        commits = self.repo.commits_count()
        assert commits == 5, commits
        commits = self.repo.commits_count('a/b/c/')
        assert commits == 2, commits
        commits = self.repo.commits_count(None, self._oid(3))
        assert commits == 3, commits
        commits = self.repo.commits_count('README', self._oid(1))
        assert commits == 1, commits
        commits = self.repo.commits_count('not/exist/')
        assert commits == 0, commits

    def test_notification_email(self):
        setup_global_objects()
        h.set_context('test', 'src', neighborhood='Projects')
        repo_dir = pkg_resources.resource_filename(
            'forgesvn', 'tests/data/')
        self.repo = SM.Repository(
            name='testsvn',
            fs_path=repo_dir,
            url_path = '/test/',
            tool = 'svn',
            status = 'creating')
        self.repo.refresh()
        ThreadLocalORMSession.flush_all()
        commits = self.repo.commits()
        send_notifications(self.repo, [commits[4], ])
        ThreadLocalORMSession.flush_all()
        notifications = M.Notification.query.find().sort('pubdate')
        n = notifications.all()[3]
        assert_equal(n.subject, '[test:src] [r1] - rick446: Create readme')
        assert_equal(n.text, 'Create readme http://localhost//p/test/src/1/')


class _Test(unittest.TestCase):
    idgen = ( 'obj_%d' % i for i in count())

    def _make_tree(self, object_id, **kwargs):
        t, isnew = M.repo.Tree.upsert(object_id)
        repo = getattr(self, 'repo', None)
        t.repo = repo
        for k,v in kwargs.iteritems():
            if isinstance(v, basestring):
                obj = M.repo.Blob(
                    t, k, self.idgen.next())
                t.blob_ids.append(Object(
                        name=k, id=obj._id))
            else:
                obj = self._make_tree(self.idgen.next(), **v)
                t.tree_ids.append(Object(
                        name=k, id=obj._id))
        session(t).flush()
        return t

    def _make_commit(self, object_id, **tree_parts):
        ci, isnew = M.repo.Commit.upsert(object_id)
        if isnew:
            ci.committed.email=c.user.email_addresses[0]
            ci.authored.email=c.user.email_addresses[0]
            dt = datetime.utcnow()
            # BSON datetime resolution is to 1 millisecond, not 1 microsecond
            # like Python. Round this now so it'll match the value that's
            # pulled from MongoDB in the tests.
            ci.authored.date = dt.replace(microsecond=dt.microsecond/1000 * 1000)
            ci.message='summary\n\nddescription'
            ci.set_context(self.repo)
            ci.tree_id = 't_' + object_id
            ci.tree = self._make_tree(ci.tree_id, **tree_parts)
        return ci, isnew

    def _make_log(self, ci):
        session(ci).flush(ci)
        rb = M.repo_refresh.CommitRunBuilder([ci._id])
        rb.run()
        rb.cleanup()

    def setUp(self):
        setup_basic_test()
        setup_global_objects()
        ThreadLocalORMSession.flush_all()
        ThreadLocalORMSession.close_all()
        self.prefix = tg.config.get('scm.repos.root', '/')

class _TestWithRepo(_Test):
    def setUp(self):
        super(_TestWithRepo, self).setUp()
        h.set_context('test', neighborhood='Projects')
        c.project.install_app('svn', 'test1')
        h.set_context('test', 'test1', neighborhood='Projects')
        self.repo = M.Repository(name='test1', tool='svn')
        self.repo._impl = mock.Mock(spec=M.RepositoryImplementation())
        self.repo._impl.shorthand_for_commit = M.RepositoryImplementation.shorthand_for_commit
        self.repo._impl.url_for_commit = (
            lambda *a, **kw: M.RepositoryImplementation.url_for_commit(
                self.repo._impl, *a, **kw))
        self.repo._impl.log = lambda *a,**kw:(['foo'], [])
        self.repo._impl._repo = self.repo
        self.repo._impl.all_commit_ids = lambda *a,**kw: []
        self.repo._impl.commit().symbolic_ids = None
        ThreadLocalORMSession.flush_all()
        # ThreadLocalORMSession.close_all()

class _TestWithRepoAndCommit(_TestWithRepo):
    def setUp(self):
        super(_TestWithRepoAndCommit, self).setUp()
        self.ci, isnew = self._make_commit('foo')
        ThreadLocalORMSession.flush_all()
        # ThreadLocalORMSession.close_all()

class TestRepo(_TestWithRepo):

    def test_create(self):
        assert self.repo.fs_path == os.path.join(self.prefix, 'svn/p/test/')
        assert self.repo.url_path == '/p/test/'
        assert self.repo.full_fs_path == os.path.join(self.prefix, 'svn/p/test/test1')

    def test_passthrough(self):
        argless = ['init']
        for fn in argless:
            getattr(self.repo, fn)()
            getattr(self.repo._impl, fn).assert_called_with()
        unary = [ 'commit', 'open_blob' ]
        for fn in unary:
            getattr(self.repo, fn)('foo')
            getattr(self.repo._impl, fn).assert_called_with('foo')

    def test_shorthand_for_commit(self):
        self.assertEqual(
            self.repo.shorthand_for_commit('a'*40),
            '[aaaaaa]')

    def test_url_for_commit(self):
        self.assertEqual(
            self.repo.url_for_commit('a'*40),
            '/p/test/test1/ci/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/')

    @mock.patch('allura.model.repository.g.post_event')
    def test_init_as_clone(self, post_event):
        self.repo.init_as_clone('srcpath', 'srcname', 'srcurl')
        assert self.repo.upstream_repo.name == 'srcname'
        assert self.repo.upstream_repo.url == 'srcurl'
        assert self.repo._impl.clone_from.called_with('srcpath')
        post_event.assert_called_once_with('repo_cloned', 'srcurl', 'srcpath')

    @mock.patch.object(M.repo.CommitRunDoc.m, 'get')
    def test_log(self, crd):
        head = mock.Mock(name='commit_head', _id=3)
        commits = dict([(i, mock.Mock(name='commit_%s'%i, _id=i)) for i in range(3)])
        commits[3] = head
        head.query.get = lambda _id: commits[_id]
        self.repo._impl.commit = mock.Mock(return_value=head)
        crd.return_value = mock.Mock(commit_ids=[3, 2, 1, 0], commit_times=[4, 3, 2, 1], parent_commit_ids=[])
        log = self.repo.log()
        assert_equal([c._id for c in log], [3, 2, 1, 0])

    def test_count_revisions(self):
        ci = mock.Mock()
        self.repo.count_revisions = mock.Mock(return_value=42)
        self.repo._impl.commit = mock.Mock(return_value=ci)
        assert self.repo.count() == 42

    def test_latest(self):
        ci = mock.Mock()
        self.repo._impl.commit = mock.Mock(return_value=ci)
        assert self.repo.latest() is ci

    def test_index(self):
        i = self.repo.index()
        assert i['type_s'] == 'Repository', i
        assert i['name_s'] == 'test1', i

    def test_scm_host_url(self):
        assert (
            self.repo.clone_url('rw', 'nobody')
            == 'svn+ssh://nobody@localhost:8022/scm-repo/p/test/test1/'),\
            self.repo.clone_url('rw', 'nobody')
        assert (
            self.repo.clone_url('https', 'nobody')
            == 'https://nobody@localhost:8022/scm-repo/p/test/test1/'),\
            self.repo.clone_url('https', 'nobody')

    def test_merge_request(self):
        M.MergeRequest.upsert(app_config_id=c.app.config._id, status='open')
        M.MergeRequest.upsert(app_config_id=c.app.config._id, status='closed')
        session(M.MergeRequest).flush()
        session(M.MergeRequest).clear()
        assert self.repo.merge_requests_by_statuses('open').count() == 1
        assert self.repo.merge_requests_by_statuses('closed').count() == 1
        assert self.repo.merge_requests_by_statuses('open', 'closed').count() == 2

    def test_guess_type(self):
        assert self.repo.guess_type('foo.txt') == ('text/plain', None)
        assert self.repo.guess_type('foo.gbaer') == ('application/octet-stream', None)
        assert self.repo.guess_type('foo.html') == ('text/html', None)
        assert self.repo.guess_type('.gitignore') == ('text/plain', None)

    def test_refresh(self):
        committer_name = 'Test Committer'
        committer_email = 'test@example.com'
        ci = mock.Mock()
        ci.authored.name = committer_name
        ci.committed.name = committer_name
        ci.committed.email = committer_email
        ci.author_url = '/u/test-committer/'
        self.repo.count_revisions=mock.Mock(return_value=100)
        self.repo._impl.commit = mock.Mock(return_value=ci)
        self.repo._impl.new_commits = mock.Mock(return_value=['foo%d' % i for i in range(100) ])
        self.repo._impl.all_commit_ids = mock.Mock(return_value=['foo%d' % i for i in range(100) ])
        self.repo.symbolics_for_commit = mock.Mock(return_value=[['master', 'branch'], []])
        def refresh_commit_info(oid, seen, lazy=False):
            M.repo.CommitDoc(dict(
                    authored=dict(
                        name=committer_name,
                        email=committer_email),
                    _id=oid)).m.insert()
        self.repo._impl.refresh_commit_info = refresh_commit_info
        _id = lambda oid: getattr(oid, '_id', str(oid))
        self.repo.shorthand_for_commit = lambda oid: '[' + _id(oid) + ']'
        self.repo.url_for_commit = lambda oid: 'ci/' + _id(oid) + '/'
        self.repo.refresh()
        ThreadLocalORMSession.flush_all()
        notifications = M.Notification.query.find().all()
        for n in notifications:
            if '100 new commits' in n.subject:
                assert "master,branch:  by %s http://localhost/ci/foo99" % committer_name in n.text
                break
        else:
            assert False, 'Did not find notification'
        assert M.Feed.query.find(dict(
            author_name=committer_name)).count() == 100

    def test_refresh_private(self):
        ci = mock.Mock()
        self.repo.count_revisions=mock.Mock(return_value=100)
        self.repo._impl.commit = mock.Mock(return_value=ci)
        self.repo._impl.new_commits = mock.Mock(return_value=['foo%d' % i for i in range(100) ])

        # make unreadable by *anonymous, so additional notification logic executes
        self.repo.acl = []
        c.project.acl = []

        self.repo.refresh()

    def test_push_upstream_context(self):
        self.repo.init_as_clone('srcpath', '/p/test/svn/', '/p/test/svn/')
        old_app_instance = M.Project.app_instance
        try:
            M.Project.app_instance = mock.Mock(return_value=ming.base.Object(
                    config=ming.base.Object(_id=None)))
            with self.repo.push_upstream_context():
                assert c.project.shortname == 'test'
        finally:
            M.Project.app_instance = old_app_instance

    def test_pending_upstream_merges(self):
        self.repo.init_as_clone('srcpath', '/p/test/svn/', '/p/test/svn/')
        old_app_instance = M.Project.app_instance
        try:
            M.Project.app_instance = mock.Mock(return_value=ming.base.Object(
                    config=ming.base.Object(_id=None)))
            self.repo.pending_upstream_merges()
        finally:
            M.Project.app_instance = old_app_instance

class TestMergeRequest(_TestWithRepoAndCommit):

    def setUp(self):
        super(TestMergeRequest, self).setUp()
        c.project.install_app('svn', 'test2')
        h.set_context('test', 'test2', neighborhood='Projects')
        self.repo2 = M.Repository(name='test2', tool='svn')
        self.repo2._impl = mock.Mock(spec=M.RepositoryImplementation())
        self.repo2._impl.log = lambda *a,**kw:(['foo'], [])
        self.repo2._impl.all_commit_ids = lambda *a,**kw: []
        self.repo2._impl._repo = self.repo2
        self.repo2.init_as_clone('/p/test/', 'test1', '/p/test/test1/')
        ThreadLocalORMSession.flush_all()
        ThreadLocalORMSession.close_all()

    def test_upsert(self):
        h.set_context('test', 'test1', neighborhood='Projects')
        mr = M.MergeRequest.upsert(
            downstream=ming.base.Object(
                project_id=c.project._id,
                mount_point='test2',
                commit_id='foo'),
            target_branch='foobranch',
            summary='summary',
            description='description')
        u = M.User.by_username('test-admin')
        assert mr.creator == u
        assert mr.creator_name == u.get_pref('display_name')
        assert mr.creator_url == u.url()
        assert mr.downstream_url == '/p/test/test2/'
        assert mr.downstream_repo_url == 'http://svn.localhost/p/test/test2/'
        assert mr.commits == [ self._make_commit('foo')[0] ]

class TestRepoObject(_TestWithRepoAndCommit):

    def test_upsert(self):
        obj0, isnew0 = M.repo.Tree.upsert('foo1')
        obj1, isnew1 = M.repo.Tree.upsert('foo1')
        assert obj0 is obj1
        assert isnew0 and not isnew1

    def test_artifact_methods(self):
        assert self.ci.index_id() == 'allura/model/repo/Commit#foo', self.ci.index_id()
        assert self.ci.primary() is self.ci, self.ci.primary()


class TestCommit(_TestWithRepo):

    def setUp(self):
        super(TestCommit, self).setUp()
        self.ci, isnew = self._make_commit(
            'foo',
            a=dict(
                a=dict(
                    a='',
                    b='',),
                b=''))
        self.tree = self.ci.tree
        impl = M.RepositoryImplementation()
        impl._repo = self.repo
        self.repo._impl.shorthand_for_commit = impl.shorthand_for_commit
        self.repo._impl.url_for_commit = impl.url_for_commit

    def test_upsert(self):
        obj0, isnew0 = M.repo.Commit.upsert('foo')
        obj1, isnew1 = M.repo.Commit.upsert('foo')
        assert obj0 is obj1
        assert not isnew1
        u = M.User.by_username('test-admin')
        assert self.ci.author_url == u.url()
        assert self.ci.committer_url == u.url()
        assert self.ci.tree is self.tree
        assert self.ci.summary == 'summary'
        assert self.ci.shorthand_id() == '[foo]'
        assert self.ci.url() == '/p/test/test1/ci/foo/'

    def test_get_path(self):
        b = self.ci.get_path('a/a/a')
        assert isinstance(b, M.repo.Blob)
        x = self.ci.get_path('a/a')
        assert isinstance(x, M.repo.Tree)

    def test_count_revisions(self):
        rb = M.repo_refresh.CommitRunBuilder(['foo'])
        rb.run()
        rb.cleanup()
        assert self.repo.count_revisions(self.ci) == 1

    def _unique_blobs(self):
        def counter():
            counter.i += 1
            return counter.i
        counter.i = 0
        blobs = defaultdict(counter)
        from cStringIO import StringIO
        return lambda blob: StringIO(str(blobs[blob.path()]))

    def test_compute_diffs(self):
        self.repo._impl.commit = mock.Mock(return_value=self.ci)
        self.repo._impl.open_blob = self._unique_blobs()
        M.repo_refresh.refresh_commit_trees(self.ci, {})
        M.repo_refresh.compute_diffs(self.repo._id, {}, self.ci)
        # self.ci.compute_diffs()
        assert_equal(self.ci.diffs.added, [ 'a', 'a/a', 'a/a/a', 'a/a/b', 'a/b' ])
        assert (self.ci.diffs.copied
                == self.ci.diffs.changed
                == self.ci.diffs.removed
                == [])
        ci, isnew = self._make_commit('bar')
        ci.parent_ids = [ 'foo' ]
        self._make_log(ci)
        M.repo_refresh.refresh_commit_trees(ci, {})
        M.repo_refresh.compute_diffs(self.repo._id, {}, ci)
        assert_equal(ci.diffs.removed, [ 'a', 'a/a', 'a/a/a', 'a/a/b', 'a/b' ])
        assert (ci.diffs.copied
                == ci.diffs.changed
                == ci.diffs.added
                == [])
        ci, isnew = self._make_commit(
            'baz',
            b=dict(
                a=dict(
                    a='',
                    b='',),
                b=''))
        ci.parent_ids = [ 'foo' ]
        self._make_log(ci)
        M.repo_refresh.refresh_commit_trees(ci, {})
        M.repo_refresh.compute_diffs(self.repo._id, {}, ci)
        assert_equal(ci.diffs.added, [ 'b', 'b/a', 'b/a/a', 'b/a/b', 'b/b' ])
        assert_equal(ci.diffs.removed, [ 'a', 'a/a', 'a/a/a', 'a/a/b', 'a/b' ])
        assert (ci.diffs.copied
                == ci.diffs.changed
                == [])

    def test_diffs_file_renames(self):
        def open_blob(blob):
            blobs = {
                u'a': u'Leia',
                u'/b/a/a': u'Darth Vader',
                u'/b/a/b': u'Luke Skywalker',
                u'/b/b': u'Death Star will destroy you',
                u'/b/c': u'Luke Skywalker',  # moved from /b/a/b
                u'/b/a/z': u'Death Star will destroy you\nALL',  # moved from /b/b and modified
            }
            from cStringIO import StringIO
            return StringIO(blobs.get(blob.path(), ''))
        self.repo._impl.open_blob = open_blob

        self.repo._impl.commit = mock.Mock(return_value=self.ci)
        M.repo_refresh.refresh_commit_trees(self.ci, {})
        M.repo_refresh.compute_diffs(self.repo._id, {}, self.ci)
        assert_equal(self.ci.diffs.added, ['a', 'a/a', 'a/a/a', 'a/a/b', 'a/b'])
        assert (self.ci.diffs.copied
                == self.ci.diffs.changed
                == self.ci.diffs.removed
                == [])

        ci, isnew = self._make_commit(
            'bar',
            b=dict(
                a=dict(
                    a='',
                    b='',),
                b=''))
        ci.parent_ids = ['foo']
        self._make_log(ci)
        M.repo_refresh.refresh_commit_trees(ci, {})
        M.repo_refresh.compute_diffs(self.repo._id, {}, ci)
        assert_equal(ci.diffs.added, ['b', 'b/a', 'b/a/a', 'b/a/b', 'b/b'])
        assert_equal(ci.diffs.removed, ['a', 'a/a', 'a/a/a', 'a/a/b', 'a/b'])
        assert (ci.diffs.copied
                == ci.diffs.changed
                == [])

        ci, isnew = self._make_commit(
            'baz',
            b=dict(
                a=dict(
                    z=''),
                c=''))
        ci.parent_ids = ['bar']
        self._make_log(ci)
        M.repo_refresh.refresh_commit_trees(ci, {})
        M.repo_refresh.compute_diffs(self.repo._id, {}, ci)
        assert_equal(ci.diffs.added, [])
        assert_equal(ci.diffs.changed, [])
        assert_equal(ci.diffs.removed, ['b/a/a'])
        # see mock for open_blob
        assert_equal(len(ci.diffs.copied), 2)
        assert_equal(ci.diffs.copied[0]['old'], 'b/a/b')
        assert_equal(ci.diffs.copied[0]['new'], 'b/c')
        assert_equal(ci.diffs.copied[0]['ratio'], 1)
        assert_equal(ci.diffs.copied[0]['diff'], '')
        assert_equal(ci.diffs.copied[1]['old'], 'b/b')
        assert_equal(ci.diffs.copied[1]['new'], 'b/a/z')
        assert ci.diffs.copied[1]['ratio'] < 1, ci.diffs.copied[1]['ratio']
        assert '+++' in ci.diffs.copied[1]['diff'], ci.diffs.copied[1]['diff']

    def test_context(self):
        self.ci.context()
