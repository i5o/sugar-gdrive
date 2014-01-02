# Copyright (c) 2014 Ignacio Rodriguez <ignacio@sugarlabs.org>
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

import json
import os
import sys

import subprocess
from gettext import gettext as _

from gi.repository import GObject
from sugar3 import env

GOOGLE_API = os.path.join(env.get_profile_path(), 'extensions', 'webservice')
sys.path.append(GOOGLE_API)
USER_FILES = os.path.join(env.get_profile_path(), 'gdrive_files')

import httplib2
from apiclient.discovery import build
from apiclient.http import MediaFileUpload
from oauth2client.client import OAuth2Credentials

CLIENT_ID = "79915831092-oavi9geds5iokcn8c9okeediu92udi94.apps." \
    "googleusercontent.com"
CLIENT_SECRET = "KfvpSENuGzrafcTFI4iXxj6g"
OAUTH_SCOPE = 'https://www.googleapis.com/auth/drive'
REDIRECT_URI = "https://www.sugarlabs.org"
TOKEN_FILE = os.path.join(env.get_profile_path(), 'gdrive_settings')
ACCOUNT_NAME = _('Sugar Google Drive')


# Copied from grestful code
def asynchronous(method):
    """ Convenience wrapper for GObject.idle_add. """
    def _async(*args, **kwargs):
        GObject.idle_add(method, *args, **kwargs)
    return _async


def download_file(fileid, display_alert):
    if not os.path.exists(TOKEN_FILE):
        display_alert(None, ACCOUNT_NAME, _('Token expired, please update your'
                ' token in Control Panel.'))
        return None

    f = open(TOKEN_FILE, 'r')
    data = f.read()
    f.close()
    credentials = OAuth2Credentials.from_json(data)

    # Create an httplib2.Http object and authorize it with our credentials
    http = httplib2.Http()
    try:
        http = credentials.authorize(http)
    except:
        display_alert(None, ACCOUNT_NAME, _('No internet connection. '
                'You need internet for upload files.'))
        return None

    drive_service = build('drive', 'v2', http=http)
    try:
        drive_file = drive_service.files().get(fileId=fileid).execute()
    except:
        display_alert(None, ACCOUNT_NAME, _('Token expired, please update your'
            ' token in Control Panel.'))
    download_url = drive_file.get('downloadUrl')

    if download_url:
        display_alert(None, ACCOUNT_NAME, _('Download started'))
        resp, content = drive_service._http.request(download_url)
        if resp.status == 200:
            display_alert(None, ACCOUNT_NAME, _('Download finished'))
            return content
        else:
            display_alert(None, ACCOUNT_NAME, _('An error occurred: %s' % resp))
            return None
    else:
        return None


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
    def upload(self, path, title, description):
        if not os.path.exists(TOKEN_FILE):
            self.emit('upload-error',
                    _('Token expired, please update your'
                    ' token in Control Panel.'))
            return False

        f = open(TOKEN_FILE, 'r')
        data = f.read()
        f.close()

        credentials = OAuth2Credentials.from_json(data)

        # Create an httplib2.Http object and authorize it with our credentials
        http = httplib2.Http()
        try:
            http = credentials.authorize(http)
        except:
            self.emit('upload-error',
                _('No internet connection. '
                    'You need internet for upload files.'))
            return False

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
        try:
            file_upload = file_upload.execute()
        except:
            self.emit('upload-error',
                _('Token expired, please update your'
                ' token in Control Panel.'))

        if 'Revoked: true' in file_upload or 'invalid' in file_upload:
            self.emit('upload-error',
                _('Token expired, please update your'
                ' token in Control Panel.'))
            return False

        self.emit('upload-finished', file_upload['alternateLink'])

    def update_files(self, display_alert, load_files):

        if not os.path.exists(TOKEN_FILE):
            display_alert(None, ACCOUNT_NAME,
                    _('Token expired, please update your'
                    ' token in Control Panel.'))
            return False

        f = open(TOKEN_FILE, 'r')
        data = f.read()
        f.close()
        credentials = OAuth2Credentials.from_json(data)
        http = httplib2.Http()
        try:
            http = credentials.authorize(http)
        except:
            display_alert(None, ACCOUNT_NAME,
                    _('No internet connection. '
                        'You need internet for upload files.'))

        drive_service = build('drive', 'v2', http=http)

        result = []
        page_token = None
        while True:
            try:
                param = {}
                if page_token:
                    param['pageToken'] = page_token
                files = drive_service.files().list(**param).execute()

                result.extend(files['items'])
                page_token = files.get('nextPageToken')
                if not page_token:
                    break
            except Exception, error:
                display_alert(ACCOUNT_NAME,
                    'An error occurred: %s' % error)
                break

        f = open(USER_FILES, 'w')
        f.write(json.dumps(files))
        f.close()
        load_files()