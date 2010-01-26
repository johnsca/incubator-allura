from pylons import g

from ming.orm.ormsession import ThreadLocalORMSession

from pyforge.tests import TestController
from pyforge import model as M

class TestAdmin(TestController):

    def test_admin_controller(self):
        self.app.get('/admin/')
        self.app.post('/admin/update', params=dict(
                name='Test Project',
                shortname='test',
                short_description='A Test Project',
                description='A long description'))
        # Add/Remove a subproject
        self.app.post('/admin/update_mounts', params={
                'new.install':'install',
                'new.ep_name':'',
                'new.mount_point':'test_subproject'})
        self.app.post('/admin/update_mounts', params={
                'subproject-0.delete':'on',
                'subproject-0.id':'projects/test/test_subproject/',
                'new.ep_name':'',
                })
        # Add/Remove a plugin
        self.app.post('/admin/update_mounts', params={
                'new.install':'install',
                'new.ep_name':'hello_forge',
                'new.mount_point':'test_plugin'})
        self.app.post('/admin/update_mounts', params={
                'plugin-0.delete':'on',
                'plugin-0.mount_point':'test_plugin',
                'new.ep_name':'',
                })
        # Update ACL
        role = M.User.anonymous().project_role()
        self.app.post('/admin/update_acl', params={
                'permission':'plugin',
                'new.add':'on',
                'new.id':str(role._id)})
        self.app.post('/admin/update_acl', params={
                'new.id':'',
                'permission':'plugin',
                'role-0.delete':'on',
                'role-0.id':str(role._id)})
        self.app.post('/admin/update_acl', params={
                'permission':'plugin',
                'new.add':'on',
                'new.id':'',
                'new.username':'test_user'})
        self.app.post('/admin/update_acl', params={
                'permission':'plugin',
                'new.add':'on',
                'new.id':'',
                'new.username':'no_such_user'})
        # Update project roles
        self.app.post('/admin/update_roles', params={
                'new.add':'on',
                'new.name':'test_role'})
        role1 = M.ProjectRole.query.find(dict(name='test_role')).one()
        self.app.post('/admin/update_roles', params={
                'role-0.id':str(role1._id),
                'role-0.new.add':'on',
                'role-0.new.id':str(role._id),
                'new.name':''})
        self.app.post('/admin/update_roles', params={
                'role-0.id':str(role1._id),
                'role-0.new.id':'',
                'role-0.subroles-0.delete':'on',
                'role-0.subroles-0.id':str(role._id),
                'new.name':''})
        self.app.post('/admin/update_roles', params={
                'role-0.id':str(role1._id),
                'role-0.new.id':'',
                'role-0.delete':'on',
                'new.name':''})
