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
    return next((tree for tree in trees if
        tree.window_id == view.window().id() and tree.view_id == view.id()))

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

class Tree:
    def __init__(self, view_id, window_id, root):
        self.view_id = view_id
        self.window_id = window_id
        self.root = root

EntryType = Enum('EntryType', "File, DirOpened, DirClosed")

class Entry:
    def __init__(self, path, include_children = False):
        self.path = path
        if path.is_dir():
            self.type = EntryType.DirClosed
        else:
            self.type = EntryType.File
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
        needle.type = EntryType.DirOpened
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
    stack = [(0, entry)]
    yield stack
    for stack in stack_entries_df_recursive(entry, stack, 1):
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
            if len(files) > 1:
                print("treeedit: to go up a level, only have cursor on first line (..)")
                return
            tree = tree_by_view(self.view)
            tree.root = tree.root.make_parent()
            self.view.run_command("treeedit_sync_tree")
            return
        entries = list(entries_df(tree_by_view(self.view).root))
        entries = [entries[file - 1] for file in files]
        if all(map(lambda p: p.path.is_file(), entries)):
            for entry in entries:
                self.view.window().open_file(entry.path.as_posix())
        elif all(map(lambda p: p.path.is_dir(), entries)):
            for entry in entries:
                if entry.type == EntryType.DirClosed:
                    entry.type = EntryType.DirOpened
                    entry.refresh()
                elif entry.type == EntryType.DirOpened:
                    entry.type = EntryType.DirClosed
                self.view.run_command("treeedit_sync_tree")
        else: print("treeedit: to avoid confusion, opening both files and folders at once is not supported")

class TreeeditRenderFileCommand(sublime_plugin.TextCommand):
    def run(self, edit, file):
        root = tree_by_view(self.view).root.path
        path = Path(file).relative_to(root)
        start_point = 0
        depth = 0
        for part in path.parts:
            root = root / part
            entry = Entry(root)
            if entry.type == EntryType.DirClosed:
                entry.type = EntryType.DirOpened
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
        # for now we just render anew
        self.view.set_read_only(False)
        sel = self.view.sel()[0]
        self.view.erase(edit, sublime.Region(0, self.view.size()))
        root = tree_by_view(self.view).root
        self.view.set_name(root.path.name)
        self.view.insert(edit, 0, "..\n")
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

        tree_view.run_command("treeedit_render_file", {"file": file})
        self.window.focus_view(tree_view)

    def view_for_path(self, path):
        prune_closed(self.window)
        global trees
        tree = find(trees, lambda t: relative_part(path, t.root.path) != None)
        if tree == None:
            view = self.make_view()
            tree = self.make_and_insert_tree(path, view)
        else:
            view = view_by_id(tree.view_id, self.window)
        need_sync = False
        root = tree.root
        for part in relative_part(path, tree.root.path).parts:
            if root.children == None or find(root.children, lambda e: e.path.name == part) == None:
                root.type = EntryType.DirOpened
                root.refresh()
                need_sync = True
            root = find(root.children, lambda e: e.path.name == part)
        if need_sync:
            view.run_command("treeedit_sync_tree")
        return view

    def make_view(self):
        new_view = self.window.new_file()
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
        root_entry = Entry(new_root)
        tree = Tree(view.id(), self.window.id(), root_entry)
        global trees
        trees.append(tree)
        return tree

class TreeeditJumpUpFolderCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        files = [self.view.rowcol(file.a)[0] for sel in self.view.sel() for file in self.view.lines(sel)]
        vis_start = self.view.rowcol(self.view.visible_region().a)[0]
        vis_end = self.view.rowcol(self.view.visible_region().b)[0]
        show_line = find(files, lambda line: vis_start <= line and line <= vis_end)
        if len(files) == 0:
            print("treeedit: no cursor in view")
            return
        elif files[0] == 0:
            print("treeedit: to go up a level, use treeedit_open_file command or bound key")
            return
        root = tree_by_view(self.view).root
        parent_lines = []
        file_line = 0
        for line, stack in enumerate(stack_entries_df(root)):
            if line == files[file_line]:
                # #todo: find indentation and move cursor there?
                parent_line = stack[-2][0]
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

