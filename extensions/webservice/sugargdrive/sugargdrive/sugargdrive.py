# Copyright (c) 2013 Ignacio Rodriguez <ignacio@sugarlabs.org>
# Thx to Jorge Alberto Gomez Lopez <gomezlopez.jorge96@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import os
import sys
import subprocess
import logging
from gettext import gettext as _
from sugar3 import env
from gi.repository import GObject
from gi.repository import GConf

GOOGLE_API = os.path.join(env.get_profile_path(), 'extensions', 'webservice')
sys.path.append(GOOGLE_API)
try:
    import simplejson as json
except:
    import json

import httplib2
from apiclient.discovery import build
from apiclient.http import MediaFileUpload
from oauth2client.client import OAuth2WebServerFlow
from oauth2client.client import FlowExchangeError
client = GConf.Client.get_default()

CLIENT_ID = "79915831092-oavi9geds5iokcn8c9okeediu92udi94.apps.googleusercontent.com"
CLIENT_SECRET = "KfvpSENuGzrafcTFI4iXxj6g"
OAUTH_SCOPE = 'https://www.googleapis.com/auth/drive'
REDIRECT_URI = "https://www.sugarlabs.org"


# Copied from grestful code
def asynchronous(method):
    """ Convenience wrapper for GObject.idle_add. """
    def _async(*args, **kwargs):
        GObject.idle_add(method, *args, **kwargs)
    return _async



class Upload(GObject.GObject):

    __gsignals__ = {
        'upload-finished': (GObject.SignalFlags.RUN_FIRST, None,
                                    ([str])),
        'upload-error': (GObject.SignalFlags.RUN_FIRST, None,
                                    ([str])),
    }

    def __init__(self):
        GObject.GObject.__init__(self)

    @asynchronous
    def upload(self, path, title, description, mime_type, token):
        # Run through the OAuth flow and retrieve credentials
        flow = OAuth2WebServerFlow(CLIENT_ID, CLIENT_SECRET, OAUTH_SCOPE, REDIRECT_URI)
        code = client.get_string(token)

        if not code:
            self.emit('upload-error', 
                    _('Token expired, please update your'
                    ' token in Control Panel.'))
            return False

        authorize_url = flow.step1_get_authorize_url()
        try:
            credentials = flow.step2_exchange(code)
        except Exception, error:
            error = str(error)
            if error.startswith('Unable to find the server'):
                self.emit('upload-error',
                    _('No internet connection. '
                        'You need internet for upload files.'))

            elif 'invalid_grant' in error:
                self.emit('upload-error', 
                    _('Token expired, please update your'
                    ' token in Control Panel.'))

            else:
                self.emit('upload-error',
                    _('Unknown error. Please send a email to developers.'))
            return False

        # Create an httplib2.Http object and authorize it with our credentials
        http = httplib2.Http()
        http = credentials.authorize(http)

        drive_service = build('drive', 'v2', http=http)

        mime_type = subprocess.Popen("/usr/bin/file -b --mime-type " + path,
            shell=True, stdout=subprocess.PIPE).communicate()[0][:-1]

        media_body = MediaFileUpload(path, mimetype=mime_type,
                resumable=True)

        body = {
          'title': title,
          'description': description,
          'mimeType': mime_type
        }

        file_upload = drive_service.files()
        file_upload = file_upload.insert(body=body, media_body=media_body)
        file_upload = file_upload.execute()

        if 'Revoked: true' in file_upload or 'invalid' in file_upload:
            self.emit('upload-error', 
                _('Token expired, please update your'
                ' token in Control Panel.'))
            return False

        self.emit('upload-finished', file_upload['alternateLink'])