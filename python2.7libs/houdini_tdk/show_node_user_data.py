"""
Tool Development Kit for SideFX Houdini
Copyright (C) 2021  Ivan Titov

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

import json
import re

try:
    from PyQt5.QtWidgets import *
    from PyQt5.QtGui import *
    from PyQt5.QtCore import *

    Signal = pyqtSignal
except ImportError:
    from PySide2.QtWidgets import *
    from PySide2.QtGui import *
    from PySide2.QtCore import *
from lxml import etree

import hou

from .notification import notify


class UserDataItem:
    def __init__(self, key, data, cached):
        self.key = key
        self.data = data
        self.cached = cached


class UserDataModel(QAbstractListModel):
    DEFAULT_ICON = hou.qt.Icon('DATATYPES_file', 16, 16)
    CACHED_DATA_ICON = hou.qt.Icon('NETVIEW_time_dependent_badge', 16, 16)

    def __init__(self, parent=None):
        super(UserDataModel, self).__init__(parent)

        # Data
        self.__data = []

    def updateDataFromNode(self, node):
        self.beginResetModel()
        self.__data = []
        if node is not None:
            for key, data in node.userDataDict().items():
                self.__data.append(UserDataItem(key, data, False))

            for key, data in node.cachedUserDataDict().items():
                self.__data.append(UserDataItem(key, data, True))
        self.endResetModel()

    def indexByKey(self, key):
        for index, data in enumerate(self.__data):
            if data.key == key:
                return self.index(index, 0)

        return QModelIndex()

    def rowCount(self, parent):
        return len(self.__data)

    def data(self, index, role):
        item = self.__data[index.row()]
        if role == Qt.DisplayRole:
            return item.key
        elif role == Qt.DecorationRole:
            if item.cached:
                return UserDataModel.CACHED_DATA_ICON
            else:
                return UserDataModel.DEFAULT_ICON
        elif role == Qt.UserRole:
            return item.data


class UserDataListView(QListView):
    def __init__(self):
        super(UserDataListView, self).__init__()

        self.setAlternatingRowColors(True)


def prettify(text):
    # JSON
    try:
        data = json.loads(text)
        return json.dumps(data, indent=4)
    except ValueError:
        pass

    # XML
    try:
        parser = etree.XMLParser(remove_blank_text=True, resolve_entities=False, strip_cdata=False)
        data = etree.XML(text, parser)
        data = etree.tostring(data, encoding='unicode', pretty_print=True)
        return data
    except etree.XMLSyntaxError:
        pass

    # INI-like
    # \n allowed, delimiter is ;
    ini_regex = re.compile(r'([\w\d\"]+)[\s\n]*:?=[\s\n]*([\w\d\S]+)\n*;')
    if ini_regex.match(text):
        return '\n'.join(r.expand(r'\1 = \2;') for r in ini_regex.finditer(text))

    # \n is delimiter
    ini_wo_semicolon_regex = re.compile(r'([\w\d\"]+)[\s]*:?=[\s]*([\w\d\S]+)(?:\n|$)')
    if ini_wo_semicolon_regex.match(text):
        return '\n'.join(r.expand(r'\1 = \2') for r in ini_wo_semicolon_regex.finditer(text))

    # Comma-separated sequence
    comma_separated_seq_regex = re.compile(r'(?:\s*)(.+?)(?:\s*)(?:,|$)')
    if comma_separated_seq_regex.match(text):
        return ',\n'.join(comma_separated_seq_regex.findall(text))

    return text


class UserDataWindow(QWidget):
    def __init__(self, parent=None):
        super(UserDataWindow, self).__init__(parent, Qt.Window)

        # Data
        self._current_key = None
        self._node = None

        # State
        self._pinned = True
        self._auto_update = True
        self._word_wrap = True
        self._prettify = False

        # Window
        self.updateWindowTitle()
        self.setWindowIcon(hou.qt.Icon('TOP_jsondata', 32, 32))

        # Layout
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(4)

        splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(splitter)

        # Key List
        self.user_data_model = UserDataModel()

        self.user_data_list = UserDataListView()
        self.user_data_list.setModel(self.user_data_model)
        self.user_data_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        selection_model = self.user_data_list.selectionModel()
        selection_model.currentChanged.connect(self._readData)
        splitter.addWidget(self.user_data_list)

        # Data View
        self.user_data_view = QTextBrowser()
        self.user_data_view.setOpenLinks(True)
        self.user_data_view.setOpenExternalLinks(True)
        self.user_data_view.setPlaceholderText('Key has no data')
        self.user_data_view.installEventFilter(self)
        splitter.addWidget(self.user_data_view)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)

        # Update
        options_layout = QVBoxLayout()
        options_layout.setContentsMargins(0, 0, 0, 0)
        options_layout.setSpacing(4)
        main_layout.addLayout(options_layout)

        self.update_timer = QTimer(self)
        self.update_timer.setInterval(500)
        self.update_timer.timeout.connect(self.updateData)

        self.pin_toggle = QPushButton()
        self.pin_toggle.setCheckable(True)
        self.pin_toggle.setChecked(True)
        self.pin_toggle.setToolTip('Pin to node')
        self.pin_toggle.setFixedSize(24, 24)
        self.pin_toggle.setIcon(hou.qt.Icon('BUTTONS_pinned', 16, 16))
        options_layout.addWidget(self.pin_toggle)

        self.auto_update_toggle = QPushButton()
        self.auto_update_toggle.setCheckable(True)
        self.auto_update_toggle.setChecked(True)
        self.auto_update_toggle.setToolTip('Auto Update')
        self.auto_update_toggle.setFixedSize(24, 24)
        self.auto_update_toggle.setIcon(hou.qt.Icon('NETVIEW_reload', 16, 16))
        self.auto_update_toggle.toggled.connect(self.__toggleUpdateTimer)
        options_layout.addWidget(self.auto_update_toggle)

        self.word_wrap_toggle = QPushButton()
        self.word_wrap_toggle.setCheckable(True)
        self.word_wrap_toggle.setChecked(True)
        self.word_wrap_toggle.setToolTip('Word Wrap')
        self.word_wrap_toggle.setFixedSize(24, 24)
        self.word_wrap_toggle.setIcon(hou.qt.Icon('BUTTONS_decrease_indent', 16, 16))
        self.word_wrap_toggle.toggled.connect(self.setWordWrapEnabled)
        options_layout.addWidget(self.word_wrap_toggle)

        self.prettify_toggle = QPushButton()
        self.prettify_toggle.setCheckable(True)
        self.prettify_toggle.setChecked(False)
        self.prettify_toggle.setToolTip('Prettify')
        self.prettify_toggle.setFixedSize(24, 24)
        self.prettify_toggle.setIcon(hou.qt.Icon('TOP_jsondata', 16, 16))
        self.prettify_toggle.toggled.connect(self.setPrettifyEnabled)
        options_layout.addWidget(self.prettify_toggle)

        options_layout.addSpacerItem(QSpacerItem(0, 0, QSizePolicy.Preferred, QSizePolicy.Expanding))

    def updateWindowTitle(self):
        title = 'TDK: Node User Data'
        if self._node:
            title += ': ' + self._node.path()
        self.setWindowTitle(title)

    def setWordWrapEnabled(self, enable):
        if enable:
            self.user_data_view.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        else:
            self.user_data_view.setWordWrapMode(QTextOption.NoWrap)

    def setPrettifyEnabled(self, enable):
        self._prettify = enable
        self.updateData()

    def _readData(self):
        selection_model = self.user_data_list.selectionModel()
        index = selection_model.currentIndex()

        if not index.isValid():
            return

        self._current_key = index.data(Qt.DisplayRole)
        data = index.data(Qt.UserRole)

        if self._prettify:
            data = prettify(data)

        self.user_data_view.setText(data)

    def updateData(self):
        if self._node:
            self.user_data_model.updateDataFromNode(self._node)

        new_index = self.user_data_model.indexByKey(self._current_key)
        if new_index.isValid():
            self.user_data_list.setCurrentIndex(new_index)

    def setCurrentNode(self, node):
        self._node = node
        self.updateData()
        self.update_timer.start()
        self.updateWindowTitle()

    def __toggleUpdateTimer(self):
        if self.update_timer.isActive():
            self.update_timer.stop()
        else:
            self.update_timer.start()

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.Refresh):
            self.updateData()
        else:
            super(UserDataWindow, self).keyPressEvent(event)

    def eventFilter(self, watched, event):
        if watched == self.user_data_view and event.type() == QEvent.Wheel:
            if event.modifiers() == Qt.ControlModifier:
                if event.delta() > 0:
                    self.user_data_view.zoomIn(2)
                else:
                    self.user_data_view.zoomOut(2)
                return True
        return False

    def __del__(self):
        self.update_timer.stop()

    def hideEvent(self, event):
        self.update_timer.stop()
        super(UserDataWindow, self).hideEvent(event)


def showNodeUserData(node=None, **kwargs):
    if node is None:
        nodes = hou.selectedNodes()
        if not nodes:
            notify('No node selected', hou.severityType.Error)
            return
        elif len(nodes) > 1:
            notify('Too much nodes selected', hou.severityType.Error)
            return
        node = nodes[0]

    window = UserDataWindow(hou.qt.mainWindow())
    window.setCurrentNode(node)
    window.show()
