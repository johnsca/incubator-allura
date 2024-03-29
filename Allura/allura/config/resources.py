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
import logging

import pkg_resources

log = logging.getLogger(__name__)

def register_ew_resources(manager):
    manager.register_directory(
        'js', pkg_resources.resource_filename('allura', 'lib/widgets/resources/js'))
    manager.register_directory(
        'css', pkg_resources.resource_filename('allura', 'lib/widgets/resources/css'))
    manager.register_directory(
        'allura', pkg_resources.resource_filename('allura', 'public/nf'))
    for ep in pkg_resources.iter_entry_points('allura'):
        try:
            manager.register_directory(
                'tool/%s' % ep.name.lower(),
                pkg_resources.resource_filename(
                    ep.module_name,
                    os.path.join('nf', ep.name.lower())))
        except ImportError:
            log.warning('Cannot import entry point %s', ep)
            raise
    for ep in pkg_resources.iter_entry_points('allura.theme'):
        try:
            theme = ep.load()
            theme.register_ew_resources(manager, ep.name)
        except ImportError:
            log.warning('Cannot import entry point %s', ep)
            raise
