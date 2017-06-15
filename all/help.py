import sublime
import sublime_plugin

import re
import webbrowser

from .output_view import find_view, output_to_view

from .operations import log
from .operations import HelpData, load_index_json
from .operations import package_help_scan

###----------------------------------------------------------------------------


def focus_on_position(view, position):
    """
    Focus the provided view on the given position. Includes some hacks to work
    around a bug in sublime that can sometimes cause it to not properly update
    the window visually.
    """
    position = sublime.Region(position.end(), position.begin())

    view.show_at_center(position)
    view.sel().clear()
    view.sel().add(position)

    # Hack to make the view update properly. See:
    #    https://github.com/SublimeTextIssues/Core/issues/485
    view.add_regions("_hh_rk", [], "", "", sublime.HIDDEN)
    view.erase_regions("_hh_rk")


###----------------------------------------------------------------------------


class HyperHelpCommand(sublime_plugin.ApplicationCommand):
    """
    This command is the core of the help system, and can open a view, follow a
    link target to a new location, or display topic lists.
    """
    def __init__(self):
        self._url_re = re.compile("^(https?|file)://")
        self._help_list = dict()

    def help_content(self, pkg_info, help_file):
        filename = "Packages/%s/doc/%s" % (pkg_info.package, help_file)
        try:
            return sublime.load_resource(filename)
        except:
            pass

        return None

    def show_file(self, pkg_info, help_file):
        window = sublime.active_window()
        view = find_view(window, "HyperHelp")

        if view is not None:
            window.focus_view(view)
            current_pkg = view.settings().get("_hh_package", None)
            current_file = view.settings().get("_hh_file", None)

            if help_file == current_file and pkg_info.package == current_pkg:
                return view

        help_text = self.help_content(pkg_info, help_file)
        if help_text is not None:
            view = output_to_view(window,
                                  "HyperHelp",
                                  help_text,
                                  syntax="Packages/hyperhelp/all/Help.sublime-syntax")
            view.settings().set("_hh_file", help_file)
            view.settings().set("_hh_package", pkg_info.package)
            return view

        return None

    def show_topic(self, pkg_info, topic):
        help_file = pkg_info.topics.get(topic, {}).get("file", None)
        if help_file is None:
            return log("Unknown help topic '%s'", topic, status=True)

        if self._url_re.match(help_file):
            return webbrowser.open_new_tab(help_file)

        if help_file.startswith("Packages/"):
            help_file = help_file.replace("Packages/", "${packages}/")
            window = sublime.active_window()
            return window.run_command("open_file", {"file": help_file})

        help_view = self.show_file(pkg_info, help_file)
        if help_view is None:
            return log("Unable to load help file '%s'", help_file, status=True)

        for pos in help_view.find_by_selector('meta.link-target'):
            target = help_view.substr(pos)
            if target == topic:
                return focus_on_position(help_view, pos)

        log("Unable to find topic '%s' in help file '%s'", topic, help_file,
            status=True)

    def select_toc_item(self, pkg_info, items, stack, index):
        if index >= 0:
            # When stack is not empty, first item takes us back
            if index == 0 and len(stack) > 0:
                items = stack.pop()
                return self.show_toc(pkg_info, items, stack)

            # Compensate for the ".." entry on a non-empty stack
            if len(stack) > 0:
                index -= 1

            entry = items[index]
            children = entry.get("children", None)

            if children is not None:
                stack.append(items)
                return self.show_toc(pkg_info, children, stack)

            self.show_topic(pkg_info, entry["topic"])

    def show_toc(self, pkg_info, items, stack):
        captions = [[item["caption"], item["topic"] +
            (" ({} topics)".format(len(item["children"])) if "children" in item else "")]
            for item in items]

        if len(captions) == 0 and len(stack) == 0:
            return log("No table of contents defined for %s", pkg_info.package,
                       status=True)

        if len(stack) > 0:
            captions.insert(0, ["..", "Go back"])

        sublime.active_window().show_quick_panel(
            captions,
            on_select=lambda index: self.select_toc_item(pkg_info, items, stack, index))

    def select_package_item(self, pkg_list, index):
        if index >= 0:
            self.run(pkg_list[index][0], True)

    def select_package(self):
        if len(self._help_list) <= 1:
            return log("No packages with help are currently installed", status=True)

        pkg_list = sorted([key for key in self._help_list if key != "__scanned"])
        captions = [[self._help_list[key].package,
                     self._help_list[key].description]
            for key in pkg_list]

        sublime.active_window().show_quick_panel(
            captions,
            on_select=lambda index: self.select_package_item(captions, index))

    def run(self, package=None, toc=False, topic=None):
        if "__scanned" not in self._help_list:
            package_help_scan(self._help_list)

        # When there are no arguments at all, respond by showing all available
        # help packages for the user to select from.
        if package is None and topic is None and toc == False:
            return self.select_package()

        # When there is no package, try to get it from the help file in the
        # current window; if there is no help yet, select the help package
        # instead.
        if package is None:
            view = find_view(sublime.active_window(), "HyperHelp")
            if view is not None:
                package = view.settings().get("_hh_package")
            else:
                return self.select_package()

        # Get the help information for the selected package; if there is none
        # we can't display any help.
        pkg_info = self._help_list.get(package, None)
        if pkg_info is None:
            return log("No help availabie for package %s", package, status=True)

        # Display the table of contents for this help if asked to do that.
        if toc:
            return self.show_toc(pkg_info, pkg_info.toc, [])

        # We have a package and we're not displaying the toc; if there is not
        # a topic, use a default.
        topic = topic or "index.txt"

        # Show the given topic from within this help package
        self.show_topic(pkg_info, topic)

    def is_enabled(self, package=None, toc=False, topic=None):
        # Always enable unless we're told to display the TOC and:
        #   1) no package is given
        #   2) No help view is currently available to get one from
        if toc == True:
            help_view = find_view(sublime.active_window(), "HyperHelp")
            if package is None and help_view is None:
                return False

        return True


###----------------------------------------------------------------------------


class HyperHelpNavigateCommand(sublime_plugin.WindowCommand):
    """
    Perform all help navigation (with the exception of opening a new help
    topic).
    """
    def run(self, nav, prev=False):
        #TODO: Extend to allow navigation among links, targets or both
        if nav == "link":
            return self.focus_link(prev)

        if nav == "follow":
            return self.follow_link()

        log("Unknown help navigation directive '%s'", nav)

    def follow_link(self):
        topic = self.extract_topic()
        if topic is not None:
            return sublime.run_command("hyper_help", {"topic": topic})

        log("Cannot follow link; no link found under the cursor")

    def focus_link(self, prev):
        view = self.window.active_view()
        point = view.sel()[0].begin()

        targets = view.find_by_selector("meta.link | meta.link-target")
        fallback = targets[-1] if prev else targets[0]

        def pick(pos):
            other = pos.begin()
            return (point < other) if not prev else (point > other)

        for pos in reversed(targets) if prev else targets:
            if pick(pos):
                return focus_on_position(view, pos)

        focus_on_position(view, fallback)

    def extract_topic(self):
        view = self.window.active_view()
        point = view.sel()[0].begin()

        if view.match_selector(point, "text.help meta.link"):
            return view.substr(view.extract_scope(point))

        return None

###----------------------------------------------------------------------------


class HyperHelpListener(sublime_plugin.EventListener):
    def on_text_command(self, view, command, args):
        """
        Listen for double clicks in help files and, if they occur over links,
        follow the link instead of selecting the text.
        """
        if command == "drag_select" and args.get("by", None) == "words":
            event = args["event"]
            point = view.window_to_text((event["x"], event["y"]))

            if view.match_selector(point, "text.help meta.link"):
                view.window().run_command("hyper_help_navigate", {"nav": "follow"})
                return ("noop")

        return None

    def on_query_context(self, view, key, operator, operand, match_all):
        """
        Allow key bindings in help windows to detect if they are currently in
        help "authoring" mode so that it is possible to edit files without
        the bindings getting in the way.
        """
        if key != "help_author_mode":
            return None

        lhs = view.is_read_only() == False
        rhs = bool(operand)

        if operator == sublime.OP_EQUAL:
            return lhs == rhs
        elif operator == sublime.OP_NOT_EQUAL:
            return lhs != rhs

        return None



###----------------------------------------------------------------------------
