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

from gi.repository import GObject

import httplib2
from apiclient.discovery import build
from apiclient.http import MediaFileUpload
from oauth2client.client import OAuth2WebServerFlow
from oauth2client.client import FlowExchangeError
client = GConf.Client.get_default()

CLIENT_ID = "79915831092-oavi9geds5iokcn8c9okeediu92udi94.apps.googleusercontent.com"
CLIENT_SECRET = "KfvpSENuGzrafcTFI4iXxj6g"
OAUTH_SCOPE = 'https://www.googleapis.com/auth/drive'
REDIRECT_URI = "https://www.sugarlabs.org/"


class Upload(Object):

    def upload_(self, path, title, description, mime_type, token):
        flow = OAuth2WebServerFlow(CLIENT_ID, CLIENT_SECRET,
                OAUTH_SCOPE, REDIRECT_URI)
        code = client.get_string(token)
        if not code:
            raise FlowExchangeError('invalid_grant')

        credentials = flow.step2_exchange(code)
        # Create an httplib2.Http object and authorize it with our credentials
        http = httplib2.Http()
        http = credentials.authorize(http)

        drive_service = build('drive', 'v2', http=http)

        # Insert a file
        media_body = MediaFileUpload(path, mimetype=mime_type)
        body = {
          'title': '%s',
          'description': '%s',
          'mimeType': '%s'
        } % (title, description, mime_type)


        file_result = drive_service.files()
        file_result.insert(body=body, media_body=media_body)
        file_result = file_result.execute()

        print file_result

    def upload(self, path, title, description, mime_type, token):
        GObject.idle_add(self.upload_, path, title, description,
            mime_type, token)