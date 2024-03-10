import html
import json
import urllib
from threading import Lock, Thread

import sublime
import sublime_plugin

_view_to_phantom_set = {}
completion = None
lock = Lock()
settings = sublime.load_settings("OllamaAutocomplete.sublime-settings")


def get_phantom_set(view) -> sublime.PhantomSet:
    view_id = view.id()

    # create phantom set if there is no existing one
    if not _view_to_phantom_set.get(view_id):
        _view_to_phantom_set[view_id] = sublime.PhantomSet(view)

    return _view_to_phantom_set[view_id]


STOP_WORDS = {
    "TSX": ["function", "class", "module", "export"],
    "TypeScript": ["function", "class", "module", "export"],
    "Python": ["def", "class"],
}

TEMPLATE = """
    <body>
        <style>
            body {{
                color: #808080;
                font-style: italic;
            }}
        </style>
        {body}
    </body>
"""

FAMILY = {
    "deepseek": {
        "prompt": "<｜fim▁begin｜>{prefix}<｜fim▁hole｜>{suffix}<｜fim▁end｜>",
        "stop": ["<｜fim▁begin｜>", "<｜fim▁hole｜>", "<｜fim▁end｜>"],
    },
    "codellama": {
        "prompt": "<PRE> {prefix} <SUF>{suffix} <MID>",
        "stop": ["<PRE>", "<SUF>", "<MID>", "<EOT>"],
    },
}


def is_active_view(obj):
    return bool(obj and obj == sublime.active_window().active_view())


class Completion:
    def __init__(self, text, view, use_multiline):
        self.text = text.strip()
        self.view = view
        self.lines = self.text.splitlines()
        self.settings = view.settings()

        if not use_multiline:
            self.lines = self.lines[:1]
        self.active = True

    def normalize_line(self, line):
        return (
            html.escape(line)
            .replace(" ", "&nbsp;")
            .replace("\t", "&nbsp;" * self.view.settings().get("tab_size"))
        )

    def body(self):
        body = "".join([f"<div>{self.normalize_line(l)}</div>" for l in self.lines])
        return TEMPLATE.format(body=body)

    def show(self):
        if not self.text:
            return
        cursor = self.view.sel()[0]

        phantom_set = get_phantom_set(self.view)
        phantom_set.update(
            [
                sublime.Phantom(
                    sublime.Region(cursor.b, None),
                    self.body(),
                    sublime.LAYOUT_INLINE
                    if len(self.lines) == 1
                    else sublime.LAYOUT_BLOCK,
                )
            ]
        )
        self.active = True

    def insert(self, edit):
        global completion
        self.hide()
        self.view.insert(edit, self.view.sel()[0].b, "\n".join(self.lines))
        completion = None

    def hide(self):
        phantom_set = get_phantom_set(self.view)
        phantom_set.update([])


def make_async_request(view, use_multiline):
    global completion
    cursor = view.sel()[0]

    line, col = view.rowcol(cursor.a)
    a = view.text_point(0, 0)
    prefix = view.substr(sublime.Region(a, cursor.b))
    suffix = view.substr(sublime.Region(cursor.b, view.size()))

    stop = STOP_WORDS[view.syntax().name]

    model_family = FAMILY[settings.get("family", "codellama")]
    prompt = model_family["prompt"].format(prefix=prefix, suffix=suffix)
    print(prompt)
    req = urllib.request.Request(
        settings.get("url"),
        data=json.dumps(
            {
                "model": settings.get("model"),
                "prompt": prompt,
                "options": {
                    "stop": [
                        *model_family["stop"],
                        "//",
                        *stop,
                    ],
                    "temperature": 0.9,
                },
                "raw": True,
                "stream": False,
            }
        ).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    k = urllib.request.urlopen(req)

    response = json.loads(k.read().decode("utf-8"))
    lock.acquire()
    if completion:
        completion.hide()
    completion = Completion(response["response"], view, use_multiline=use_multiline)
    lock.release()
    view.run_command("ollama_show_autocomplete")


class RequestCompletionListener(sublime_plugin.EventListener):
    def on_selection_modified_async(self, view):
        if not completion or not completion.active:
            return
        completion.hide()


class OllamaInsertCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        if not completion or not completion.active:
            return
        completion.insert(edit)


class OllamaFillCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        cursor = self.view.sel()[0]
        scope = self.view.scope_name(cursor.end()).strip().split(" ")

        scope_start, scope_end = self.view.expand_to_scope(
            cursor.end(), " ".join(scope)
        )
        text_in_scope = self.view.substr(
            sublime.Region(scope_start + 1, scope_end - 1)
        ).strip()
        use_multiline = len(text_in_scope) == 0

        t = Thread(target=make_async_request, args=[self.view, use_multiline])
        t.start()


class OllamaShowAutocompleteCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        completion.show()
