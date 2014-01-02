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
import json
import tempfile

from gettext import gettext as _

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
USER_FILES = os.path.join(env.get_profile_path(), 'gdrive_files')


class FilesModel(Gtk.ListStore):
    def __init__(self, account, display_alert, load_files,
            journal_button, listview):
        Gtk.ListStore.__init__(self, str, bool, str, object, str, str, str,
            int, object, object, object, bool, str, str, str, str)

        self._account = account
        self._display_alert = display_alert
        self._load_files = load_files
        self._journal_button = journal_button
        self._listview = listview

    def do_drag_data_get(self, path, selection):
        data = self.get_iter(path)
        mime_type = self.get_value(data, 13)
        fileid = self.get_value(data, 14)
        title = self.get_value(data, 15)
        data = self._account.download_file(fileid, self._display_alert)

        fd, file_path = tempfile.mkstemp(dir="/tmp/")
        os.close(fd)

        f = open(file_path, 'w')
        f.write(data)
        f.close()

        jobject = datastore.create()
        jobject.metadata['title'] = title
        jobject.metadata['icon-color'] = profile.get_color().to_string()
        jobject.metadata['mime_type'] = mime_type
        if data:
            jobject.file_path = file_path
        datastore.write(jobject)
        self._load_files()
        self._journal_button.set_active(True)
        self._listview.refresh()


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
        self._volume_button = None
        self._cid = None
        self._ccid = None

    def get_description(self):
        return ACCOUNT_DESCRIPTION

    def add_journal_button(self):
        if not self._journal:
            palette = ExtensionPalette()
            self._journal = get_journal()
            self._listview = self._journal.get_list_view()
            self._volumes_toolbar = self._journal.get_volumes_toolbar()

            self._volume_button = ExtensionButton(ACCOUNT_ICON, ICONS_PATH)
            self._volume_button.connect('toggled', self._journal_toggled)
            self._volume_button.connect('load-files', self._load_files)
            self._volume_button.connect('data-upload', self._upload_file)
            self._volumes_toolbar.add_extension_button(self._volume_button,
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
        self._cid = self._listview.tree_view.connect('drag-begin',
            self.turn_off_buttons)
        self._ccid = self._listview.tree_view.connect('drag-end',
            self.turn_on_buttons)

        if option:
            option = False
        else:
            self._listview.tree_view.disconnect(self._cid)
            self._listview.tree_view.disconnect(self._ccid)
            self._cid = None
            self._ccid = None
            option = True
        self._listview.use_options(option)

    def _load_files(self, *kwargs):
        if not self._model:
            journal_button = self._volumes_toolbar._volume_buttons[0]
            self._model = FilesModel(self.upload, self._display_alert_cb,
                self._load_files, journal_button, self._listview)
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
                    data = []

                isdict = False
                if isinstance(data, dict):
                    isdict = True

                if isdict:
                    data = data['items']

                for userfile in data:
                    txt = '<span weight="bold">%s</span>' % (
                        GLib.markup_escape_text(userfile['title']))
                    icon_name = _get_icon_for_mime(userfile['mimeType'])
                    link = userfile['alternateLink']

                    itter = self._model.insert(-1, [
                        '', False, icon_name,
                        profile.get_color(), txt, '', '', 50,
                        profile.get_color(), profile.get_color(),
                        profile.get_color(), True, link,
                        userfile['mimeType'],
                        userfile['id'],
                        userfile['title']])

                    files.append(itter)

            if len(files) == 0 or not os.path.exists(USER_FILES):
                self._listview._show_message(_('No files in your '
                    'account, please update your file list '
                    'clicking in the toolbar menu option.'),
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
        self._journal.get_window().set_cursor(cursor)

        def internal_callback():
            inst = self.upload.Upload()
            inst.update_files(self._display_alert_cb, self._load_files)
            self._listview._clear_message()
            self._journal.get_window().set_cursor(None)

        GObject.idle_add(internal_callback)

    def turn_off_buttons(self, *kwargs):
        buttons = self._volumes_toolbar._volume_buttons
        current = 0
        for button in buttons:
            if current == 0:
                button.set_sensitive(True)
            else:
                button.set_sensitive(False)
            current += 1

    def turn_on_buttons(self, *kwargs):
        buttons = self._volumes_toolbar._volume_buttons
        for button in buttons:
            button.set_sensitive(True)


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
        upload.upload(path, title, description)

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
