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

from ming.base import Object
from ming import schema as S

EVERYONE, ALL_PERMISSIONS = None, '*'

class ACE(S.Object):
    '''ACE - access control entry'''
    ALLOW, DENY = 'ALLOW', 'DENY'
    def __init__(self, permissions, **kwargs):
        if permissions is None:
            permission=S.String()
        else:
            permission=S.OneOf('*', *permissions)
        super(ACE, self).__init__(
            fields=dict(
                access=S.OneOf(self.ALLOW, self.DENY),
                role_id=S.ObjectId(),
                permission=permission),
            **kwargs)

    @classmethod
    def allow(cls, role_id, permission):
        return Object(
            access=cls.ALLOW,
            role_id=role_id,
            permission=permission)

    @classmethod
    def deny(cls, role_id, permission):
        return Object(
            access=cls.DENY,
            role_id=role_id,
            permission=permission)

    @classmethod
    def match(cls, ace, role_id, permission):
        return (
            ace.role_id in (role_id, EVERYONE)
            and ace.permission in (permission, ALL_PERMISSIONS))

class ACL(S.Array):

    def __init__(self, permissions=None, **kwargs):
        super(ACL, self).__init__(
            field_type=ACE(permissions), **kwargs)

DENY_ALL = ACE.deny(EVERYONE, ALL_PERMISSIONS)
