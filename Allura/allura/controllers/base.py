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

from tg import expose
from webob import exc
from tg.controllers.dispatcher import ObjectDispatcher


class BaseController(object):
    @expose()
    def _lookup(self, name, *remainder):
        """Provide explicit default lookup to avoid dispatching backtracking
        and possible loops."""
        raise exc.HTTPNotFound, name


class DispatchIndex(object):
    """Rewrite default url dispatching for controller.

    Catch url that ends with `index.*` and pass it to the `_lookup()`
    controller method, instead of `index()` as by default.
    Assumes that controller has `_lookup()` method.

    Use default dispatching for other urls.

    Use this class as a mixin to controller that needs such behaviour.
    (see allura.controllers.repository.TreeBrowser for an example)
    """
    dispatcher = ObjectDispatcher()

    def _dispatch(self, state, remainder):
        dispatcher = self.dispatcher
        if remainder and remainder[0] == 'index':
            controller, new_remainder = self._lookup(*remainder)
            state.add_controller(controller.__class__.__name__, controller)
            dispatcher = getattr(controller, '_dispatch', dispatcher._dispatch)
            return dispatcher(state, new_remainder)
        return dispatcher._dispatch(state, remainder)
