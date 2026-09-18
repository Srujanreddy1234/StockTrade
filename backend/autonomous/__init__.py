"""Autonomous trading loop: live tick monitoring, probability scoring, and
(optionally) real order execution via Groww.

Everything in this package is OFF by default in the sense that matters: no
real money moves unless GROWW_ALLOW_REAL_ORDERS=true is set explicitly. See
backend/autonomous/config.py and the root README for the full safety model.
"""
