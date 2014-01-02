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

import logging
import os
import sys
import json

import subprocess
from gettext import gettext as _
import tempfile
import dbus
import cairo
import StringIO

from gi.repository import GConf
from gi.repository import Gtk
from gi.repository import Gdk
from gi.repository import WebKit
from gi.repository import GdkPixbuf
from gi.repository import GObject

from jarabe.journal import journalwindow

from sugar3.datastore import datastore
from sugar3 import profile
from sugar3 import mime
from sugar3.graphics.alert import Alert, TimeoutAlert, NotifyAlert
from sugar3.graphics.icon import Icon
from sugar3.activity import activity
from sugar3 import env

GOOGLE_API = os.path.join(env.get_profile_path(), 'extensions', 'webservice')
sys.path.append(GOOGLE_API)
USER_FILES = os.path.join(env.get_profile_path(), 'gdrive_files.json')

import httplib2
from apiclient.discovery import build
from apiclient.http import MediaFileUpload
from oauth2client.client import OAuth2WebServerFlow
client = GConf.Client.get_default()

CLIENT_ID = "79915831092-oavi9geds5iokcn8c9okeediu92udi94.apps." \
    "googleusercontent.com"
CLIENT_SECRET = "KfvpSENuGzrafcTFI4iXxj6g"
OAUTH_SCOPE = 'https://www.googleapis.com/auth/drive'
REDIRECT_URI = "https://www.sugarlabs.org"
ACCOUNT_NAME = _('Sugar Google Drive')

DS_DBUS_SERVICE = 'org.laptop.sugar.DataStore'
DS_DBUS_INTERFACE = 'org.laptop.sugar.DataStore'
DS_DBUS_PATH = '/org/laptop/sugar/DataStore'

_active_downloads = []
_dest_to_window = {}

PROGRESS_TIMEOUT = 3000
SPACE_THRESHOLD = 52428800  # 50 Mb


def format_float(f):
    return "%0.2f" % f


def remove_all_downloads():
    for download in _active_downloads:
        download.cancel()
        if download.dl_jobject is not None:
            datastore.delete(download.dl_jobject.object_id)
        download.cleanup()


# Copied from grestful code
def asynchronous(method):
    """ Convenience wrapper for GObject.idle_add. """
    def _async(*args, **kwargs):
        GObject.idle_add(method, *args, **kwargs)
    return _async


class DownloadFiles(WebKit.WebView):
    def __init__(self, uri, title, mime, listview, button):
        WebKit.WebView.__init__(self)
        self._link = uri
        self._title = title
        self._mime = mime
        self._listview = listview
        self._button = button

        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_policy(Gtk.PolicyType.AUTOMATIC,
            Gtk.PolicyType.AUTOMATIC)
        self.scroll.add(self)

        self._listview._show_widget(self.scroll)
        self.connect('download-requested', self.__download_requested_cb)
        self.connect('mime-type-policy-decision-requested',
                     self.__mime_type_policy_cb)
        self.load_uri(uri)

    def __download_requested_cb(self, browser, download):
        Download(download, self._mime)

        def internal_callback():
            journal_button = self._button._volumes_toolbar._volume_buttons[0]
            journal_button.set_active(True)

        GObject.idle_add(internal_callback)
        return True

    def __mime_type_policy_cb(self, webview, frame, request, mimetype,
                              policy_decision):
        if 'html'in mimetype:
            return True
        else:
            policy_decision.download()
            return True


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
    def upload(self, path, title, description, token):
        # Run through the OAuth flow and retrieve credentials
        flow = OAuth2WebServerFlow(CLIENT_ID, CLIENT_SECRET,
            OAUTH_SCOPE, REDIRECT_URI)
        code = client.get_string(token)

        if not code:
            self.emit('upload-error',
                    _('Token expired, please update your'
                    ' token in Control Panel.'))
            return False

        flow.step1_get_authorize_url()
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
                logging.debug("For developers: %s" % error)
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

    def update_files(self, display_alert, key, load_files):
        flow = OAuth2WebServerFlow(CLIENT_ID, CLIENT_SECRET,
            OAUTH_SCOPE, REDIRECT_URI)
        code = client.get_string(key)

        if not code:
            display_alert(None, ACCOUNT_NAME,
                    _('Token expired, please update your'
                    ' token in Control Panel.'))
            return False

        flow.step1_get_authorize_url()

        try:
            credentials = flow.step2_exchange(code)
        except Exception, error:
            error = str(error)
            if error.startswith('Unable to find the server'):
                display_alert(None, ACCOUNT_NAME,
                    _('No internet connection. '
                        'You need internet for upload files.'))

            elif 'invalid_grant' in error:
                display_alert(None, ACCOUNT_NAME,
                    _('Token expired, please update your'
                    ' token in Control Panel.'))

            else:
                logging.debug("For developers: %s" % error)
                display_alert(None, ACCOUNT_NAME,
                    _('Unknown error. Please send a email to developers.'))
            return False
        http = httplib2.Http()
        http = credentials.authorize(http)

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


class Download(object):
    def __init__(self, download, mime):
        self._download = download
        self._journal = journalwindow.get_journal_window()
        self._source = download.get_uri()

        self._download.connect('notify::status', self.__state_change_cb)
        self._download.connect('error', self.__error_cb)

        self.datastore_deleted_handler = None
        self._filename = self._download.get_suggested_filename()
        self._mimetype = mime

        self.dl_jobject = None
        self._object_id = None
        self._stop_alert = None

        self._progress = 0
        self._last_update_progress = 0
        self._progress_sid = None

        # figure out download URI
        self.temp_path = os.path.join(env.get_profile_path(), 'tmp')
        if not os.path.exists(self.temp_path):
            os.makedirs(self.temp_path)

        fd, self._dest_path = tempfile.mkstemp(dir=self.temp_path,
                                    suffix=download.get_suggested_filename(),
                                    prefix='tmp')
        os.close(fd)

        # We have to start the download to get 'total-size'
        # property. It not, 0 is returned
        self._download.set_destination_uri('file://' + self._dest_path)
        self._download.start()

    def _update_progress(self):
        if self._progress > self._last_update_progress:
            self._last_update_progress = self._progress
            self.dl_jobject.metadata['progress'] = str(self._progress)
            datastore.write(self.dl_jobject)

        self._progress_sid = None
        return False

    def __progress_change_cb(self, download, something):
        self._progress = int(self._download.get_progress() * 100)

        if self._progress_sid is None:
            self._progress_sid = GObject.timeout_add(
                PROGRESS_TIMEOUT, self._update_progress)

    def __current_size_changed_cb(self, download, something):
        current_size = self._download.get_current_size()
        total_size = self._download.get_total_size()
        self._progress = int(current_size * 100 / total_size)

        if self._progress_sid is None:
            self._progress_sid = GObject.timeout_add(
                PROGRESS_TIMEOUT, self._update_progress)

    def __state_change_cb(self, download, gparamspec):
        state = self._download.get_status()
        if state == WebKit.DownloadStatus.STARTED:
            # Check free space and cancel the download if there is not enough.
            total_size = self._download.get_total_size()
            logging.debug('Total size of the file: %s', total_size)
            enough_space = self.enough_space(
                total_size, path=self.temp_path)
            if not enough_space:
                logging.debug('Download canceled because of Disk Space')
                self.cancel()

                self._canceled_alert = Alert()
                self._canceled_alert.props.title = _('Not enough space '
                                                     'to download')

                total_size_mb = total_size / 1024.0 ** 2
                free_space_mb = (self._free_available_space(
                    path=self.temp_path) - SPACE_THRESHOLD) \
                    / 1024.0 ** 2
                filename = self._download.get_suggested_filename()
                self._canceled_alert.props.msg = _(
                    'Download "%{filename}" requires %{total_size_in_mb}'
                      ' MB of free space, only %{free_space_in_mb} MB'
                      ' is available' %
                      {'filename': filename,
                       'total_size_in_mb': format_float(total_size_mb),
                       'free_space_in_mb': format_float(free_space_mb)})
                ok_icon = Icon(icon_name='dialog-ok')
                self._canceled_alert.add_button(Gtk.ResponseType.OK,
                                                _('Ok'), ok_icon)
                ok_icon.show()
                self._canceled_alert.connect('response',
                                             self.__stop_response_cb)
                self.add_alert(self._canceled_alert)
            else:
                # FIXME: workaround for SL #4385
                # self._download.connect('notify::progress',
                #                        self.__progress_change_cb)
                self._download.connect('notify::current-size',
                                       self.__current_size_changed_cb)

                self._create_journal_object()
                self._object_id = self.dl_jobject.object_id

                alert = TimeoutAlert(9)
                alert.props.title = _('Download started')
                alert.props.msg = _('%s' %
                                    self._filename)
                self.add_alert(alert)
                alert.connect('response', self.__start_response_cb)
                alert.show()
                global _active_downloads
                _active_downloads.append(self)

        elif state == WebKit.DownloadStatus.FINISHED:
            self._stop_alert = Alert()
            self._stop_alert.props.title = _('Download completed')
            self._stop_alert.props.msg = \
                _('%s' % self._filename)
            ok_icon = Icon(icon_name='dialog-ok')
            self._stop_alert.add_button(Gtk.ResponseType.OK, _('Ok'), ok_icon)
            ok_icon.show()
            self.add_alert(self._stop_alert)
            self._stop_alert.connect('response', self.__stop_response_cb)
            self._stop_alert.show()

            if self._progress_sid is not None:
                GObject.source_remove(self._progress_sid)

            self.dl_jobject.metadata['title'] = self._filename
            self.dl_jobject.metadata['description'] = _('From: %s') \
                % self._source
            self.dl_jobject.metadata['progress'] = '100'
            self.dl_jobject.file_path = self._dest_path

            # sniff for a mime type, no way to get headers from WebKit
            sniffed_mime_type = mime.get_for_file(self._dest_path)
            self.dl_jobject.metadata['mime_type'] = sniffed_mime_type

            if sniffed_mime_type in ('image/bmp', 'image/gif', 'image/jpeg',
                                     'image/png', 'image/tiff'):
                preview = self._get_preview()
                if preview is not None:
                    self.dl_jobject.metadata['preview'] = \
                        dbus.ByteArray(preview)

            datastore.write(self.dl_jobject,
                            transfer_ownership=True,
                            reply_handler=self.__internal_save_cb,
                            error_handler=self.__internal_error_cb,
                            timeout=360)

        elif state == WebKit.DownloadStatus.CANCELLED:
            self.cleanup()

    def add_alert(self, alert):
        alerts = self._journal._alerts
        for alert in alerts:
            self._journal.remove_alert(alert)

        self._journal.add_alert(alert)

    def __error_cb(self, download, err_code, err_detail, reason):
        alert = NotifyAlert(10)
        alert.props.title = ACCOUNT_NAME
        alert.props.msg = _('Error downloading file: %s' % reason)
        self.add_alert(alert)
        alert.connect('response', lambda x, y: self._journal.remove_alert(x))

    def __internal_save_cb(self):
        logging.debug('Object saved succesfully to the datastore.')
        self.cleanup()

    def __internal_error_cb(self, err):
        logging.debug('Error saving activity object to datastore: %s' % err)
        self.cleanup()

    def __start_response_cb(self, alert, response_id):
        global _active_downloads
        if response_id is Gtk.ResponseType.CANCEL:
            logging.debug('Download Canceled')
            self.cancel()
            try:
                datastore.delete(self._object_id)
            except Exception, e:
                logging.warning('Object has been deleted already %s' % e)

            self.cleanup()
            if self._stop_alert is not None:
                self._journal.remove_alert(self._stop_alert)

        self._journal.remove_alert(alert)

    def __stop_response_cb(self, alert, response_id):
        self._journal.remove_alert(alert)

    def cleanup(self):
        global _active_downloads
        if self in _active_downloads:
            _active_downloads.remove(self)

        if self.datastore_deleted_handler is not None:
            self.datastore_deleted_handler.remove()
            self.datastore_deleted_handler = None

        if os.path.isfile(self._dest_path):
            os.remove(self._dest_path)

        if self.dl_jobject is not None:
            self.dl_jobject.destroy()
            self.dl_jobject = None

    def cancel(self):
        self._download.cancel()

    def enough_space(self, size, path='/'):
        """Check if there is enough (size) free space on path

        size -- free space requested in Bytes

        path -- device where the check will be done. For example: '/tmp'

        This method is useful to check the free space, for example,
        before starting a download from internet, creating a big map
        in some game or whatever action that needs some space in the
        Hard Disk.
        """

        free_space = self._free_available_space(path=path)
        return free_space - size > SPACE_THRESHOLD

    def _free_available_space(self, path='/'):
        """Return available space in Bytes

        This method returns the available free space in the 'path' and
        returns this amount in Bytes.
        """

        s = os.statvfs(path)
        return s.f_bavail * s.f_frsize

    def _create_journal_object(self):
        self.dl_jobject = datastore.create()
        self.dl_jobject.metadata['title'] = \
            _('Downloading %(filename)s from \n%(source)s.') % \
            {'filename': self._filename,
             'source': self._source}

        self.dl_jobject.metadata['progress'] = '0'
        self.dl_jobject.metadata['keep'] = '0'
        self.dl_jobject.metadata['buddies'] = ''
        self.dl_jobject.metadata['preview'] = ''
        self.dl_jobject.metadata['icon-color'] = \
                profile.get_color().to_string()
        self.dl_jobject.metadata['mime_type'] = self._mimetype
        self.dl_jobject.file_path = ''
        datastore.write(self.dl_jobject)

        bus = dbus.SessionBus()
        obj = bus.get_object(DS_DBUS_SERVICE, DS_DBUS_PATH)
        datastore_dbus = dbus.Interface(obj, DS_DBUS_INTERFACE)
        self.datastore_deleted_handler = datastore_dbus.connect_to_signal(
            'Deleted', self.__datastore_deleted_cb,
            arg0=self.dl_jobject.object_id)

    def _get_preview(self):
        # This code borrows from sugar3.activity.Activity.get_preview
        # to make the preview with cairo, and also uses GdkPixbuf to
        # load any GdkPixbuf supported format.
        pixbuf = GdkPixbuf.Pixbuf.new_from_file(self._dest_path)
        image_width = pixbuf.get_width()
        image_height = pixbuf.get_height()

        preview_width, preview_height = activity.PREVIEW_SIZE
        preview_surface = cairo.ImageSurface(cairo.FORMAT_ARGB32,
                                             preview_width, preview_height)
        cr = cairo.Context(preview_surface)

        scale_w = preview_width * 1.0 / image_width
        scale_h = preview_height * 1.0 / image_height
        scale = min(scale_w, scale_h)

        translate_x = int((preview_width - (image_width * scale)) / 2)
        translate_y = int((preview_height - (image_height * scale)) / 2)

        cr.translate(translate_x, translate_y)
        cr.scale(scale, scale)

        cr.set_source_rgba(1, 1, 1, 0)
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.paint()
        Gdk.cairo_set_source_pixbuf(cr, pixbuf, 0, 0)
        cr.paint()

        preview_str = StringIO.StringIO()
        preview_surface.write_to_png(preview_str)
        return preview_str.getvalue()

    def __datastore_deleted_cb(self, uid):
        logging.debug('Downloaded entry has been deleted'
                          ' from the datastore: %r', uid)
        global _active_downloads
        if self in _active_downloads:
            self.cancel()
            self.cleanup()
