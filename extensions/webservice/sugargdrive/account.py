# Copyright (c) 2014 Ignacio Rodriguez <ignacio@sugarlabs.org>
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

try:
    import simplejson as json
except ImportError:
    import json

from gettext import gettext as _

from gi.repository import GConf
from gi.repository import Gdk
from gi.repository import GLib
from gi.repository import GObject
from gi.repository import Gtk

from sugar3 import env
from sugar3 import profile
from sugar3.datastore import datastore
from sugar3.graphics.alert import NotifyAlert
from sugar3.graphics.icon import Icon
from sugar3.graphics.menuitem import MenuItem
from sugar3.graphics.palette import Palette
from sugar3.graphics.palettemenu import PaletteMenuBox
from sugar3.graphics.palettemenu import PaletteMenuItem

from jarabe.journal import journalwindow
from jarabe.journal import model
from jarabe.journal.journalactivity import get_journal
from jarabe.journal.misc import _get_icon_for_mime
from jarabe.journal.volumestoolbar import ExtensionButton
from jarabe.webservice import account
from jarabe.webservice import accountsmanager


GOOGLE_API = os.path.join(env.get_profile_path(), 'extensions', 'webservice')
sys.path.append(GOOGLE_API)

ICONS_PATH = os.path.join(env.get_profile_path(),
    'extensions', 'webservice', 'sugargdrive', 'icons')

theme = Gtk.IconTheme.get_default()
theme.append_search_path(ICONS_PATH)

ACCOUNT_DESCRIPTION = _('Upload to Google Drive')
ACCOUNT_NAME = _('Sugar Google Drive')
ACCOUNT_ICON = 'sugargdrive'
TOKEN_KEY = "/desktop/sugar/collaboration/gdrive_token"
USER_FILES = os.path.join(env.get_profile_path(), 'gdrive_files.json')


class FilesModel(Gtk.ListStore):
    def __init__(self, account):
        Gtk.ListStore.__init__(self, str, bool, str, object, str, str, str, 
            int, object, object, object, bool, str, str)

        self._account = account

    def do_drag_data_get(self, path, selection):
        data = self.get_iter(path)
        title = self.get_value(data, 4)
        link = self.get_value(data, 12)
        mime_type = self.get_value(data, 13)
        self._account.Download(link, title, mime_type)


class ExtensionPalette(Palette):

    def __init__(self):
        label = GLib.markup_escape_text(ACCOUNT_NAME)
        account_icon = Icon(icon_name=ACCOUNT_ICON,
            xo_color=profile.get_color(),
            icon_size=Gtk.IconSize.MENU)

        Palette.__init__(self, primary_text=label,
                         icon=account_icon)

        self.menu_box = PaletteMenuBox()

        self.menu_item = PaletteMenuItem(_('Update'), 'view-refresh')
        self.menu_box.append_item(self.menu_item)

        self.set_content(self.menu_box)
        self.menu_box.show_all()

    def set_item_cb(self, callback):
        self.menu_item.connect('activate', callback)


class SharedJournalEntry():
    def get_share_menu(self, get_uid_list):
        raise NotImplementedError

    def set_metadata(self, metadata):
        raise NotImplementedError


class Account(account.Account):

    def __init__(self):
        self.upload = accountsmanager.get_service('sugargdrive')
        self._shared_journal_entry = None
        self._journal = None
        self._model = None
        self._alert = None
        self._listview = None

    def get_description(self):
        return ACCOUNT_DESCRIPTION

    def add_journal_button(self):
        if not self._journal:
            palette = ExtensionPalette()
            self._journal = get_journal()
            self._listview = self._journal.get_list_view()
            self._volumes_toolbar = self._journal.get_volumes_toolbar()
            volume_button = ExtensionButton(ACCOUNT_ICON, ICONS_PATH)
            volume_button.connect('toggled', self._journal_toggled)
            volume_button.connect('load-files', self._load_files)
            volume_button.connect('data-upload', self._upload_file)
            self._volumes_toolbar.add_extension_button(volume_button,
                ACCOUNT_NAME, palette)
            palette.set_item_cb(self.update_files)

    def get_token_state(self):
        return self.STATE_VALID

    def get_shared_journal_entry(self):
        if self._shared_journal_entry is None:
            self._shared_journal_entry = _SharedJournalEntry(self)

        return self._shared_journal_entry


    def _journal_toggled(self, widget):
        self._journal.get_window().set_cursor(None)
        option = widget.props.active
        if option:
            option = False
        else:
            option = True
        self._listview.use_options(option)

    def _load_files(self, widget):
        if not self._model:
            self._model = FilesModel(self.upload)
        self._model.clear()
        self._listview.tree_view.set_model(self._model)

        def internal_callback():
            files = []
            if os.path.exists(USER_FILES):
                f = open(USER_FILES, 'r')
                try:
                    data = json.load(f)
                except:
                    files = []
                    os.remove(USER_FILES)
                    self._journal.get_window().set_cursor(None)
                    f.close()
                    return
                
                for userfile in data['items']:
                    txt = '<span weight="bold">%s</span>' % (
                        GLib.markup_escape_text(userfile['title']))
                    icon_name = _get_icon_for_mime(userfile['mimeType'])
                    link = userfile['selfLink']
                    itter = self._model.insert(-1, ['', False, icon_name,
                        profile.get_color(), txt, '', '', 50,
                        profile.get_color(), profile.get_color(),
                        profile.get_color(), True, link, userfile['mimeType']])
                    files.append(itter)

            if len(files) == 0 or not os.path.exists(USER_FILES):
                self._listview._show_message(_('No files in your '
                    'account, please update information clickeando '
                    'en el menu del icono'),
                    icon_name=ACCOUNT_ICON)
            else:
                self._listview._clear_message()

            self._journal.get_window().set_cursor(None)
    
        self._listview._show_message(_('Loading files...'),
                icon_name=ACCOUNT_ICON)
        cursor = Gdk.Cursor.new(Gdk.CursorType.WATCH)
        self._journal.get_window().set_cursor(cursor)
        GObject.idle_add(internal_callback)

    def _upload_file(self, widget, metadata):
        account = self._shared_journal_entry._menu
        account.connect('transfer-state-changed', self._display_alert_cb)
        account.upload_file(None, metadata)


    def _display_alert_cb(self, widget, title, message):
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

    def update_files(self, widget):
        self._listview._show_message(_('Updating file list...'),
                icon_name=ACCOUNT_ICON)
        cursor = Gdk.Cursor.new(Gdk.CursorType.WATCH)

        def internal_callback():
            inst = self.upload.Upload()
            inst.update_files(self._display_alert_cb, TOKEN_KEY, 
                self._load_files)
            self._listview._clear_message()

        GObject.idle_add(internal_callback)


class _SharedJournalEntry(SharedJournalEntry):
    __gsignals__ = {
        'transfer-state-changed': (GObject.SignalFlags.RUN_FIRST, None,
                                   ([str, str])),
    }

    def __init__(self, account):
        self._account = account
        self._alert = None

    def get_share_menu(self, get_uid_list):
        self._menu = _ShareMenu(self._account, get_uid_list, True)
        self._connect_transfer_signals(self._menu)
        return self._menu

    def _connect_transfer_signals(self, transfer_widget):
        transfer_widget.connect('transfer-state-changed',
                                self._account._display_alert_cb)


class _ShareMenu(MenuItem):
    __gsignals__ = {
        'transfer-state-changed': (GObject.SignalFlags.RUN_FIRST, None,
                                   ([str, str])),
    }

    def __init__(self, account, get_uid_list, is_active):
        MenuItem.__init__(self, ACCOUNT_DESCRIPTION)

        self._account = account
        self.set_image(Icon(icon_name=ACCOUNT_ICON,
                            icon_size=Gtk.IconSize.MENU))
        self.show()
        self._get_uid_list = get_uid_list
        self.connect('activate', self.upload_file)

    def _get_metadata(self):
        return model.get(self._get_uid_list()[0])

    def _get_data(self, metadata=None):
        if not metadata:
            metadata = self._get_metadata()
        jobject = datastore.get(metadata['uid'])
        path = str(jobject.file_path)

        return path

    def _get_description(self, metadata=None):
        if not metadata:
            metadata = self._get_metadata()
        description = ""
        if 'description' in metadata:
            description = str(metadata['description'])

        return description

    def _get_title(self, metadata=None):
        if not metadata:
            metadata = self._get_metadata()
        title = _('Sugar upload')
        if 'title' in metadata:
            title = str(metadata['title'])

        return title

    def upload_file(self, menu_item, metadata=None):
        path = self._get_data(metadata)
        title = self._get_title(metadata)
        description = self._get_description(metadata)

        self.emit('transfer-state-changed', _('Google drive'),
                _('Upload started'))

        upload = self._account.upload.Upload()
        upload.connect('upload-error', self.upload_error)
        upload.connect('upload-finished', self.upload_completed)
        upload.upload(path, title, description, TOKEN_KEY)

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