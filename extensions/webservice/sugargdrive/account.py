# Copyright (c) 2013 Ignacio Rodriguez <ignacio@sugarlabs.org>
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
import logging
from sugar3 import env
from gettext import gettext as _

from gi.repository import Gtk
from gi.repository import GObject

from sugar3.graphics.alert import NotifyAlert
from sugar3.graphics.icon import Icon
from sugar3.graphics.menuitem import MenuItem
from sugar3.datastore import datastore

from jarabe.journal import journalwindow
from jarabe.journal import model
from jarabe.webservice import account, accountsmanager

GOOGLE_API = os.path.join(env.get_profile_path(), 'extensions', 'webservice')
sys.path.append(GOOGLE_API)


ACCOUNT_NAME = _('Upload to Google Drive')
ACCOUNT_ICON = 'sugargdrive'
TOKEN_KEY = "/desktop/sugar/collaboration/gdrive_token"


class SharedJournalEntry():
    def get_share_menu(self, get_uid_list):
        raise NotImplementedError

    def set_metadata(self, metadata):
        raise NotImplementedError


class Account(account.Account):

    def __init__(self):
        self.upload = accountsmanager.get_service('sugargdrive')
        self._shared_journal_entry = None

    def get_description(self):
        return ACCOUNT_NAME

    def get_token_state(self):
        return self.STATE_VALID

    def get_shared_journal_entry(self):
        if self._shared_journal_entry is None:
            self._shared_journal_entry = _SharedJournalEntry(self)
        return self._shared_journal_entry


class _SharedJournalEntry(SharedJournalEntry):
    __gsignals__ = {
        'transfer-state-changed': (GObject.SignalFlags.RUN_FIRST, None,
                                   ([str, str])),
    }

    def __init__(self, account):
        self._account = account
        self._alert = None

    def get_share_menu(self, get_uid_list):
        menu = _ShareMenu(self._account, get_uid_list, True)
        self._connect_transfer_signals(menu)
        return menu

    def __display_alert_cb(self, widget, title, message):
        if self._alert is None:
            self._alert = NotifyAlert()
            self._alert.connect('response', self.__alert_response_cb)
            journalwindow.get_journal_window().add_alert(self._alert)
            self._alert.show()

        self._alert.props.title = title
        self._alert.props.msg = message

    def __alert_response_cb(self, alert, response_id):
        journalwindow.get_journal_window().remove_alert(alert)
        self._alert = None

    def _connect_transfer_signals(self, transfer_widget):
        transfer_widget.connect('transfer-state-changed',
                                self.__display_alert_cb)

class _ShareMenu(MenuItem):
    __gsignals__ = {
        'transfer-state-changed': (GObject.SignalFlags.RUN_FIRST, None,
                                   ([str, str])),
    }

    def __init__(self, account, get_uid_list, is_active):
        MenuItem.__init__(self, ACCOUNT_NAME)
        from sugar3.datastore import datastore

        self._account = account
        self.set_image(Icon(icon_name=ACCOUNT_ICON,
                            icon_size=Gtk.IconSize.MENU))
        self.show()
        self._get_uid_list = get_uid_list
        self.connect('activate', self.__share_menu_cb)

    def _get_metadata(self):
        return model.get(self._get_uid_list()[0])

    def _get_data(self):
        metadata = self._get_metadata()
        jobject = datastore.get(metadata['uid'])
        path = str(jobject.file_path)

        return path

    def _get_mimetype(self):
        metadata = self._get_metadata()
        mime_type = "text/plain"
        if 'mime_type' in metadata:
            title = str(metadata['mime_type'])

        return mime_type

    def _get_description(self):
        metadata = self._get_metadata()
        description = ""
        if 'description' in metadata:
            description = str(metadata['description'])

        return description

    def _get_title(self):
        metadata = self._get_metadata()
        title = _('Sugar upload')
        if 'title' in metadata:
            title = str(metadata['title'])

        return title

    def __share_menu_cb(self, menu_item):
        path = self._get_data()
        title = self._get_title()
        mime_type = self._get_mimetype()
        description = self._get_description()

        self.emit('transfer-state-changed', _('Google drive'),
                _('Upload started'))

        upload = self._account.upload.Upload()
        upload.connect('upload-error', self.upload_error)
        upload.connect('upload-finished', self.upload_completed)
        upload.upload(path, title, description,
                mime_type, TOKEN_KEY)


    def upload_completed(self, widget, link):
        metadata = self._get_metadata()
        tags = '%s %s' % (metadata.get('tags', ''), link)

        ds_object = datastore.get(metadata['uid'])
        ds_object.metadata['tags'] = tags
        datastore.write(ds_object, update_mtime=False)

        self.emit('transfer-state-changed', _('Google drive'),
                _('Upload finished. Link saved in tags of entry.'))

    def upload_error(self, widget, msg):
        self.emit('transfer-state-changed', _('Google drive'), msg)

def get_account():
    return Account()
