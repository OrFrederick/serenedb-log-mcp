"""Run Brain (logparser3 1.0.4, Apache-2.0) in memory on a list of contents.
Settings are the ones from logparser/Brain/benchmark.py for BGL / OpenStack."""
import pandas as pd
from logparser.Brain.Brain import LogParser

SETTINGS = {
    "bgl": {"regex": [r"core\.\d+"], "threshold": 6},
    "openstack": {"regex": [r"((\d+\.){3}\d+,?)+", r"/.+?\s ", r"\d+"], "threshold": 5},
}


class _InMemory(LogParser):
    def __init__(self, contents, ds):
        s = SETTINGS[ds]
        super().__init__(ds, "<Content>", threshold=s["threshold"], rex=s["regex"])
        self._contents = contents
        self.result = None

    def load_data(self):
        self.df_log = pd.DataFrame({"Content": self._contents})

    def generateresult(self, template_set, sentences):
        out = [None] * len(sentences)
        for k, idxs in template_set.items():
            t = " ".join(k)
            for i in idxs:
                out[i] = t
        self.result = out


def brain_parse(contents, ds):
    p = _InMemory(contents, ds)
    p.parse("in-memory")
    return p.result
