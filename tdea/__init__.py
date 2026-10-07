"""Technical Debt Evolution Evaluator (TDEA).

Five layers, each importing only the layer directly below it (NFR-14):
api -> aggregator -> analyzer -> git_handler. The dashboard layer is the
React single-page application and talks to the api layer over HTTP.
"""

__version__ = "0.1.0"
