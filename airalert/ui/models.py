"""List models exposed to QML."""
from PySide6.QtCore import (QAbstractListModel, QByteArray, QModelIndex, QSortFilterProxyModel, Qt,
                            Property, Signal, Slot)


class RowModel(QAbstractListModel):
    """Rows of dicts with a fixed set of role names.

    update_rows() applies keyed inserts/removals/changes so views keep their
    scroll position and selection while data refreshes every second.
    """
    countChanged = Signal()

    def __init__(self, roles, key='key', parent=None):
        super().__init__(parent)
        self.role_names = list(roles)
        if key not in self.role_names:
            self.role_names.insert(0, key)
        self.key = key
        self.rows = []
        self._roles = {Qt.ItemDataRole.UserRole + 1 + i: name for i, name in enumerate(self.role_names)}
        self._role_ids = {name: rid for rid, name in self._roles.items()}

    def roleNames(self):
        return {rid: QByteArray(name.encode()) for rid, name in self._roles.items()}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.rows):
            return None
        name = self._roles.get(role)
        if name is None:
            return None
        return self.rows[index.row()].get(name)

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = [dict(r) for r in rows]
        self.endResetModel()
        self.countChanged.emit()

    def update_rows(self, rows):
        new = {r[self.key]: r for r in rows}
        # Remove rows that disappeared (from the end so indices stay valid).
        for i in range(len(self.rows) - 1, -1, -1):
            if self.rows[i][self.key] not in new:
                self.beginRemoveRows(QModelIndex(), i, i)
                del self.rows[i]
                self.endRemoveRows()
        existing = {r[self.key]: i for i, r in enumerate(self.rows)}
        for key, row in new.items():
            i = existing.get(key)
            if i is None:
                continue
            if self.rows[i] != row:
                changed = [self._role_ids[k] for k in self.role_names if self.rows[i].get(k) != row.get(k)]
                self.rows[i] = dict(row)
                idx = self.index(i)
                self.dataChanged.emit(idx, idx, changed)
        additions = [r for k, r in new.items() if k not in existing]
        if additions:
            start = len(self.rows)
            self.beginInsertRows(QModelIndex(), start, start + len(additions) - 1)
            self.rows.extend(dict(r) for r in additions)
            self.endInsertRows()
        if additions or len(self.rows) != len(existing):
            self.countChanged.emit()

    def sync_rows(self, rows):
        """Make the rows equal `rows`, in their order, through keyed removals, inserts and in-place changes.

        Unlike set_rows (a model reset, which scrolls views back to the top) views keep their scroll
        position. Falls back to a reset when surviving rows changed order or keys repeat."""
        rows = [dict(r) for r in rows]
        keys = [r[self.key] for r in rows]
        if len(set(keys)) != len(keys):
            self.set_rows(rows)
            return
        before = len(self.rows)
        wanted = set(keys)
        for i in range(len(self.rows) - 1, -1, -1):
            if self.rows[i][self.key] not in wanted:
                self.beginRemoveRows(QModelIndex(), i, i)
                del self.rows[i]
                self.endRemoveRows()
        kept = [r[self.key] for r in self.rows]
        present = set(kept)
        if kept != [k for k in keys if k in present]:
            self.set_rows(rows)
            return
        for i, row in enumerate(rows):
            if i < len(self.rows) and self.rows[i][self.key] == row[self.key]:
                if self.rows[i] != row:
                    changed = [self._role_ids[k] for k in self.role_names if self.rows[i].get(k) != row.get(k)]
                    self.rows[i] = row
                    idx = self.index(i)
                    self.dataChanged.emit(idx, idx, changed)
            else:  # kept rows are in order, so a mismatch here is a new row
                self.beginInsertRows(QModelIndex(), i, i)
                self.rows.insert(i, row)
                self.endInsertRows()
        if len(self.rows) != before:
            self.countChanged.emit()

    def _count(self):
        return len(self.rows)

    count = Property(int, _count, notify=countChanged)

    @Slot(int, result='QVariantMap')
    def get(self, row):
        return dict(self.rows[row]) if 0 <= row < len(self.rows) else {}


class SortProxy(QSortFilterProxyModel):
    """Sorts by any role; missing values always sort last in either direction."""
    sortChanged = Signal()

    def __init__(self, source, sort_key, ascending=True, parent=None):
        super().__init__(parent)
        self.setSourceModel(source)
        self._key = sort_key
        self._ascending = ascending
        self.setDynamicSortFilter(True)
        self.sort(0, Qt.SortOrder.AscendingOrder)
        source.countChanged.connect(self.countChanged)

    countChanged = Signal()

    def lessThan(self, left, right):
        rows = self.sourceModel().rows
        a = rows[left.row()].get(self._key)
        b = rows[right.row()].get(self._key)
        if a is None or b is None or a == '' or b == '':
            if (a is None or a == '') == (b is None or b == ''):
                return False
            return b is None or b == ''
        if isinstance(a, str) or isinstance(b, str):
            a, b = str(a).casefold(), str(b).casefold()
        if isinstance(a, bool) or isinstance(b, bool):
            a, b = int(a), int(b)
        return a < b if self._ascending else b < a

    @Slot(str)
    def sortBy(self, key):
        """Toggle direction when the same key is chosen again."""
        if key == self._key:
            self._ascending = not self._ascending
        else:
            self._key = key
            self._ascending = key not in ('lastSeen', 'last', 'first', 'time', 'sightings', 'altitude', 'speed')
        self.invalidate()
        self.sort(0, Qt.SortOrder.AscendingOrder)
        self.sortChanged.emit()

    def _sort_key(self):
        return self._key

    def _is_ascending(self):
        return self._ascending

    sortKey = Property(str, _sort_key, notify=sortChanged)
    ascending = Property(bool, _is_ascending, notify=sortChanged)

    def _count(self):
        return self.rowCount()

    count = Property(int, _count, notify=countChanged)

    @Slot(int, result='QVariantMap')
    def get(self, row):
        source = self.mapToSource(self.index(row, 0))
        return self.sourceModel().get(source.row()) if source.isValid() else {}

    @Slot(str, result=int)
    def rowOfKey(self, key):
        model = self.sourceModel()
        for i, r in enumerate(model.rows):
            if r[model.key] == key:
                return self.mapFromSource(model.index(i)).row()
        return -1
