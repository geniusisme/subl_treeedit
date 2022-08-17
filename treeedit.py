import sublime
import sublime_plugin
import os.path
from pathlib import Path
from enum import Enum

trees = []

def view_by_id(view_id, window):
    for view in window.views():
        if view.id() == view_id:
            return view
    return None

def tree_by_view(view):
    try:
        return next((tree for tree in trees if
            tree.window_id == view.window().id() and tree.view_id == view.id()))
    except StopIteration:
        return None

def prune_closed(window):
    global trees
    trees = [tree
        for tree in trees if tree.window_id != window.id() or view_by_id(tree.view_id, window) != None
    ]

def relative_part(of, to):
    try:
        return of.relative_to(to)
    except ValueError:
        return None

def find(gen, fun):
    try:
        return next(filter(fun, gen))
    except StopIteration:
        return None

def indexed(gen):
    index = 0
    for thing in gen:
        yield (index, thing)
        index = index + 1

class Tree:
    def __init__(self, view_id, window_id, root):
        self.view_id = view_id
        self.window_id = window_id
        self.root = root

EntryType = Enum('EntryType', 'File, DirOpened, DirClosed')

class Entry:
    def __init__(self, path, include_children = False):
        self.path = path
        try:
            if path.is_dir():
                self.type = EntryType.DirClosed
            else:
                self.type = EntryType.File
        except PermissionError:
            self.type = EntryType.File
            return
        if path.is_dir() and include_children:
            self.children = list(map(
                lambda p: Entry(p),
                sorted(path.iterdir(), key = lambda p: p.name)
            ))
        else: self.children = None

    def refresh(self):
        if self.type != EntryType.DirOpened:
            return
        paths = sorted(self.path.iterdir(), key = lambda p: p.name)
        if self.children == None:
            self.children = list(map(lambda p: Entry(p), paths))
            return
        child_idx = 0
        new_children = []
        for path in paths:
            while child_idx < len(self.children) and self.children[child_idx].path.name != path.name:
                child_idx = child_idx + 1
            if child_idx < len(self.children):
                new_children.append(self.children[child_idx])
            else:
                new_children.append(Entry(path))
        self.children = new_children

    def make_parent(self):
        parent = Entry(self.path.parent, include_children = True)
        needle = find(parent.children, lambda child: child.path.name == self.path.name)
        needle.children = self.children
        needle.type = self.type
        return parent

def paths_df(entry):
    for child in entry.children:
        yield child.path
        if child.type == EntryType.DirOpened:
            for path in paths_df(child):
                yield path

def entries_df(entry):
    for child in entry.children:
        yield child
        if child.type == EntryType.DirOpened:
            for entry in entries_df(child):
                yield entry

def stack_entries_df(entry):
    stack = [(-1, entry)]
    yield stack
    for stack in stack_entries_df_recursive(entry, stack, 0):
        yield stack

def stack_entries_df_recursive(entry, stack, count_so_far):
    for child in entry.children:
        stack.append((count_so_far, child))
        yield stack
        count_so_far = count_so_far + 1
        if child.type == EntryType.DirOpened:
            for stack in stack_entries_df_recursive(child, stack, count_so_far):
                yield stack
                count_so_far = count_so_far + 1
        stack.pop()

class TreeeditShowCurrentCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        self.view.window().run_command("treeedit_show_file", {"file": self.view.file_name()})

    def is_enabled(self):
        return self.view.file_name() != None

class TreeeditOpenFileCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        files = [self.view.rowcol(file.a)[0] for sel in self.view.sel() for file in self.view.lines(sel)]
        if len(files) == 0:
            print("treeedit: no cursor in view")
            return
        elif files[0] == 0:
            print('treeedit: no action on first line')
        elif files[0] == 1:
            if len(files) > 1:
                print("treeedit: to go up a level please have only one cursor")
                return
            tree = tree_by_view(self.view)
            old_path = tree.root.path
            tree.root = tree.root.make_parent()
            self.view.run_command("treeedit_sync_tree")
            self.view.run_command('treeedit_select_file', {"file": old_path.as_posix()})
            return
        entries = list(entries_df(tree_by_view(self.view).root))
        entries = [entries[file - 2] for file in files]
        try:
            if all(map(lambda p: p.path.is_file(), entries)):
                for entry in entries:
                    # todo: settings for group selection
                    in_group = self.view.window().active_group()
                    if in_group < self.view.window().num_groups() - 1:
                        in_group = in_group + 1
                    elif in_group > 0:
                        in_group = in_group - 1
                    try:
                        self.view.window().open_file(entry.path.as_posix(), group = in_group)
                    except Error as err:
                        print("file: {} could not be open due to: {}".format(entry.path, str(err)))
            elif all(map(lambda p: p.path.is_dir(), entries)):
                for entry in entries:
                    if entry.type == EntryType.DirClosed:
                        entry.type = EntryType.DirOpened
                        entry.refresh()
                    elif entry.type == EntryType.DirOpened:
                        entry.type = EntryType.DirClosed
                        # #todo: put cursors on folders'
                self.view.run_command("treeedit_sync_tree")
            else: print("treeedit: to avoid confusion, opening both files and folders at once is not supported")
        except PermissionError as err:
            sublime.error_message(str(err))

class TreeeditSelectFileCommand(sublime_plugin.TextCommand):
    def run(self, edit, file):
        root = tree_by_view(self.view).root.path
        root_entry = tree_by_view(self.view).root
        path = Path(file).relative_to(root_entry.path)
        start_point = 0
        depth = 0
        need_sync = False
        def find_child(entry, part):
            return find(entry.children, lambda e: e.path.name == part)
        entry = root_entry
        for part in path.parts:
            if entry.type == EntryType.DirClosed:
                entry.type = EntryType.DirOpened
                need_sync = True
            if entry.type == EntryType.DirOpened:
                if entry.children == None or find_child(entry, part) == None:
                    entry.type = EntryType.DirOpened
                    entry.refresh()
                    need_sync = True
                names = list(map(lambda e: e.path.name, entry.children))
                entry = find_child(entry, part)
                if entry == None:
                    raise Exception('file is not part of the tree: {} {}'.format(file, part))
        if need_sync:
            self.view.run_command("treeedit_sync_tree")
        entry = root_entry
        for part in path.parts:
            root = root / part
            entry = find_child(entry, part)
            rendered_part = render_entry(entry, depth)
            needle = self.view.find("^" + rendered_part + "$", start_point)
            if needle == sublime.Region(-1, -1):
                raise Exception("Requested path does not exist {} in {}".format(rendered_part, needle))
            else:
                depth += 1
                start_point = self.view.full_line(needle).b
        self.view.sel().clear()
        self.view.sel().add(self.view.line(start_point - 1))
        self.view.show_at_center(start_point)

class TreeeditSyncTreeCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        self.view.set_read_only(False)
        sel = self.view.sel()[0]
        self.view.erase(edit, sublime.Region(0, self.view.size()))
        tree = tree_by_view(self.view)
        if tree == None:
            self.view.insert("We had troubles syncing with the file system")
        else:
            # for now we just render anew
            root = tree_by_view(self.view).root
            self.view.set_name(root.path.name)
            self.view.insert(edit, 0, root.path.as_posix() + '\n')
            self.view.insert(edit, self.view.size(), "..\n")
            self.render_children(root.children, 0, edit)
            self.view.sel().clear()
            self.view.sel().add(sel)
            self.view.show(sel, False, False, False)
        self.view.set_read_only(True)

    def render_children(self, children, depth, edit):
        for child in children:
            self.view.insert(edit, self.view.size(), render_entry(child, depth) + "\n")
            if child.type == EntryType.DirOpened:
                self.render_children(child.children, depth + 1, edit)

def render_entry(entry, depth):
    if entry.type == EntryType.DirClosed:
        suffix = " ▶"
    elif entry.type == EntryType.DirOpened:
        suffix = " ▼"
    else: suffix = ""
    return " " * depth * 4 + entry.path.name + suffix

class TreeeditShowFileCommand(sublime_plugin.WindowCommand):

    def run(self, file):
        tree_view = self.view_for_path(Path(file))

        tree_view.run_command("treeedit_select_file", {"file": file})
        self.window.focus_view(tree_view)

    def view_for_path(self, path):
        prune_closed(self.window)
        global trees
        tree = find(trees, lambda t: (relative_part(path, t.root.path) != None) and (t.window_id == self.window.id()) )
        if tree == None:
            view = self.make_view()
            tree = self.make_and_insert_tree(path, view)
            view.run_command("treeedit_sync_tree")
        else:
            view = view_by_id(tree.view_id, self.window)
        return view

    def make_view(self):
        new_view = self.window.new_file()
        if self.window.num_groups() > 1:
            idx = self.window.active_group() + 1
            if idx == self.window.num_groups(): idx = 0
            self.window.set_view_index(new_view, idx, 0)
        new_view.set_scratch(True)
        new_view.assign_syntax("scope:treeedit")
        new_view.set_read_only(True)
        return new_view

    def make_and_insert_tree(self, path, view):
        common_root = find(
            (folder for folder in self.window.folders()),
            lambda folder: relative_part(path, folder) != None
        )
        if common_root == None:
            new_root = path.parent
        else:
            new_root = Path(common_root)
        root_entry = Entry(new_root, include_children = True)
        tree = Tree(view.id(), self.window.id(), root_entry)
        global trees
        trees.append(tree)
        return tree

def plugin_loaded():
    views = (view for window in sublime.windows() for view in window.views())
    views = filter(lambda v: v.syntax() != None, views)
    views = filter(lambda v: v.syntax().name == 'treeedit', views)
    local_trees = map(restore_tree, views)
    local_trees = filter(lambda t: t != None, local_trees)
    global trees
    trees = list(local_trees)
    for view in views:
        view.run_command("treeedit_sync_tree")


class OpenFolder:
    def __init__(self, name, next_pos):
        self.name = name
        self.next_pos = next_pos

class IndentationSentinel:
    def __init__(self, next_pos):
        self.next_pos = next_pos

class EOF:
    def __init__(self, next_pos):
        self.next_pos = next_pos

def look_for_open_folder(view, depth, from_pos):
    one_identation = ' ' * 4
    full_identation = one_identation * depth
    next_pos = from_pos
    while True:
        line_region = view.find('^.*\n', next_pos)
        if line_region.a == -1:
            return EOF(next_pos)
        line = view.substr(line_region)
        start_pos = line.find(full_identation)
        if start_pos < 0:
            return IndentationSentinel(next_pos)
        next_pos = next_pos + len(line)
        end_pos = line.find(' ▼')
        if end_pos > 0:
            return OpenFolder(line[start_pos + depth * 4: end_pos], next_pos)


def restore_tree(view):
    path = Path(view.substr(view.find('^.*$', 0)))

    if not path.is_dir():
        return None
    entry = Entry(path, include_children = True)
    start_pos = view.text_point(2, 0)
    depth = 0
    expand_entry(view, entry, depth, start_pos)
    return Tree(view.id(), view.window().id(), entry)

def expand_entry(view, entry, depth, next_pos):
    entry.type = EntryType.DirOpened
    while True:
        result = look_for_open_folder(view, depth, next_pos)
        if isinstance(result, OpenFolder):
            child = find(enumerate(entry.children), lambda child: child[1].path.name == result.name)
            if child != None:
                next_pos = result.next_pos
                new_entry = Entry(child[1].path, include_children = True)
                next_pos = expand_entry(view, new_entry, depth + 1, next_pos)
                entry.children[child[0]] = new_entry
            else:
                while isinstance(result, OpenFolder):
                    result = look_for_open_folder(view, depth + 1, result.next_pos)
                next_pos = result.next_pos
        else:
            return result.next_pos
    return next_pos


class TreeeditJumpUpFolderCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        files = [self.view.rowcol(file.a)[0] for sel in self.view.sel() for file in self.view.lines(sel)]
        vis_start = self.view.rowcol(self.view.visible_region().a)[0]
        vis_end = self.view.rowcol(self.view.visible_region().b)[0]
        show_line = find(files, lambda line: vis_start <= line and line <= vis_end)
        if len(files) == 0:
            print("treeedit: no cursor in view")
            return
        elif files[0] < 2:
            print("treeedit: to go up a level, use treeedit_open_file command or bound key")
            return
        root = tree_by_view(self.view).root
        parent_lines = []
        file_line = 0
        for stack in stack_entries_df(root):
            line = stack[-1][0] + 2
            if line == files[file_line]:
                # #todo: find indentation and move cursor there?
                parent_line = stack[-2][0] + 2
                parent_lines.append(parent_line)
                file_line = file_line + 1
                if line == show_line:
                    self.view.show(self.view.text_point(parent_line, 0), False)
                if file_line == len(files):
                    break

        self.view.sel().clear()
        for line in parent_lines:
            start = self.view.text_point(line, 0)
            self.view.sel().add(sublime.Region(start, start))

