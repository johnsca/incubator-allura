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

import smtpd
import asyncore

import tg
from paste.script import command

import allura.tasks
from allura.command import base

from paste.deploy.converters import asint

class SMTPServerCommand(base.Command):
    min_args=1
    max_args=1
    usage = '<ini file>'
    summary = 'Handle incoming emails, routing them to RabbitMQ'
    parser = command.Command.standard_parser(verbose=True)
    parser.add_option('-c', '--context', dest='context',
                      help=('The context of the message (path to the project'
                            ' and/or tool'))

    def command(self):
        self.basic_setup()
        MailServer((tg.config.get('forgemail.host', '0.0.0.0'),
                    asint(tg.config.get('forgemail.port', 8825))),
                   None)
        asyncore.loop()

class MailServer(smtpd.SMTPServer):

    def process_message(self, peer, mailfrom, rcpttos, data):
        base.log.info('Msg Received from %s for %s', mailfrom, rcpttos)
        base.log.info(' (%d bytes)', len(data))
        allura.tasks.mail_tasks.route_email(
            peer=peer, mailfrom=mailfrom, rcpttos=rcpttos, data=data)
        base.log.info('Msg passed along')
