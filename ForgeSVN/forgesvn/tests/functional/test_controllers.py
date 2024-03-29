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

import json
import shutil
import os

import tg
import pkg_resources
from pylons import tmpl_context as c
from ming.orm import ThreadLocalORMSession
from nose.tools import assert_equal
from IPython.testing.decorators import onlyif

from allura import model as M
from allura.lib import helpers as h
from alluratest.controller import TestController
from forgesvn.tests import with_svn
from allura.tests.decorators import with_tool

class SVNTestController(TestController):
    def setUp(self):
        TestController.setUp(self)
        self.setup_with_tools()

    @with_svn
    @with_tool('test', 'SVN', 'svn-tags', 'SVN with tags')
    def setup_with_tools(self):
        h.set_context('test', 'src', neighborhood='Projects')
        repo_dir = pkg_resources.resource_filename(
            'forgesvn', 'tests/data/')
        c.app.repo.fs_path = repo_dir
        c.app.repo.status = 'ready'
        c.app.repo.name = 'testsvn'
        ThreadLocalORMSession.flush_all()
        ThreadLocalORMSession.close_all()
        h.set_context('test', 'src', neighborhood='Projects')
        c.app.repo.refresh()
        ThreadLocalORMSession.flush_all()
        ThreadLocalORMSession.close_all()
        h.set_context('test', 'svn-tags', neighborhood='Projects')
        c.app.repo.fs_path = repo_dir
        c.app.repo.status = 'ready'
        c.app.repo.name = 'testsvn-trunk-tags-branches'
        c.app.repo.refresh()
        ThreadLocalORMSession.flush_all()
        ThreadLocalORMSession.close_all()
        h.set_context('test', 'src', neighborhood='Projects')


class TestRootController(SVNTestController):
    def test_status(self):
        resp = self.app.get('/src/status')
        d = json.loads(resp.body)
        assert d == dict(status='ready')

    def test_status_html(self):
        resp = self.app.get('/src/').follow()
        # repo status not displayed if 'ready'
        assert None == resp.html.find('div', dict(id='repo_status'))
        h.set_context('test', 'src', neighborhood='Projects')
        c.app.repo.status = 'analyzing'
        ThreadLocalORMSession.flush_all()
        ThreadLocalORMSession.close_all()
        # repo status displayed if not 'ready'
        resp = self.app.get('/src/').follow()
        div = resp.html.find('div', dict(id='repo_status'))
        assert div.span.text == 'analyzing'

    def test_index(self):
        resp = self.app.get('/src/').follow()
        assert 'svn checkout' in resp
        assert '[r5]' in resp, resp.showbrowser()

    def test_index_empty(self):
        self.app.get('/svn/')

    def test_commit_browser(self):
        resp = self.app.get('/src/commit_browser')

    def test_commit_browser_data(self):
        resp = self.app.get('/src/commit_browser_data')
        data = json.loads(resp.body);
        assert data['max_row'] == 4
        assert data['next_column'] == 1
        for val in data['built_tree'].values():
            if val['url'] == '/p/test/src/1/':
                assert val['column'] == 0
                assert val['row'] == 4
                assert val['message'] == 'Create readme'

    def test_feed(self):
        for ext in ['', '.rss']:
            r = self.app.get('/src/feed%s' % ext)
            channel = r.xml.find('channel')
            title = channel.find('title').text
            assert_equal(title, 'test SVN changes')
            description = channel.find('description').text
            assert_equal(description, 'Recent changes to SVN repository in test project')
            link = channel.find('link').text
            assert_equal(link, 'http://localhost:80/p/test/src/')
            commit = channel.find('item')
            assert_equal(commit.find('title').text, 'Create readme')
            link = 'http://localhost:80/p/test/src/1/'
            assert_equal(commit.find('link').text, link)
            assert_equal(commit.find('guid').text, link)
        # .atom has slightly different structure
        prefix = '{http://www.w3.org/2005/Atom}'
        r = self.app.get('/src/feed.atom')
        title = r.xml.find(prefix + 'title').text
        assert_equal(title, 'test SVN changes')
        link = r.xml.find(prefix + 'link').attrib['href']
        assert_equal(link, 'http://localhost:80/p/test/src/')
        commit = r.xml.find(prefix + 'entry')
        assert_equal(commit.find(prefix + 'title').text, 'Create readme')
        link = 'http://localhost:80/p/test/src/1/'
        assert_equal(commit.find(prefix + 'link').attrib['href'], link)

    def test_commit(self):
        resp = self.app.get('/src/3/tree/')
        assert len(resp.html.findAll('tr')) == 3, resp.showbrowser()

    def test_tree(self):
        resp = self.app.get('/src/1/tree/')
        assert len(resp.html.findAll('tr')) == 2, resp.showbrowser()
        resp = self.app.get('/src/3/tree/a/')
        assert len(resp.html.findAll('tr')) == 2, resp.showbrowser()

    def test_file(self):
        resp = self.app.get('/src/1/tree/README')
        assert 'README' in resp.html.find('h2', {'class':'dark title'}).contents[2]
        content = str(resp.html.find('div', {'class':'clip grid-19 codebrowser'}))
        assert 'This is readme' in content, content
        assert '<span id="l1" class="code_block">' in resp
        assert 'var hash = window.location.hash.substring(1);' in resp

    def test_invalid_file(self):
        resp = self.app.get('/src/1/tree/READMEz', status=404)

    def test_diff(self):
        resp = self.app.get('/src/3/tree/README?diff=2')
        assert 'This is readme' in resp, resp.showbrowser()
        assert '+++' in resp, resp.showbrowser()

    def test_checkout_svn(self):
        self.app.post('/p/test/admin/src/set_checkout_url',
                      {"checkout_url": "badurl"})
        r = self.app.get('/p/test/admin/src/checkout_url')
        assert 'value="badurl"' not in r
        self.app.post('/p/test/admin/src/set_checkout_url',
                      {"checkout_url": ""})
        r = self.app.get('/p/test/admin/src/checkout_url')
        assert 'value="trunk"' not in r
        self.app.post('/p/test/admin/src/set_checkout_url',
                      {"checkout_url": "a"})
        r = self.app.get('/p/test/admin/src/checkout_url')
        assert 'value="a"' in r

    def test_log(self):
        r = self.app.get('/src/1/log/')
        assert 'Create readme' in r
        r = self.app.get('/src/2/log/?path=')
        assert "Create readme" in r
        assert "Add path" in r
        r = self.app.get('/src/2/log/?path=README')
        assert "Modify readme" not in r
        assert "Create readme" in r
        r = self.app.get('/src/2/log/?path=/a/b/c/')
        assert 'Add path' in r
        assert 'Remove hello.txt' not in r
        r = self.app.get('/src/5/log/?path=a/b/c/')
        assert 'Add path' in r
        assert 'Remove hello.txt' in r
        r = self.app.get('/src/2/log/?path=does/not/exist/')
        assert 'No (more) commits' in r

    @onlyif(os.path.exists(tg.config.get('scm.repos.tarball.zip_binary', '/usr/bin/zip')), 'zip binary is missing')
    def test_tarball(self):
        r = self.app.get('/src/3/tree/')
        assert 'Download Snapshot' in r
        r = self.app.get('/src/3/tarball')
        assert 'Generating snapshot...' in r
        M.MonQTask.run_ready()
        ThreadLocalORMSession.flush_all()
        r = self.app.get('/src/3/tarball_status')
        assert '{"status": "ready"}' in r

    @onlyif(os.path.exists(tg.config.get('scm.repos.tarball.zip_binary', '/usr/bin/zip')), 'zip binary is missing')
    def test_tarball_tags_aware(self):
        h.set_context('test', 'svn-tags', neighborhood='Projects')
        shutil.rmtree(c.app.repo.tarball_path, ignore_errors=True)
        r = self.app.get('/p/test/svn-tags/19/tree/')
        link = r.html.find('h2', attrs={'class': 'dark title'})
        link = link.find('small').findAll('a')[0]
        assert_equal(link.text, 'Download Snapshot')
        assert_equal(link.get('href'), '/p/test/svn-tags/19/tarball')

        r = self.app.get('/p/test/svn-tags/19/tree/tags/tag-1.0/')
        link = r.html.find('h2', attrs={'class': 'dark title'})
        link = link.find('small').findAll('a')[0]
        assert_equal(link.text, 'Download Snapshot')
        assert_equal(link.get('href'), '/p/test/svn-tags/19/tarball?path=/tags/tag-1.0')

        r = self.app.get('/p/test/svn-tags/19/tarball_status?path=/tags/tag-1.0')
        assert_equal(r.json['status'], None)
        r = self.app.get(link.get('href'))
        assert 'Generating snapshot...' in r
        M.MonQTask.run_ready()
        r = self.app.get('/p/test/svn-tags/19/tarball_status?path=/tags/tag-1.0')
        assert_equal(r.json['status'], 'ready')

        r = self.app.get('/p/test/svn-tags/19/tarball_status?path=/trunk')
        assert_equal(r.json['status'], None)
        r = self.app.get('/p/test/svn-tags/19/tarball?path=/trunk/')
        assert 'Generating snapshot...' in r
        M.MonQTask.run_ready()
        r = self.app.get('/p/test/svn-tags/19/tarball_status?path=/trunk')
        assert_equal(r.json['status'], 'ready')

        r = self.app.get('/p/test/svn-tags/19/tarball_status?path=/branches/aaa/')
        assert_equal(r.json['status'], None)

        # All of the following also should be ready because...
        # ...this is essentially the same as trunk snapshot
        r = self.app.get('/p/test/svn-tags/19/tarball_status?path=/trunk/some/path/')
        assert_equal(r.json['status'], 'ready')
        r = self.app.get('/p/test/svn-tags/19/tarball_status')
        assert_equal(r.json['status'], 'ready')
        # ...the same as trunk, 'cause concrete tag isn't specified
        r = self.app.get('/p/test/svn-tags/19/tarball_status?path=/tags/')
        assert_equal(r.json['status'], 'ready')
        # ...the same as trunk, 'cause concrete branch isn't specified
        r = self.app.get('/p/test/svn-tags/19/tarball_status?path=/branches/')
        assert_equal(r.json['status'], 'ready')
        # ...this is essentially the same as tag snapshot
        r = self.app.get('/p/test/svn-tags/19/tarball_status?path=/tags/tag-1.0/dir')
        assert_equal(r.json['status'], 'ready')


class TestImportController(SVNTestController):
    def test_index(self):
        self.app.get('/p/test/admin/src/importer').follow(status=200)
