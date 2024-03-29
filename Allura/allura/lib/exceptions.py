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

class ForgeError(Exception): pass
class ProjectConflict(ForgeError): pass
class ProjectOverlimitError(ForgeError): pass
class ProjectRatelimitError(ForgeError): pass
class ToolError(ForgeError): pass
class NoSuchProjectError(ForgeError): pass
class NoSuchNeighborhoodError(ForgeError): pass
class NoSuchGlobalsError(ForgeError): pass
class MailError(ForgeError): pass
class AddressException(MailError): pass
class NoSuchNBFeatureError(ForgeError): pass
class InvalidNBFeatureValueError(ForgeError): pass

class CompoundError(ForgeError):
    def __repr__(self):
        return '<%s>\n%s\n</%s>'  % (
            self.__class__.__name__,
            '\n'.join(map(repr, self.args)),
            self.__class__.__name__)
    def format_error(self):
        import traceback
        parts = [ '<%s>\n' % self.__class__.__name__ ]
        for tp,val,tb in self.args:
            for line in traceback.format_exception(tp,val,tb):
                parts.append('    ' + line)
        parts.append('</%s>\n' % self.__class__.__name__ )
        return ''.join(parts)
                
