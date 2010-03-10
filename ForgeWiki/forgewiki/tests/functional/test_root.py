import os
import Image, StringIO
import pyforge

from nose.tools import assert_true

from forgewiki.tests import TestController
from forgewiki import model

# These are needed for faking reactor actions
import mock
from pyforge.lib import helpers as h
from pyforge.command import reactor
from pyforge.ext.search import search_main
from ming.orm.ormsession import ThreadLocalORMSession

#---------x---------x---------x---------x---------x---------x---------x
# RootController methods exposed:
#     index, new_page, search
# PageController methods exposed:
#     index, edit, history, diff, raw, revert, update
# CommentController methods exposed:
#     reply, delete

class TestRootController(TestController):
    def test_root_index(self):
        response = self.app.get('/Wiki/TEST/index')
        assert 'TEST' in response

    def test_root_markdown_syntax(self):
        response = self.app.get('/Wiki/markdown_syntax/')
        assert 'Markdown Syntax' in response

    def test_root_wiki_help(self):
        response = self.app.get('/Wiki/wiki_help/')
        assert 'Wiki Help' in response

    def test_root_browse_tags(self):
        response = self.app.get('/Wiki/browse_tags/')
        assert 'Browse Tags' in response

    def test_root_browse_pages(self):
        response = self.app.get('/Wiki/browse_pages/')
        assert 'Browse Pages' in response

    def test_root_new_page(self):
        response = self.app.get('/Wiki/new_page?title=TEST')
        assert 'TEST' in response

    def test_root_new_search(self):
        self.app.get('/Wiki/TEST/index')
        response = self.app.get('/Wiki/search?q=TEST')
        assert 'ForgeWiki Search' in response

    def test_page_index(self):
        response = self.app.get('/Wiki/TEST/index/')
        assert 'TEST' in response

    def test_page_edit(self):
        self.app.get('/Wiki/TEST/index/')
        response = self.app.post('/Wiki/TEST/edit')
        assert 'TEST' in response

    def test_page_history(self):
        response = self.app.get('/Wiki/TEST/history')
        assert 'TEST' in response

    def test_page_diff(self):
        self.app.get('/Wiki/TEST/index/')
        self.app.get('/Wiki/TEST/revert?version=1')
        response = self.app.get('/Wiki/TEST/diff?v1=0&v2=0')
        assert 'TEST' in response

    def test_page_raw(self):
        self.app.get('/Wiki/TEST/index/')
        response = self.app.get('/Wiki/TEST/raw')
        assert 'TEST' in response

    def test_page_revert_no_text(self):
        self.app.get('/Wiki/TEST/index/')
        response = self.app.get('/Wiki/TEST/revert?version=1')
        assert 'TEST' in response

    def test_page_revert_with_text(self):
        self.app.get('/Wiki/TEST/index/')
        self.app.get('/Wiki/TEST/update?text=sometext&tags=&tags_old=&viewable_by=all')
        response = self.app.get('/Wiki/TEST/revert?version=1')
        assert 'TEST' in response

    def test_page_update(self):
        self.app.get('/Wiki/TEST/index/')
        response = self.app.get('/Wiki/TEST/update?text=sometext&tags=&tags_old=&viewable_by=all')
        assert 'TEST' in response

    def test_page_tag_untag(self):
        self.app.get('/Wiki/TEST/index/')
        response = self.app.get('/Wiki/TEST/update?text=sometext&tags=red,blue&tags_old=red,blue&viewable_by=all')
        assert 'TEST' in response
        response = self.app.get('/Wiki/TEST/update?text=sometext&tags=red&tags_old=red&viewable_by=all')
        assert 'TEST' in response

    def test_new_attachment(self):
        self.app.get('/Wiki/TEST/index')
        content = file(__file__).read()
        response = self.app.post('/Wiki/TEST/attach', upload_files=[('file_info', 'test_root.py', content)]).follow()
        assert 'test_root.py' in response

    def test_new_text_attachment_content(self):
        self.app.get('/Wiki/TEST/index')
        file_name = 'test_root.py'
        file_data = file(__file__).read()
        upload = ('file_info', file_name, file_data)
        page_editor = self.app.post('/Wiki/TEST/attach', upload_files=[upload]).follow()
        download = page_editor.click(description=file_name)
        assert_true(download.body == file_data)

    def test_new_image_attachment_content(self):
        self.app.get('/Wiki/TEST/index')
        file_name = 'adobe_header.png'
        file_path = os.path.join(pyforge.__path__[0],'public','images',file_name)
        file_data = file(file_path).read()
        upload = ('file_info', file_name, file_data)
        self.app.post('/Wiki/TEST/attach', upload_files=[upload])
        h.set_context('test', 'wiki')
        page = model.Page.query.find(dict(title='TEST')).first()
        filename = page.attachments.first().filename

        uploaded = Image.open(file_path)
        r = self.app.get('/Wiki/TEST/attachment/'+filename)
        downloaded = Image.open(StringIO.StringIO(r.body))
        assert uploaded.size == downloaded.size
        r = self.app.get('/Wiki/TEST/attachment/'+filename+'/thumb')

        thumbnail = Image.open(StringIO.StringIO(r.body))
        assert thumbnail.size == (101,101)

    def test_sidebar_static_page(self):
        response = self.app.get('/Wiki/TEST/')
        assert 'Edit this page' not in response
        assert 'Related Pages' not in response

    def test_sidebar_dynamic_page(self):
        response = self.app.get('/Wiki/TEST/').follow()
        assert 'Edit this page' in response
        assert 'Related Pages' not in response
        self.app.get('/Wiki/aaa/')
        self.app.get('/Wiki/bbb/')
        
        # Fake out updating the pages since reactor doesn't work with tests
        app = search_main.SearchApp
        cmd = reactor.ReactorCommand('reactor')
        cmd.args = [ os.environ.get('SANDBOX') and 'sandbox-test.ini' or 'test.ini' ]
        cmd.options = mock.Mock()
        cmd.options.dry_run = True
        cmd.options.proc = 1
        configs = cmd.command()
        add_artifacts = cmd.route_audit('search', app.add_artifacts)
        del_artifacts = cmd.route_audit('search', app.del_artifacts)
        msg = mock.Mock()
        msg.ack = lambda:None
        msg.delivery_info = dict(routing_key='search.add_artifacts')
        h.set_context('test', 'wiki')
        a = model.Page.query.find(dict(title='aaa')).first()
        a.text = '\n[TEST]\n'
        msg.data = dict(project_id=a.project_id,
                        mount_point=a.app_config.options.mount_point,
                        artifacts=[a.dump_ref()])
        add_artifacts(msg.data, msg)
        b = model.Page.query.find(dict(title='TEST')).first()
        b.text = '\n[bbb]\n'
        msg.data = dict(project_id=b.project_id,
                        mount_point=b.app_config.options.mount_point,
                        artifacts=[b.dump_ref()])
        add_artifacts(msg.data, msg)
        ThreadLocalORMSession.flush_all()
        ThreadLocalORMSession.close_all()
        
        response = self.app.get('/Wiki/TEST/')
        assert 'Related Pages' in response
        assert 'aaa' in response
        assert 'bbb' in response

    def test_page_permissions(self):
        response = self.app.get('/Wiki/TEST/').follow()
        assert 'Viewable by' in response
        self.app.get('/Wiki/TEST/update?text=sometext&tags=&tags_old=&viewable_by=')
        self.app.get('/Wiki/TEST/', status=403)
