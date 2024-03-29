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

import sys
import urllib
import urllib2
import urlparse
import hmac
import hashlib
import json
import logging
from datetime import datetime


log = logging.getLogger('import')

class AlluraImportApiClient(object):

    def __init__(self, base_url, api_key, secret_key, verbose=False, retry=True):
        self.base_url = base_url
        self.api_key = api_key
        self.secret_key = secret_key
        self.verbose = verbose
        self.retry = retry

    def sign(self, path, params):
        params.append(('api_key', self.api_key))
        params.append(('api_timestamp', datetime.utcnow().isoformat()))
        message = path + '?' + urllib.urlencode(sorted(params))
        digest = hmac.new(self.secret_key, message, hashlib.sha256).hexdigest()
        params.append(('api_signature', digest))
        return params

    def call(self, url, **params):
        url = urlparse.urljoin(self.base_url, url)
        if self.verbose:
            print "Using URL '%s'" % (url)
        
        params = self.sign(urlparse.urlparse(url).path, params.items())

        while True:
            try:
                result = urllib2.urlopen(url, urllib.urlencode(params))
                resp = result.read()
                return json.loads(resp)
            except urllib2.HTTPError, e:
                if self.verbose:
                    error_content = e.read()
                    e.msg += '. Error response:\n' + error_content
                raise e
            except (urllib2.URLError, IOError):
                if self.retry:
                    log.exception('Error making API request, will retry')
                    continue
                raise
